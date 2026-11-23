"""SDLC — Requirements R9: IAM (M9).

User creation, duplicate/short-password/unknown-role rejection, login,
inactive users, last-admin guards, role changes, JWT round-trip and
expiry. DB is in-memory SQLite; no network.
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vireonet.iam.auth import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from vireonet.iam.permissions import ALL_PERMISSIONS, ROLE_PERMISSIONS, can, has_permission
from vireonet.iam.service import UserService

SECRET = "test-secret-for-unit-tests-only"


def _svc(engine) -> UserService:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return UserService(factory)


async def _admin(svc: UserService, username: str = "admin1"):
    return await svc.create(username=username, display_name=username, password="correct-horse-battery", role="admin")


def test_password_hash_and_verify() -> None:
    h = hash_password("correct-horse-battery")
    assert h != "correct-horse-battery"
    assert verify_password("correct-horse-battery", h) is True
    assert verify_password("wrong-password", h) is False


def test_role_permissions_matrix() -> None:
    assert has_permission("viewer", "changes:read")
    assert not has_permission("viewer", "iam:manage")
    assert not has_permission("viewer", "approvals:approve")
    assert has_permission("operator", "changes:rollback")
    assert not has_permission("operator", "iam:manage")
    assert has_permission("approver", "approvals:approve")
    assert has_permission("admin", "iam:manage")
    assert can("admin", "viewer") and can("approver", "operator") and not can("viewer", "operator")
    assert set(ALL_PERMISSIONS) == {p for perms in ROLE_PERMISSIONS.values() for p in perms}
    # Permissions only grow with rank.
    assert set(ROLE_PERMISSIONS["viewer"]) <= set(ROLE_PERMISSIONS["operator"]) <= set(ROLE_PERMISSIONS["approver"]) <= set(ROLE_PERMISSIONS["admin"])


async def test_create_user_and_authenticate(engine) -> None:
    svc = _svc(engine)
    user = await svc.create(username="alice", display_name="Alice", password="correct-horse-battery", role="viewer")
    assert user.role == "viewer" and user.is_active is True
    assert await svc.authenticate("alice", "correct-horse-battery") is not None
    assert await svc.authenticate("alice", "wrong-password") is None
    assert await svc.authenticate("nobody", "correct-horse-battery") is None


async def test_duplicate_username_rejected(engine) -> None:
    svc = _svc(engine)
    await svc.create(username="alice", display_name="Alice", password="correct-horse-battery", role="viewer")
    with pytest.raises(ValueError, match="already exists"):
        await svc.create(username="alice", display_name="Alice 2", password="correct-horse-battery", role="viewer")


@pytest.mark.parametrize("bad", ["short", "12345678901", ""])
async def test_short_password_rejected(engine, bad: str) -> None:
    svc = _svc(engine)
    with pytest.raises(ValueError, match="at least 12 characters"):
        await svc.create(username="bob", display_name="Bob", password=bad, role="viewer")


async def test_unknown_role_rejected(engine) -> None:
    svc = _svc(engine)
    with pytest.raises(ValueError, match="unknown role"):
        await svc.create(username="bob", display_name="Bob", password="correct-horse-battery", role="superuser")


async def test_inactive_user_cannot_authenticate(engine) -> None:
    svc = _svc(engine)
    admin = await _admin(svc, "admin1")
    user = await svc.create(username="alice", display_name="Alice", password="correct-horse-battery", role="viewer")
    await svc.set_active(user.id, False, actor_id=admin.id)
    assert await svc.authenticate("alice", "correct-horse-battery") is None


async def test_last_admin_cannot_be_demoted_or_deactivated(engine) -> None:
    svc = _svc(engine)
    admin1 = await _admin(svc, "admin1")
    admin2 = await _admin(svc, "admin2")
    # Self-demotion is blocked by the self-guard (not the last-admin guard).
    with pytest.raises(ValueError, match="cannot demote themselves"):
        await svc.set_role(admin1.id, "viewer", actor_id=admin1.id)
    # Demote admin1 via admin2: allowed while two admins exist.
    await svc.set_role(admin1.id, "viewer", actor_id=admin2.id)
    # Now admin2 is the last active admin: demoting/deactivating them via
    # another actor hits the last-admin guard.
    with pytest.raises(ValueError, match="last active admin"):
        await svc.set_role(admin2.id, "viewer", actor_id=admin1.id)
    with pytest.raises(ValueError, match="last active admin"):
        await svc.set_active(admin2.id, False, actor_id=admin1.id)


async def test_admin_cannot_demote_or_deactivate_self_when_not_last(engine) -> None:
    svc = _svc(engine)
    admin1 = await _admin(svc, "admin1")
    admin2 = await _admin(svc, "admin2")
    with pytest.raises(ValueError, match="cannot demote themselves"):
        await svc.set_role(admin1.id, "viewer", actor_id=admin1.id)
    with pytest.raises(ValueError, match="cannot deactivate themselves"):
        await svc.set_active(admin1.id, False, actor_id=admin1.id)
    assert admin2.role == "admin"


async def test_role_change_takes_effect(engine) -> None:
    svc = _svc(engine)
    admin = await _admin(svc, "admin1")
    user = await svc.create(username="alice", display_name="Alice", password="correct-horse-battery", role="viewer")
    updated = await svc.set_role(user.id, "operator", actor_id=admin.id)
    assert updated is not None and updated.role == "operator"
    assert await svc.set_role("no-such-id", "viewer", actor_id=admin.id) is None
    assert await svc.set_active("no-such-id", False, actor_id=admin.id) is None


async def test_user_list_is_sorted(engine) -> None:
    svc = _svc(engine)
    await svc.create(username="zoe", display_name="Zoe", password="correct-horse-battery", role="viewer")
    await svc.create(username="amy", display_name="Amy", password="correct-horse-battery", role="viewer")
    assert [u.username for u in await svc.list()] == ["amy", "zoe"]


def test_jwt_round_trip_carries_role() -> None:
    token = create_access_token(user_id="u1", username="alice", role="approver", secret=SECRET)
    payload = decode_access_token(token, secret=SECRET)
    assert payload["sub"] == "u1"
    assert payload["username"] == "alice"
    assert payload["role"] == "approver"


def test_jwt_wrong_secret_rejected() -> None:
    from jwt import InvalidTokenError

    token = create_access_token(user_id="u1", username="alice", role="viewer", secret=SECRET)
    with pytest.raises(InvalidTokenError):
        decode_access_token(token, secret="different-secret")


def test_jwt_expired_rejected() -> None:
    from jwt import ExpiredSignatureError

    token = create_access_token(user_id="u1", username="alice", role="viewer", secret=SECRET, expires_minutes=-1)
    with pytest.raises(ExpiredSignatureError):
        decode_access_token(token, secret=SECRET)
