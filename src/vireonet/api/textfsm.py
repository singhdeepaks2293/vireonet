"""TextFSM API: browse ntc-templates and your own templates, parse output,
validate a draft template. Your templates are files in policies/textfsm
(reviewed in Git); drafts are tested here and never saved."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from vireonet.api.security import require_role
from vireonet.textfsm_lib import (
    NTC_PLATFORMS,
    TextFsmError,
    list_templates,
    parse,
    read_template,
    validate,
)

router = APIRouter(prefix="/textfsm")


def _user_dir(request: Request) -> str:
    return str(request.app.state.settings.textfsm_user_dir)


@router.get("/templates")
async def templates(
    request: Request,
    platform: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    _role: Any = Depends(require_role("viewer")),
) -> dict[str, Any]:
    out = list_templates(platform, q, user_dir=_user_dir(request), limit=limit)
    out["platform_map"] = NTC_PLATFORMS
    out["user_dir"] = _user_dir(request)
    return out


@router.get("/templates/{source}/{name}")
async def template_text(
    source: str, name: str, request: Request, _role: Any = Depends(require_role("viewer"))
) -> dict[str, Any]:
    if source not in {"user", "ntc"}:
        raise HTTPException(status_code=404, detail="source must be user or ntc")
    try:
        return {
            "source": source,
            "template": name,
            "text": read_template(source, name, user_dir=_user_dir(request)),
        }
    except TextFsmError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class ParseRequest(BaseModel):
    platform: str = Field(min_length=1)
    command: str = Field(min_length=1)
    output: str = Field(max_length=2_000_000)
    template: str | None = Field(default=None, max_length=200_000)  # a draft to test instead


@router.post("/parse")
async def parse_output(
    payload: ParseRequest, request: Request, _role: Any = Depends(require_role("viewer"))
) -> dict[str, Any]:
    try:
        result = parse(
            payload.platform,
            payload.command,
            payload.output,
            template_text=payload.template,
            user_dir=_user_dir(request),
        )
    except TextFsmError as exc:
        return {"ok": False, "error": str(exc), "rows": [], "headers": []}
    return {"ok": True, **result.to_dict()}


class ValidateRequest(BaseModel):
    template: str = Field(max_length=200_000)


@router.post("/validate")
async def validate_template(
    payload: ValidateRequest, _role: Any = Depends(require_role("viewer"))
) -> dict[str, Any]:
    return validate(payload.template)
