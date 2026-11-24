from __future__ import annotations

from fastapi.testclient import TestClient

from vireonet.api.security import CurrentUser, get_current_user
from vireonet.config import Settings
from vireonet.main import create_app

BASE = {
    "devices": ["leaf-01"],
    "site": "dc1",
    "ticket_number": "RITM0001234",
    "requested_by": "alice",
}


def _client() -> TestClient:
    # No webhook secret in Settings => the explicit dev flag allows the
    # call (see security.py); fake mode alone does not bypass webhook auth.
    return TestClient(
        create_app(
            Settings(adapter_mode="fake", dev=True, database_url="sqlite+aiosqlite:///:memory:")
        )
    )


def test_post_changes_valid_intent_returns_202_validated() -> None:
    # ChangeService needs a real DB session factory; the app's default factory
    # points at the in-memory SQLite URL, which creates tables lazily in the
    # service test. For the API test we stub the service via dependency override.
    import uuid

    from vireonet.api.changes import get_service
    from vireonet.models import Change
    from vireonet.state_machine import ChangeState

    class StubService:
        async def create_from_intent(self, data, *, actor, emergency=False, now=None):  # type: ignore[no-untyped-def]
            return Change(
                id=uuid.uuid4(),
                ticket_number=data["ticket_number"],
                change_type=data["change_type"],
                state=ChangeState.VALIDATED,
                requester="alice",
                intent=data,
                devices=data["devices"],
            ), True

        async def create_from_servicenow(self, ticket, *, actor, sys_id=None):  # type: ignore[no-untyped-def]
            raise AssertionError("not used here")

        async def get(self, change_id):  # type: ignore[no-untyped-def]
            return None

    app = _client().app  # type: ignore[attr-defined]
    app.dependency_overrides[get_service] = lambda: StubService()
    client = TestClient(app)
    resp = client.post(
        "/changes",
        json={"intent": {**BASE, "change_type": "vlan_add", "vlan_id": 200, "vlan_name": "new"}},
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["state"] == "validated"
    assert resp.json()["ticket_number"] == "RITM0001234"


def test_post_changes_requires_intent_or_ticket() -> None:
    from vireonet.api.changes import get_service

    class StubService:  # type: ignore[no-untyped-class]
        async def create_from_intent(self, *a, **k):  # type: ignore[no-untyped-def]
            raise AssertionError

        async def create_from_servicenow(self, *a, **k):  # type: ignore[no-untyped-def]
            raise AssertionError

        async def get(self, *a, **k):  # type: ignore[no-untyped-def]
            return None

    app = _client().app  # type: ignore[attr-defined]
    app.dependency_overrides[get_service] = lambda: StubService()
    assert TestClient(app).post("/changes", json={}).status_code == 422


def test_webhook_hmac_rejected_with_wrong_signature() -> None:
    settings = Settings(
        adapter_mode="fake", webhook_secret="s3cret", database_url="sqlite+aiosqlite:///:memory:"
    )
    client = TestClient(create_app(settings))
    resp = client.post(
        "/changes",
        json={"intent": {**BASE, "change_type": "vlan_add", "vlan_id": 200, "vlan_name": "new"}},
        headers={"X-VireoNet-Signature": "deadbeef"},
    )
    assert resp.status_code == 401


def test_get_change_requires_viewer_role() -> None:
    from vireonet.api.changes import get_service

    class StubService:  # type: ignore[no-untyped-class]
        async def get(self, change_id):  # type: ignore[no-untyped-def]
            return None

    app = _client().app  # type: ignore[attr-defined]
    app.dependency_overrides[get_service] = lambda: StubService()
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="u1", username="test", role="viewer"
    )
    client = TestClient(app)
    assert client.get("/changes/does-not-exist").status_code == 404
    # Unknown role falls back to viewer, so it is not a 403; an operator-only
    # route would be. Viewer can read; the 404 proves auth passed and lookup ran.


def test_unknown_servicenow_ticket_is_a_404_not_a_500() -> None:
    from fastapi.testclient import TestClient

    from vireonet.config import Settings
    from vireonet.main import create_app

    app = create_app(
        Settings(adapter_mode="fake", dev=True, database_url="sqlite+aiosqlite:///:memory:")
    )
    r = TestClient(app).post("/changes", json={"ticket_number": "RITM-DOES-NOT-EXIST"})
    assert r.status_code == 404 and "not found" in r.json()["detail"]
