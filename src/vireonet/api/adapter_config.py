"""Adapters → Configure: view and edit adapter settings from the UI (admin).

GET shows every adapter setting with where its value comes from (ui / env /
default); secrets only say whether they are set. PUT saves overrides
(validated like environment values). POST /test checks the candidate
settings against the real system before saving.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from vireonet.adapter_config import (
    GROUPS,
    JSON_FIELDS,
    apply_overrides,
    coerce,
    encrypt,
    field_kind,
    group_fields,
    is_secret,
    load_overrides,
)
from vireonet.api.security import CurrentUser, require_role

router = APIRouter(prefix="/adapters/config", tags=["adapters"])


class SaveBody(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    clear: list[str] = Field(default_factory=list)


class TestBody(BaseModel):
    group: str
    values: dict[str, Any] = Field(default_factory=dict)


def _settings_cls(request: Request) -> Any:
    return type(request.app.state.settings)


@router.get("")
async def get_config(
    request: Request, _u: CurrentUser = Depends(require_role("admin"))
) -> dict[str, Any]:
    settings = request.app.state.settings
    cls = type(settings)
    try:
        saved = await load_overrides(request.app.state.session_factory, settings.jwt_secret)
    except Exception:  # noqa: BLE001 — table missing on an old DB
        saved = {}
    groups = []
    for g, names in group_fields(cls).items():
        fields = []
        for n in names:
            info = cls.model_fields[n]
            kind, options = field_kind(info.annotation)
            env = f"VIREONET_{n.upper()}"
            secret = is_secret(n)
            current = getattr(settings, n)
            source = "ui" if n in saved else "env" if env in os.environ else "default"
            fields.append(
                {
                    "name": n,
                    "env": env,
                    "kind": "json" if n in JSON_FIELDS else ("secret" if secret else kind),
                    "options": options,
                    "secret": secret,
                    "value": None if secret else current,
                    "is_set": bool(current) if secret else None,
                    "source": source,
                    "default": None if secret else info.default,
                }
            )
        groups.append({"id": g, "label": GROUPS[g]["label"], "fields": fields})
    return {
        "groups": groups,
        "note": "Saved values override the environment when the API and the worker next start.",
    }


def _candidate(request: Request, values: dict[str, Any], clear: list[str] | None = None) -> Any:
    cls = _settings_cls(request)
    allowed = {n for names in group_fields(cls).values() for n in names}
    bad = sorted(set(values) - allowed) + sorted(set(clear or []) - allowed)
    if bad:
        raise HTTPException(status_code=422, detail=f"not adapter settings: {bad}")
    try:
        coerced = {k: coerce(cls, k, v) for k, v in values.items()}
        return apply_overrides(request.app.state.settings, coerced), coerced
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid value: {exc}") from exc


@router.put("")
async def save_config(
    body: SaveBody, request: Request, user: CurrentUser = Depends(require_role("admin"))
) -> dict[str, Any]:
    from sqlalchemy import delete

    from vireonet.models import AdapterOverride

    _settings, coerced = _candidate(request, body.values, body.clear)
    jwt = request.app.state.settings.jwt_secret
    async with request.app.state.session_factory() as s:
        for name in body.clear:
            await s.execute(delete(AdapterOverride).where(AdapterOverride.key == name))
        for name, value in coerced.items():
            secret = is_secret(name)
            if secret and value in ("", None):
                continue  # an empty secret field means "keep the stored secret"
            row = await s.get(AdapterOverride, name)
            stored = {"enc": encrypt(str(value), jwt)} if secret else {"v": value}
            if row is None:
                s.add(
                    AdapterOverride(key=name, value=stored, secret=secret, updated_by=user.username)
                )
            else:
                row.value, row.secret, row.updated_by = stored, secret, user.username
        await s.commit()
    return {
        "saved": sorted(k for k, v in coerced.items() if not (is_secret(k) and v in ("", None))),
        "cleared": sorted(body.clear),
        "restart_needed": True,
        "message": "Saved. Restart the API and the worker to apply.",
    }


@router.post("/test")
async def test_config(
    body: TestBody, request: Request, _u: CurrentUser = Depends(require_role("admin"))
) -> dict[str, Any]:
    """Health-check one adapter group with the candidate settings (nothing is saved)."""
    if body.group not in GROUPS:
        raise HTTPException(status_code=404, detail=f"no group {body.group!r}")
    cand, _ = _candidate(request, body.values)
    try:
        result = await _health(body.group, cand)
    except Exception as exc:  # noqa: BLE001 — report, never 500
        return {"group": body.group, "ok": False, "detail": f"{type(exc).__name__}: {exc}"[:400]}
    return {"group": body.group, **result}


async def _health(group: str, s: Any) -> dict[str, Any]:
    if group == "itsm" or group == "servicenow":
        from vireonet.adapters.registry import build_adapters
        from vireonet.itsm.factory import build_itsm

        adapters = build_adapters(s) if group == "servicenow" else None
        itsm = build_itsm(s, adapters)
        if itsm is None:
            return {"ok": False, "detail": "no ITSM provider selected (itsm_provider)"}
        h = await itsm.health()
        return {"ok": h.get("status") == "ok", "detail": h}
    if group == "telemetry":
        from vireonet.telemetry.factory import build_telemetry

        t = build_telemetry(s)
        if t is None:
            return {"ok": False, "detail": "no telemetry backend selected (telemetry_backend)"}
        h = await t.health()
        return {"ok": h.get("status") == "ok", "detail": h}
    if group == "controllers":
        from vireonet.controllers import build_controllers

        items = [await c.health() for c in build_controllers(s).values()]
        return {"ok": all(i.get("status") == "ok" for i in items), "detail": items}
    if group == "logs":
        from vireonet.logsearch import build_log_backends

        items = [await b.health() for b in build_log_backends(s).values()]
        return {"ok": all(i.get("status") == "ok" for i in items), "detail": items}
    if group in ("netbox", "awx", "vault", "notifications", "monitoring", "labs", "batfish", "git"):
        from vireonet.adapters.registry import build_adapters

        name = {"notifications": "notification", "labs": "eveng", "git": "gitlab"}.get(group, group)
        adapter = build_adapters(s).get(name)
        if adapter is None:
            return {"ok": False, "detail": f"no {name} adapter in this mode"}
        ah = await adapter.health_check()
        status = getattr(ah, "status", "")
        return {
            "ok": status == "ok",
            "detail": ah.model_dump() if hasattr(ah, "model_dump") else str(ah),
        }
    if group == "gitea":
        import httpx

        ui = str(
            getattr(s, "gitea_api_url", "")
            or (str(getattr(s, "gitea_ui_url", "")).rstrip("/") + "/api/v1")
        )
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{ui}/version")
        return {
            "ok": r.status_code == 200,
            "detail": r.json() if r.status_code == 200 else f"HTTP {r.status_code}",
        }
    return {"ok": True, "detail": "no live test for this group; the values are valid"}
