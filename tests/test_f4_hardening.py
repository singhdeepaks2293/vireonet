"""F4 hardening: fail-closed startup, webhook auth, JWT token-version revocation.

DB is in-memory SQLite; no network. The app is built with a session
factory bound to a pre-created in-memory engine (create_app accepts one),
so login / /auth/me exercise the real UserService + token-version check.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vireonet.config import Settings, validate_settings
from vireonet.db import Base, create_engine
from vireonet.iam.service import UserService
from vireonet.main import create_app

REAL_JWT = "a-real-jwt-secret-that-is-32-chars"
REAL_DB = "postgresql+asyncpg://vireonet:s3cret@localhost:5432/vireonet"


def _real_settings(**overrides) -> Settings:
    base = {
        "adapter_mode": "real",
        "jwt_secret": REAL_JWT,
        "database_url": REAL_DB,
        "webhook_secret": "hook-secret",
        "gitlab_webhook_token": "gl-secret",
    }
    base.update(overrides)
    return Settings(**base)


# --- validate_settings -------------------------------------------------


def test_validate_refuses_default_jwt_secret_in_real_mode() -> None:
    problems = validate_settings(_real_settings(jwt_secret="dev-only-change-me"))
    assert any("jwt_secret" in p for p in problems)


def test_validate_refuses_default_db_password_in_real_mode() -> None:
    problems = validate_settings(
        _real_settings(
            database_url="postgresql+asyncpg://vireonet:vireonet@localhost:5432/vireonet"
        )
    )
    assert any("database" in p and "password" in p for p in problems)


def test_validate_refuses_missing_db_password_in_real_mode() -> None:
    problems = validate_settings(
        _real_settings(database_url="postgresql+asyncpg://vireonet@localhost:5432/vireonet")
    )
    assert any("database" in p and "password" in p for p in problems)


def test_validate_refuses_missing_webhook_secrets_in_real_mode() -> None:
    problems = validate_settings(
        _real_settings(webhook_secret="", api_token="", gitlab_webhook_token="")
    )
    assert any("webhook_secret" in p or "api_token" in p for p in problems)
    assert any("gitlab_webhook_token" in p for p in problems)


def test_validate_passes_with_real_secrets() -> None:
    assert validate_settings(_real_settings()) == []


def test_validate_passes_in_fake_mode_with_defaults() -> None:
    # Fake mode always passes so unit tests and demos start with defaults.
    assert validate_settings(Settings(adapter_mode="fake")) == []


def test_validate_dev_flag_relaxes_webhook_secrets_only() -> None:
    # dev=True waives the webhook-secret problems but not default secrets.
    problems = validate_settings(
        _real_settings(webhook_secret="", api_token="", gitlab_webhook_token="", dev=True)
    )
    assert not any("webhook" in p or "gitlab" in p for p in problems)
    assert not any("jwt_secret" in p for p in problems)  # jwt is real here
    problems = validate_settings(_real_settings(jwt_secret="dev-only-change-me", dev=True))
    assert any("jwt_secret" in p for p in problems)


def test_create_app_refuses_real_mode_with_defaults() -> None:
    with pytest.raises(RuntimeError, match="refusing to start in real mode"):
        create_app(Settings(adapter_mode="real"))


def test_create_app_with_fake_settings_does_not_raise() -> None:
    # Fake settings must start even with default secrets and no webhooks.
    app = create_app(Settings(adapter_mode="fake", dev=False))
    assert app.state.settings.adapter_mode == "fake"


# --- webhook auth ------------------------------------------------------


def _webhook_client(**settings_overrides) -> TestClient:
    settings = Settings(adapter_mode="fake", dev=False, database_url="sqlite+aiosqlite:///:memory:")
    settings = settings.model_copy(update=settings_overrides)
    return TestClient(create_app(settings))


def test_webhook_without_secret_401_in_normal_mode() -> None:
    client = _webhook_client()
    assert client.post("/webhooks/gitlab", json={"object_kind": "pipeline"}).status_code == 401
    assert client.post("/webhooks/github", json={}).status_code == 401
    assert client.post("/webhooks/bitbucket", json={}).status_code == 401
    assert client.post("/webhooks/gitea", json={}).status_code == 401


def test_webhook_with_secret_accepted() -> None:
    client = _webhook_client(webhook_secret="hook-secret", gitlab_webhook_token="gl-secret")
    resp = client.post(
        "/webhooks/gitlab",
        json={"object_kind": "pipeline"},
        headers={"X-Gitlab-Token": "gl-secret"},
    )
    assert resp.status_code == 200, resp.text
    resp = client.post(
        "/webhooks/github",
        json={},
        headers={"Authorization": "Bearer hook-secret"},
    )
    assert resp.status_code == 200, resp.text


def test_webhook_dev_flag_accepts_without_secret() -> None:
    client = _webhook_client(dev=True)
    assert client.post("/webhooks/gitlab", json={"object_kind": "pipeline"}).status_code == 200
    assert client.post("/webhooks/github", json={}).status_code == 200


# --- token-version revocation ------------------------------------------


async def _app_with_users() -> tuple[TestClient, UserService]:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    settings = Settings(
        adapter_mode="fake",
        dev=True,
        jwt_secret=REAL_JWT,
        database_url="sqlite+aiosqlite:///:memory:",
    )
    app = create_app(settings, session_factory=factory)
    return TestClient(app), app.state.user_service


async def test_role_change_revokes_old_token() -> None:
    client, svc = await _app_with_users()
    assert isinstance(svc, UserService)
    admin1 = await svc.create(
        username="admin1", display_name="Admin 1", password="correct-horse-battery", role="admin"
    )
    admin2 = await svc.create(
        username="admin2", display_name="Admin 2", password="correct-horse-battery", role="admin"
    )

    login = client.post(
        "/auth/login", json={"username": "admin1", "password": "correct-horse-battery"}
    )
    assert login.status_code == 200, login.text
    old_token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {old_token}"}
    assert client.get("/auth/me", headers=headers).status_code == 200

    # Role change via the service (actor = the other admin; self-demotion
    # is blocked by the self-guard, so the subject cannot do this alone).
    updated = await svc.set_role(admin1.id, "viewer", actor_id=admin2.id)
    assert updated is not None and updated.role == "viewer"
    assert updated.token_version == 1

    # The pre-change token is revoked; a fresh login mints tv=1 and works.
    assert client.get("/auth/me", headers=headers).status_code == 401
    fresh = client.post(
        "/auth/login", json={"username": "admin1", "password": "correct-horse-battery"}
    )
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["role"] == "viewer"
    assert (
        client.get(
            "/auth/me", headers={"Authorization": f"Bearer {fresh.json()['access_token']}"}
        ).status_code
        == 200
    )


async def test_set_active_bumps_token_version() -> None:
    _, svc = await _app_with_users()
    assert isinstance(svc, UserService)
    admin = await svc.create(
        username="admin1", display_name="Admin 1", password="correct-horse-battery", role="admin"
    )
    user = await svc.create(
        username="alice", display_name="Alice", password="correct-horse-battery", role="viewer"
    )
    assert user.token_version == 0
    updated = await svc.set_active(user.id, False, actor_id=admin.id)
    assert updated is not None and updated.token_version == 1
