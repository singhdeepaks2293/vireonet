from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from vireonet.api.security import CurrentUser, get_current_user
from vireonet.iam.auth import create_access_token

router = APIRouter()
# Failed sign-ins per username (monotonic timestamps). Per API process: with
# several replicas put a shared limiter (gateway / Redis) in front as well.
_FAILURES: dict[str, list[float]] = {}


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    username: str
    role: str


@router.post("/auth/login", response_model=LoginResponse)
async def login(payload: LoginRequest, request: Request) -> LoginResponse:
    svc = request.app.state.user_service
    if svc is None:
        raise HTTPException(status_code=503, detail="user service not configured")
    key = payload.username.strip().lower()
    settings = request.app.state.settings
    failures = _FAILURES.setdefault(key, [])
    now = time.monotonic()
    failures[:] = [t for t in failures if now - t < settings.login_lockout_seconds]
    if len(failures) >= settings.login_max_failures:
        raise HTTPException(
            status_code=429,
            detail="too many failed sign-ins for this user; try again later",
            headers={"Retry-After": str(settings.login_lockout_seconds)},
        )
    user = await svc.authenticate(payload.username, payload.password)
    if user is None:
        failures.append(now)
        raise HTTPException(status_code=401, detail="invalid username or password")
    _FAILURES.pop(key, None)
    settings = request.app.state.settings
    token = create_access_token(
        user_id=user.id,
        username=user.username,
        role=user.role,
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        expires_minutes=settings.access_token_expire_minutes,
        token_version=int(getattr(user, "token_version", 0)),
    )
    return LoginResponse(
        access_token=token, user_id=user.id, username=user.username, role=user.role
    )


@router.get("/auth/me")
async def me(user: CurrentUser = Depends(get_current_user)) -> dict[str, str]:
    return {"user_id": user.user_id, "username": user.username, "role": user.role}
