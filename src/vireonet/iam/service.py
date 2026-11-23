from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vireonet.iam.auth import hash_password, verify_password
from vireonet.models import User


class UserService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self._sessions = sessions

    async def create(self, *, username: str, display_name: str, password: str, role: str) -> User:
        if role not in {"viewer", "operator", "approver", "admin"}:
            raise ValueError(f"unknown role {role}")
        if len(password) < 12:
            raise ValueError("password must be at least 12 characters")
        async with self._sessions() as session:
            existing = (
                await session.execute(select(User).where(User.username == username))
            ).scalar_one_or_none()
            if existing is not None:
                raise ValueError(f"user {username} already exists")
            user = User(
                id=str(uuid.uuid4()),
                username=username,
                display_name=display_name or username,
                password_hash=hash_password(password),
                role=role,
                is_active=True,
                auth_provider="local",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    async def authenticate(self, username: str, password: str) -> User | None:
        async with self._sessions() as session:
            user = (
                await session.execute(select(User).where(User.username == username))
            ).scalar_one_or_none()
            if user is None or not user.is_active:
                return None
            if not verify_password(password, user.password_hash):
                return None
            return user

    async def list(self) -> list[User]:
        async with self._sessions() as session:
            return list(
                (await session.execute(select(User).order_by(User.username))).scalars().all()
            )

    async def get(self, user_id: str) -> User | None:
        async with self._sessions() as session:
            return await session.get(User, user_id)

    async def set_role(self, user_id: str, role: str, *, actor_id: str) -> User | None:
        if role not in {"viewer", "operator", "approver", "admin"}:
            raise ValueError(f"unknown role {role}")
        async with self._sessions() as session:
            user = await session.get(User, user_id)
            if user is None:
                return None
            if user.id == actor_id and role != "admin":
                raise ValueError("an admin cannot demote themselves")
            if user.role == "admin" and role != "admin":
                count = int(
                    (
                        await session.execute(
                            select(func.count())
                            .select_from(User)
                            .where(User.role == "admin", User.is_active.is_(True))
                        )
                    ).scalar_one()
                )
                if count <= 1:
                    raise ValueError("cannot demote the last active admin")
            user.role = role
            # Revoke outstanding tokens: a role change invalidates JWTs
            # minted before it (see get_current_user token-version check).
            user.token_version = int(user.token_version or 0) + 1
            await session.commit()
            await session.refresh(user)
            return user

    async def set_active(self, user_id: str, active: bool, *, actor_id: str) -> User | None:
        async with self._sessions() as session:
            user = await session.get(User, user_id)
            if user is None:
                return None
            if user.id == actor_id and not active:
                raise ValueError("an admin cannot deactivate themselves")
            if user.role == "admin" and not active:
                count = int(
                    (
                        await session.execute(
                            select(func.count())
                            .select_from(User)
                            .where(User.role == "admin", User.is_active.is_(True))
                        )
                    ).scalar_one()
                )
                if count <= 1:
                    raise ValueError("cannot deactivate the last active admin")
            user.is_active = active
            # Revoke outstanding tokens: deactivation/reactivation
            # invalidates JWTs minted before it.
            user.token_version = int(user.token_version or 0) + 1
            await session.commit()
            await session.refresh(user)
            return user
