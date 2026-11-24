"""Vendor controllers: status, devices and images (read-only).

Changes (distribute, activate, upgrade, API calls) happen only inside flows,
where they are versioned, approved, logged and durable.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from vireonet.api.security import CurrentUser, require_role
from vireonet.controllers.base import ControllerError

router = APIRouter(prefix="/controllers", tags=["controllers"])


def _get(request: Request, name: str) -> Any:
    ctl = (getattr(request.app.state, "controllers", {}) or {}).get(name)
    if ctl is None:
        raise HTTPException(status_code=404, detail=f"no controller named {name!r}")
    return ctl


@router.get("")
async def list_controllers(
    request: Request, _u: CurrentUser = Depends(require_role("viewer"))
) -> dict[str, Any]:
    ctls = getattr(request.app.state, "controllers", {}) or {}
    health = await asyncio.gather(*(c.health() for c in ctls.values()), return_exceptions=True)
    items = []
    for (name, c), h in zip(ctls.items(), health, strict=True):
        hd = h if isinstance(h, dict) else {"status": "down", "detail": str(h)}
        items.append(
            {
                "name": name,
                "vendor": getattr(c, "vendor", ""),
                "platforms": list(getattr(c, "platforms", ())),
                "native_upgrade": bool(getattr(c, "native_upgrade", True)),
                "fake": type(c).__name__ == "FakeController",
                "status": hd.get("status"),
                "detail": hd.get("detail", ""),
            }
        )
    return {"items": items}


@router.get("/{name}/devices")
async def devices(
    name: str, request: Request, _u: CurrentUser = Depends(require_role("viewer"))
) -> dict[str, Any]:
    try:
        return {"items": [d.to_dict() for d in await _get(request, name).list_devices()]}
    except ControllerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/{name}/images")
async def images(
    name: str,
    request: Request,
    platform: str = "",
    _u: CurrentUser = Depends(require_role("viewer")),
) -> dict[str, Any]:
    try:
        return {"items": [i.to_dict() for i in await _get(request, name).list_images(platform)]}
    except ControllerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
