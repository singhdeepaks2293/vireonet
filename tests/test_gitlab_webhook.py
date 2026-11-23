from __future__ import annotations

from fastapi.testclient import TestClient

from vireonet.config import Settings
from vireonet.main import create_app
from vireonet.temporal.client import FakeTemporalClient


def _client():  # type: ignore[no-untyped-def]
    temporal = FakeTemporalClient()
    app = create_app(
        Settings(
            adapter_mode="fake",
            gitlab_webhook_token="gl-secret",
            database_url="sqlite+aiosqlite:///:memory:",
        ),
        temporal_client=temporal,
    )
    return TestClient(app), temporal


def test_gitlab_pipeline_webhook_signals_ci() -> None:
    client, temporal = _client()
    # Ticket is carried as a pipeline variable in this design (see webhooks.py).
    payload = {
        "object_kind": "pipeline",
        "object_attributes": {"status": "success", "variables": [{"value": "RITM1"}]},
    }
    resp = client.post("/webhooks/gitlab", json=payload, headers={"X-Gitlab-Token": "gl-secret"})
    assert resp.status_code == 200, resp.text
    assert ("RITM1", "ci_passed", "pipeline success") in temporal.signals


def test_gitlab_webhook_bad_token_401() -> None:
    client, _ = _client()
    resp = client.post(
        "/webhooks/gitlab", json={"object_kind": "pipeline"}, headers={"X-Gitlab-Token": "wrong"}
    )
    assert resp.status_code == 401


def test_gitlab_merge_webhook_signals_merge() -> None:
    client, temporal = _client()
    payload = {
        "object_kind": "merge_request",
        "object_attributes": {"state": "merged", "title": "RITM1: Add VLAN"},
    }
    resp = client.post("/webhooks/gitlab", json=payload, headers={"X-Gitlab-Token": "gl-secret"})
    assert resp.status_code == 200
    assert ("RITM1", "merge_completed", "") in temporal.signals
