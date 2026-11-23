from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from vireonet.api.security import require_role
from vireonet.safety import DeviceLockService

router = APIRouter()


class LockResponse(BaseModel):
    device: str
    ticket_number: str
    holder: str
    acquired_at: str | None = None
    expires_at: str | None = None


class ChangeWindowResponse(BaseModel):
    days: list[str]
    start: str
    end: str
    timezone: str
    emergency_bypasses_window: bool
    currently_in_window: bool
    next_window_start: str | None = None


def _lock_service(request: Request) -> DeviceLockService:
    svc = request.app.state.lock_service
    if svc is None:
        raise HTTPException(status_code=503, detail="lock service not configured")
    return cast(DeviceLockService, svc)


@router.get("/locks", response_model=list[LockResponse])
async def list_locks(
    request: Request, _role: str = Depends(require_role("viewer"))
) -> list[LockResponse]:
    svc = _lock_service(request)
    locks = await svc.list()
    return [
        LockResponse(
            device=lock.device,
            ticket_number=lock.ticket_number,
            holder=lock.holder,
            acquired_at=lock.created_at.isoformat() if lock.created_at else None,
            expires_at=lock.expires_at.isoformat() if lock.expires_at else None,
        )
        for lock in locks
    ]


@router.post("/locks/{device}/release", status_code=202)
async def release_lock(
    device: str, request: Request, _role: str = Depends(require_role("operator"))
) -> dict[str, str]:
    svc = _lock_service(request)
    released = await svc.release_any(device)
    if not released:
        raise HTTPException(status_code=404, detail="no lock held for this device")
    return {"device": device, "status": "released"}


@router.get("/change-windows", response_model=list[ChangeWindowResponse])
async def list_change_windows(
    request: Request, _role: str = Depends(require_role("viewer"))
) -> list[ChangeWindowResponse]:
    # Read from the same policy file the validator enforces (M2), so UI and
    # policy cannot disagree. Path is configurable via settings in main.py.
    policy_path = getattr(request.app.state.settings, "policy_path", "policies/default.yaml")
    data = yaml.safe_load(Path(policy_path).read_text(encoding="utf-8"))
    windows = data.get("change_windows", [])
    now = datetime.now(UTC)
    out: list[ChangeWindowResponse] = []
    for w in windows:
        days: list[str] = w.get("days", [])
        start = str(w.get("start", "01:00"))
        end = str(w.get("end", "05:00"))
        tz = str(w.get("timezone", "UTC"))
        # Currently in window: weekday name matches and time is within start..end (UTC).
        day_name = now.strftime("%a")  # Mon, Tue, ...
        in_window = day_name in days and start <= now.strftime("%H:%M") < end
        # Next window start: next day in `days` at `start`, searching up to 7 days ahead.
        next_start: str | None = None
        for offset in range(0, 8):
            candidate = now + timedelta(days=offset)
            if candidate.strftime("%a") in days:
                hh, mm = start.split(":")
                next_start = candidate.replace(
                    hour=int(hh), minute=int(mm), second=0, microsecond=0
                ).isoformat()
                if candidate.date() == now.date() and now.strftime("%H:%M") >= start:
                    continue
                break
        out.append(
            ChangeWindowResponse(
                days=days,
                start=start,
                end=end,
                timezone=tz,
                emergency_bypasses_window=bool(data.get("emergency_bypasses_window", True)),
                currently_in_window=in_window,
                next_window_start=next_start,
            )
        )
    return out
