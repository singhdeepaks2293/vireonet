from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return str(pwd_context.hash(password))


def verify_password(password: str, password_hash: str) -> bool:
    return bool(pwd_context.verify(password, password_hash))


def create_access_token(
    *,
    user_id: str,
    username: str,
    role: str,
    secret: str,
    algorithm: str = "HS256",
    expires_minutes: int = 480,
    token_version: int = 0,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
        "tv": token_version,
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_access_token(token: str, *, secret: str, algorithm: str = "HS256") -> dict[str, Any]:
    return jwt.decode(token, secret, algorithms=[algorithm])
