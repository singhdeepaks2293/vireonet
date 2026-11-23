from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from vireonet.api.security import CurrentUser, get_current_user
from vireonet.iam.auth import create_access_token

router = APIRouter()


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
    user = await svc.authenticate(payload.username, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid username or password")
    settings = request.app.state.settings
    token = create_access_token(user_id=user.id, username=user.username, role=user.role,
                                secret=settings.jwt_secret, algorithm=settings.jwt_algorithm,
                                expires_minutes=settings.access_token_expire_minutes)
    return LoginResponse(access_token=token, user_id=user.id, username=user.username, role=user.role)


@router.get("/auth/me")
async def me(user: CurrentUser = Depends(get_current_user)) -> dict[str, str]:
    return {"user_id": user.user_id, "username": user.username, "role": user.role}
