from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from vireonet.api.security import CurrentUser, require_role
from vireonet.iam.permissions import ALL_PERMISSIONS, ROLE_PERMISSIONS
from vireonet.iam.service import UserService
from vireonet.models import User

router = APIRouter()


class CreateUserRequest(BaseModel):
    username: str
    display_name: str = ""
    password: str
    role: str = "viewer"


class UpdateUserRequest(BaseModel):
    role: str | None = None
    is_active: bool | None = None


class UserResponse(BaseModel):
    id: str
    username: str
    display_name: str
    role: str
    is_active: bool
    auth_provider: str
    created_at: str | None = None


def _to_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        auth_provider=user.auth_provider,
        created_at=user.created_at.isoformat() if user.created_at else None,
    )


def _svc(request: Request) -> UserService:
    svc = request.app.state.user_service
    if svc is None:
        raise HTTPException(status_code=503, detail="user service not configured")
    return cast(UserService, svc)


@router.get("/iam/users", response_model=list[UserResponse])
async def list_users(
    request: Request, _admin: CurrentUser = Depends(require_role("admin"))
) -> list[UserResponse]:
    return [_to_response(u) for u in await _svc(request).list()]


@router.post("/iam/users", status_code=201, response_model=UserResponse)
async def create_user(
    payload: CreateUserRequest,
    request: Request,
    _admin: CurrentUser = Depends(require_role("admin")),
) -> UserResponse:
    try:
        user = await _svc(request).create(
            username=payload.username,
            display_name=payload.display_name,
            password=payload.password,
            role=payload.role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_response(user)


@router.patch("/iam/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    payload: UpdateUserRequest,
    request: Request,
    admin: CurrentUser = Depends(require_role("admin")),
) -> UserResponse:
    svc = _svc(request)
    try:
        user = None
        if payload.role is not None:
            user = await svc.set_role(user_id, payload.role, actor_id=admin.user_id)
        if payload.is_active is not None:
            user = await svc.set_active(user_id, payload.is_active, actor_id=admin.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return _to_response(user)


@router.get("/iam/roles")
async def list_roles(_admin: CurrentUser = Depends(require_role("admin"))) -> dict[str, Any]:
    return {"roles": ROLE_PERMISSIONS, "permissions": ALL_PERMISSIONS}
