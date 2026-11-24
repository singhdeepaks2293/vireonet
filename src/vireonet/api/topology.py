"""Topology and telemetry API (NeXt UI tab)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from vireonet.api.security import require_role
from vireonet.telemetry.base import TelemetryError
from vireonet.topology.service import build_topology

router = APIRouter()


def _telemetry(request: Request) -> Any:
    return getattr(request.app.state, "telemetry", None)


@router.get("/topology")
async def topology(
    request: Request,
    site: str | None = Query(default=None),
    _role: Any = Depends(require_role("viewer")),
) -> dict[str, Any]:
    settings = request.app.state.settings
    return await build_topology(
        netbox=request.app.state.adapters["netbox"],
        telemetry=_telemetry(request),
        site=site,
        demo=settings.adapter_mode == "fake",
    )


@router.get("/telemetry/health")
async def telemetry_health(
    request: Request, _role: Any = Depends(require_role("viewer"))
) -> dict[str, Any]:
    backend = _telemetry(request)
    if backend is None:
        return {"backend": None, "status": "disabled"}
    try:
        return dict(await backend.health())
    except TelemetryError as exc:
        return {"backend": backend.name, "status": "down", "detail": str(exc)}


@router.get("/telemetry/interfaces")
async def interface_rates(
    request: Request,
    device: list[str] = Query(...),
    _role: Any = Depends(require_role("viewer")),
) -> dict[str, Any]:
    backend = _telemetry(request)
    if backend is None:
        raise HTTPException(
            status_code=503, detail="telemetry is disabled (VIREONET_TELEMETRY_BACKEND)"
        )
    try:
        rates = await backend.interface_rates(device)
    except TelemetryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"backend": backend.name, "interfaces": [r.to_dict() for r in rates]}


@router.get("/telemetry/interfaces/{device}/history")
async def interface_history(
    device: str,
    request: Request,
    interface: str = Query(...),
    minutes: int = Query(default=60, ge=5, le=60 * 24 * 7),
    step_seconds: int = Query(default=300, ge=10, le=3600),
    _role: Any = Depends(require_role("viewer")),
) -> dict[str, Any]:
    backend = _telemetry(request)
    if backend is None:
        raise HTTPException(status_code=503, detail="telemetry is disabled")
    try:
        points = await backend.interface_history(device, interface, minutes, step_seconds)
    except TelemetryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "backend": backend.name,
        "device": device,
        "interface": interface,
        "points": [p.to_dict() for p in points],
    }
