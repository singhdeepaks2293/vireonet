from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from vireonet.api.changes import get_service
from vireonet.api.security import CurrentUser, get_current_user
from vireonet.config import Settings
from vireonet.main import create_app
from vireonet.models import Change
from vireonet.state_machine import ChangeState
from vireonet.temporal.client import FakeTemporalClient


def _app_with_fake_temporal():  # type: ignore[no-untyped-def]
    temporal = FakeTemporalClient()
    change_id = str(uuid.uuid4())
    change = Change(id=uuid.UUID(change_id), ticket_number="RITM0001234", change_type="vlan_add", state=ChangeState.VALIDATED, requester="alice", intent={}, devices=["leaf-01"])

    class StubService:
        async def get(self, cid):  # type: ignore[no-untyped-def]
            return change if cid == change_id else None
        async def create_from_intent(self, *a, **k):  # type: ignore[no-untyped-def]
            raise AssertionError
        async def create_from_servicenow(self, *a, **k):  # type: ignore[no-untyped-def]
            raise AssertionError

    app = create_app(Settings(adapter_mode="fake", database_url="sqlite+aiosqlite:///:memory:"), temporal_client=temporal)
    app.dependency_overrides[get_service] = lambda: StubService()
    return app, temporal, change_id


def _as_role(app, role):  # type: ignore[no-untyped-def]
    # Post-M9 the role comes from the signed JWT; tests stub the auth
    # dependency directly instead of sending the retired X-VireoNet-Role header.
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id="u1", username="test", role=role)


def test_approval_signal_requires_approver_and_records() -> None:
    app, temporal, change_id = _app_with_fake_temporal()
    client = TestClient(app)
    # Viewer cannot approve.
    _as_role(app, "viewer")
    assert client.post(f"/changes/{change_id}/signals/approval", json={"approved": True}).status_code == 403
    _as_role(app, "approver")
    resp = client.post(f"/changes/{change_id}/signals/approval", json={"approved": True, "reason": "ok"})
    assert resp.status_code == 202, resp.text
    assert ("RITM0001234", "approve", "ok") in temporal.signals


def test_ci_signal_accepted_via_webhook_auth() -> None:
    app, temporal, change_id = _app_with_fake_temporal()
    client = TestClient(app)
    resp = client.post(f"/changes/{change_id}/signals/ci", json={"passed": True})
    assert resp.status_code == 202, resp.text
    assert ("RITM0001234", "ci_passed", "") in temporal.signals


def test_signal_unknown_change_404() -> None:
    app, _, _ = _app_with_fake_temporal()
    _as_role(app, "approver")
    client = TestClient(app)
    assert client.post("/changes/nope/signals/approval", json={"approved": True}).status_code == 404
