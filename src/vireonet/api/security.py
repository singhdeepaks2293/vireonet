from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from vireonet.config import Settings
from vireonet.iam.auth import decode_access_token
from vireonet.iam.permissions import can
from vireonet.structlog_config import get_logger

bearer = HTTPBearer(auto_error=False)


async def verify_webhook(request: Request, settings: Settings) -> None:
    """ServiceNow webhook auth: HMAC (preferred) or bearer.

    No secret configured is fail-closed: only an explicit dev flag allows
    the call through (with a warning). Fake adapter mode does NOT bypass
    webhook auth; only dev=True does.

    HMAC: SHA256 over the raw body, header X-VireoNet-Signature, optionally
    prefixed with `sha256=`. Bearer: Authorization: Bearer <webhook_secret|api_token>.
    """
    if not settings.webhook_secret and not settings.api_token:
        if settings.dev:
            get_logger(__name__).warning("webhook called with no secret configured (dev mode)")
            return
        raise HTTPException(
            status_code=401, detail="webhook authentication required: no secret configured"
        )
    body = await request.body()
    sig = request.headers.get("X-VireoNet-Signature", "")
    if sig:
        # Never sign with an empty key: anyone could forge that signature.
        if not settings.webhook_secret:
            raise HTTPException(
                status_code=401, detail="HMAC signatures need VIREONET_WEBHOOK_SECRET"
            )
        expected = hmac.new(settings.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        candidate = sig.removeprefix("sha256=")
        if hmac.compare_digest(candidate, expected):
            return
        raise HTTPException(status_code=401, detail="bad webhook signature")
    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    if token and any(
        secret and hmac.compare_digest(token.encode(), secret.encode())
        for secret in (settings.webhook_secret, settings.api_token)
    ):
        return
    raise HTTPException(status_code=401, detail="webhook authentication required")


class CurrentUser:
    def __init__(self, user_id: str, username: str, role: str):
        self.user_id = user_id
        self.username = username
        self.role = role


async def get_current_user(
    request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer)
) -> CurrentUser:
    # The X-VireoNet-Role header is deliberately NOT trusted. Role comes from the
    # signed JWT only. A request without a Bearer token is unauthenticated.
    if creds is None or not creds.credentials:
        raise HTTPException(status_code=401, detail="a valid Bearer token is required")
    settings = request.app.state.settings
    try:
        payload = decode_access_token(
            creds.credentials, secret=settings.jwt_secret, algorithm=settings.jwt_algorithm
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail="invalid or expired token") from exc
    user_id = str(payload.get("sub", ""))
    role = str(payload.get("role", ""))
    if not user_id or role not in {"viewer", "operator", "approver", "admin"}:
        raise HTTPException(status_code=401, detail="invalid token claims")
    # Check the user is still active in the DB; a deactivated user's token stops working.
    svc = getattr(request.app.state, "user_service", None)
    if svc is not None:
        user = await svc.get(user_id)
        if user is None or not user.is_active:
            raise HTTPException(status_code=401, detail="user is inactive")
        # Token version: set_role/set_active bump user.token_version, which
        # revokes every token minted before the change. Tokens minted
        # before token_version existed carry no "tv" claim and default to 0.
        if int(payload.get("tv") or 0) != int(getattr(user, "token_version", 0)):
            raise HTTPException(
                status_code=401, detail="token revoked (role changed); sign in again"
            )
        role = user.role  # DB role wins over the token claim if they differ
    return CurrentUser(user_id=user_id, username=str(payload.get("username", "")), role=role)


def require_role(minimum: str) -> Callable[[CurrentUser], Coroutine[Any, Any, CurrentUser]]:
    async def _check(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not can(user.role, minimum):
            raise HTTPException(status_code=403, detail=f"{minimum} role required")
        return user

    return _check


def require_permission(
    permission: str,
) -> Callable[[CurrentUser], Coroutine[Any, Any, CurrentUser]]:
    from vireonet.iam.permissions import has_permission

    async def _check(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not has_permission(user.role, permission):
            raise HTTPException(status_code=403, detail=f"permission {permission} required")
        return user

    return _check
