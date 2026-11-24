"""Check-template API: view, validate and preview per-intent pre/post checks.

Templates are policy files reviewed in Git (policies/checks.yaml). This API
does not write them: it shows the active file, validates a draft, and
previews exactly which checks and commands a change would run, so authors
can test a template before opening a merge request.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError

from vireonet.api.security import require_role
from vireonet.checks.templates import CheckTemplates, TemplateValidationError
from vireonet.intent import parse_intent

router = APIRouter()

# One valid example intent per change type, to start a preview from.
SAMPLE_INTENTS: dict[str, dict[str, Any]] = {
    "vlan_add": {"change_type": "vlan_add", "vlan_id": 120, "vlan_name": "app-tier"},
    "vlan_remove": {"change_type": "vlan_remove", "vlan_id": 120},
    "acl_entry_add": {
        "change_type": "acl_entry_add",
        "acl_name": "EDGE-IN",
        "action": "permit",
        "protocol": "tcp",
        "source": "10.10.0.0/16",
        "destination": "10.20.5.10/32",
        "destination_port": 443,
    },
    "acl_entry_remove": {"change_type": "acl_entry_remove", "acl_name": "EDGE-IN", "sequence": 50},
    "static_route_add": {
        "change_type": "static_route_add",
        "prefix": "172.16.50.0/24",
        "next_hop": "10.0.0.1",
    },
    "static_route_remove": {"change_type": "static_route_remove", "prefix": "172.16.50.0/24"},
    "interface_set": {
        "change_type": "interface_set",
        "interface": "Ethernet1/10",
        "description": "uplink-to-core",
        "enabled": True,
    },
}
_COMMON = {
    "devices": ["leaf-01"],
    "site": "dc1",
    "ticket_number": "RITM0000001",
    "requested_by": "you",
}


class ValidateRequest(BaseModel):
    yaml: str


class PreviewRequest(BaseModel):
    intent: dict[str, Any]
    platform: str = "ios"
    # Preview a draft instead of the active file.
    yaml: str | None = None


def _active(request: Request) -> CheckTemplates:
    templates = getattr(request.app.state, "check_templates", None)
    if templates is None:
        errors = getattr(request.app.state, "check_templates_error", []) or ["not loaded"]
        raise HTTPException(status_code=409, detail={"errors": errors})
    return templates  # type: ignore[no-any-return]


@router.get("/checks/templates")
async def get_templates(
    request: Request, _role: Any = Depends(require_role("viewer"))
) -> dict[str, Any]:
    templates = getattr(request.app.state, "check_templates", None)
    errors = getattr(request.app.state, "check_templates_error", [])
    if templates is None:
        return {"valid": False, "errors": errors, "templates": None}
    return {
        "valid": True,
        "errors": [],
        "templates": templates.summary(),
        "yaml": templates.text,
    }


@router.get("/checks/sample-intents")
async def sample_intents(_role: Any = Depends(require_role("viewer"))) -> dict[str, Any]:
    return {ct: {**_COMMON, **body} for ct, body in SAMPLE_INTENTS.items()}


@router.post("/checks/templates/validate")
async def validate_templates(
    payload: ValidateRequest, _role: Any = Depends(require_role("operator"))
) -> dict[str, Any]:
    try:
        templates = CheckTemplates.from_yaml_text(payload.yaml, source="<draft>")
    except TemplateValidationError as exc:
        return {"valid": False, "errors": exc.errors, "templates": None}
    return {"valid": True, "errors": [], "templates": templates.summary()}


@router.post("/checks/templates/preview")
async def preview_templates(
    payload: PreviewRequest, request: Request, _role: Any = Depends(require_role("viewer"))
) -> dict[str, Any]:
    try:
        intent = parse_intent(payload.intent)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"intent is invalid: {exc}") from exc
    if payload.yaml is not None:
        try:
            templates = CheckTemplates.from_yaml_text(payload.yaml, source="<draft>")
        except TemplateValidationError as exc:
            return {"valid": False, "errors": exc.errors, "preview": None}
    else:
        templates = _active(request)
    return {
        "valid": True,
        "errors": [],
        "change_type": intent.change_type,
        "platform": payload.platform,
        "preview": templates.preview(intent, payload.platform),
    }


# --- Config render templates ----------------------------------------------


class RenderPreviewRequest(BaseModel):
    intent: dict[str, Any]
    platform: str
    device: str = "edge-01"
    yaml: str | None = None


@router.get("/render/templates")
async def get_render_templates(
    request: Request, _role: Any = Depends(require_role("viewer"))
) -> dict[str, Any]:
    from vireonet.rendering.platforms import _ALIASES, KNOWN_PLATFORMS

    renderer = getattr(request.app.state, "renderer", None)
    errors = getattr(request.app.state, "render_templates_error", [])
    if renderer is None:
        return {"valid": False, "errors": errors, "platforms": [], "aliases": {}, "yaml": ""}
    return {
        "valid": True,
        "errors": [],
        "builtin_platforms": list(KNOWN_PLATFORMS),
        "platforms": renderer.supported_platforms(),
        "aliases": dict(_ALIASES),
        "source": renderer.templates.source,
        "yaml": renderer.templates.text,
        "templates": {ct: sorted(per) for ct, per in renderer.templates.data.templates.items()},
    }


@router.post("/render/preview")
async def preview_render(
    payload: RenderPreviewRequest, request: Request, _role: Any = Depends(require_role("viewer"))
) -> dict[str, Any]:
    from vireonet.rendering import Renderer, UnsupportedChange
    from vireonet.rendering.templates import RenderTemplateError, RenderTemplates

    try:
        intent = parse_intent({**payload.intent, "devices": [payload.device]})
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"intent is invalid: {exc}") from exc
    if payload.yaml is not None:
        try:
            renderer = Renderer(RenderTemplates.from_yaml_text(payload.yaml, source="<draft>"))
        except RenderTemplateError as exc:
            return {"valid": False, "errors": exc.errors, "rendered": None}
    else:
        active = getattr(request.app.state, "renderer", None)
        if active is None:
            raise HTTPException(status_code=409, detail="render templates are invalid")
        renderer = active
    try:
        rendered = renderer.render(intent, payload.device, payload.platform)
    except UnsupportedChange as exc:
        return {"valid": True, "errors": [str(exc)], "rendered": None}
    return {"valid": True, "errors": [], "rendered": rendered.to_dict()}
