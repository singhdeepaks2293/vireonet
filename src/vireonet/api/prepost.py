"""Pre/Post checks: profiles (saved templates), snapshots, compare."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from vireonet.api.security import CurrentUser, require_role
from vireonet.prepost.engine import TEST_TYPES, ProfileError, validate_profile
from vireonet.prepost.service import PrePostService

router = APIRouter(prefix="/prepost", tags=["prepost"])


def _svc(request: Request) -> PrePostService:
    st = request.app.state
    return PrePostService(
        st.session_factory,
        engine=getattr(st, "execution_engine", None),
        netbox=st.adapters.get("netbox"),
        textfsm_dir=str(getattr(st.settings, "textfsm_user_dir", "") or "policies/textfsm"),
    )


class CaptureBody(BaseModel):
    profile: str
    devices: list[str] = Field(min_length=1, max_length=100)
    label: str = Field(default="adhoc", pattern="^(pre|post|adhoc)$")
    ticket: str = Field(default="", max_length=64)
    compare_with: str | None = None


class CompareBody(BaseModel):
    pre: str
    post: str


@router.get("/test-types")
async def test_types(_u: CurrentUser = Depends(require_role("viewer"))) -> dict[str, Any]:
    return {"tests": TEST_TYPES}


@router.get("/profiles")
async def profiles(
    request: Request, _u: CurrentUser = Depends(require_role("viewer"))
) -> dict[str, Any]:
    return {"items": await _svc(request).list_profiles()}


@router.get("/profiles/{name}")
async def profile(
    name: str, request: Request, _u: CurrentUser = Depends(require_role("viewer"))
) -> dict[str, Any]:
    try:
        return await _svc(request).get_profile(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="no such profile") from exc


@router.post("/profiles/validate")
async def validate(
    body: dict[str, Any], _u: CurrentUser = Depends(require_role("viewer"))
) -> dict[str, Any]:
    errors = validate_profile(body)
    return {"valid": not errors, "errors": errors}


@router.put("/profiles")
async def save(
    body: dict[str, Any], request: Request, user: CurrentUser = Depends(require_role("operator"))
) -> dict[str, Any]:
    try:
        return await _svc(request).save_profile(body, user.username)
    except ProfileError as exc:
        raise HTTPException(status_code=422, detail={"errors": exc.errors}) from exc


@router.delete("/profiles/{name}", status_code=204)
async def delete(
    name: str, request: Request, _u: CurrentUser = Depends(require_role("approver"))
) -> None:
    try:
        await _svc(request).delete_profile(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="no such profile") from exc


@router.post("/snapshots", status_code=201)
async def capture(
    body: CaptureBody, request: Request, user: CurrentUser = Depends(require_role("operator"))
) -> dict[str, Any]:
    try:
        return await _svc(request).capture(
            body.profile,
            body.devices,
            label=body.label,
            ticket=body.ticket,
            user=user.username,
            pre_id=body.compare_with,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"not found: {exc}") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/snapshots")
async def snapshots(
    request: Request,
    profile: str = "",
    ticket: str = "",
    limit: int = Query(default=100, ge=1, le=500),
    _u: CurrentUser = Depends(require_role("viewer")),
) -> dict[str, Any]:
    return {
        "items": await _svc(request).list_snapshots(profile=profile, ticket=ticket, limit=limit)
    }


@router.get("/snapshots/{snap_id}")
async def snapshot(
    snap_id: str, request: Request, _u: CurrentUser = Depends(require_role("viewer"))
) -> dict[str, Any]:
    try:
        return await _svc(request).get_snapshot(snap_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="no such snapshot") from exc


@router.post("/compare")
async def compare(
    body: CompareBody, request: Request, _u: CurrentUser = Depends(require_role("viewer"))
) -> dict[str, Any]:
    try:
        return await _svc(request).compare(body.pre, body.post)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="no such snapshot") from exc
    except ProfileError as exc:
        raise HTTPException(status_code=422, detail=exc.errors[0]) from exc
