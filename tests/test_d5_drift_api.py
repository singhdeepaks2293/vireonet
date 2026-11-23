"""D5: drift API — list/detail/summary/ignore-rules/actions + roles."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from vireonet.api.security import CurrentUser, get_current_user
from vireonet.config import Settings
from vireonet.diff import config_hash, normalize_config
from vireonet.drift.models import DriftIgnoreRule
from vireonet.main import create_app
from vireonet.reconciliation.drift import DriftStore

BASELINE = "hostname leaf-01\ninterface GigabitEthernet0/1\n description uplink\n"
CHANGED = "hostname leaf-01\ninterface GigabitEthernet0/1\n description CHANGED\n"


def _hash(text: str) -> str:
    return config_hash(normalize_config(text))


def _client(engine: AsyncEngine, role: str | None, username: str = "approver1") -> TestClient:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    app = create_app(
        Settings(adapter_mode="fake", dev=True, database_url="sqlite+aiosqlite:///:memory:"),
        session_factory=factory,
    )
    if role is not None:
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            user_id="u1", username=username, role=role
        )
    client = TestClient(app)
    client._factory = factory  # type: ignore[attr-defined]
    return client


async def _seed(client: TestClient) -> dict[str, str]:
    store = DriftStore(client._factory)  # type: ignore[attr-defined]
    from vireonet.config_store.store import ConfigStore

    config_store = ConfigStore(client._factory)  # type: ignore[attr-defined]
    for device in ("dc1-leaf-01", "dc1-leaf-02", "dc2-leaf-01"):
        await config_store.store_running(device, BASELINE, ticket_number="RITM-SEED")
    ids: dict[str, str] = {}
    ids["high"] = await store.create(
        "dc1-leaf-01", "unmanaged", "high", "diff-high", _hash(BASELINE), _hash(CHANGED), None
    )
    ids["crit"] = await store.create(
        "dc1-leaf-02",
        "intent",
        "critical",
        "diff-crit",
        _hash(BASELINE),
        _hash(CHANGED),
        "RITM0001234",
        attributed_actor="alice",
    )
    ids["low"] = await store.create(
        "dc2-leaf-01", "state", "low", "diff-low", _hash(BASELINE), _hash(CHANGED), None
    )
    scan_id = await store.create_scan("dc1")
    await store.finish_scan(
        scan_id, status="completed", devices_scanned=2, devices_with_drift=2, devices_failed=0
    )
    await store.record_decision(
        ids["high"], action="rollback", actor="approver1", reason="r", ticket_ref="RITM1"
    )
    return ids


# -- OpenAPI ------------------------------------------------------------------


def test_all_drift_endpoints_exist_in_openapi(engine: AsyncEngine) -> None:
    client = _client(engine, "admin")
    paths = client.get("/openapi.json").json()["paths"]
    for path in (
        "/drift",
        "/drift/summary",
        "/drift/ignore-rules",
        "/drift/{record_id}",
        "/drift/{record_id}/rollback",
        "/drift/{record_id}/merge",
        "/drift/{record_id}/exception",
        "/drift/{record_id}/emergency-accept",
        "/drift/{record_id}/mr-merged",
        "/drift/scan",
        "/drift/events",
    ):
        assert path in paths, f"missing OpenAPI path {path}"


# -- auth / roles ---------------------------------------------------------------


async def test_list_requires_token_and_roles(engine: AsyncEngine) -> None:
    anon = _client(engine, None)
    assert anon.get("/drift").status_code == 401
    viewer = _client(engine, "viewer")
    assert viewer.get("/drift").status_code == 200
    # Rollback is approver+; viewer and operator are refused.
    ids = await _seed(viewer)
    assert (
        viewer.post(
            f"/drift/{ids['high']}/rollback",
            json={"reason": "r", "ticket_ref": "RITM1"},
        ).status_code
        == 403
    )
    operator = _client(engine, "operator")
    assert (
        operator.post(
            f"/drift/{ids['high']}/rollback",
            json={"reason": "r", "ticket_ref": "RITM1"},
        ).status_code
        == 403
    )


async def test_ignore_rules_admin_only(engine: AsyncEngine) -> None:
    operator = _client(engine, "operator")
    assert operator.get("/drift/ignore-rules").status_code == 403
    assert operator.put("/drift/ignore-rules", json={"rules": []}).status_code == 403
    admin = _client(engine, "admin")
    assert admin.get("/drift/ignore-rules").status_code == 200


# -- list / filters -------------------------------------------------------------


async def test_list_filters_and_pagination(engine: AsyncEngine) -> None:
    client = _client(engine, "viewer")
    ids = await _seed(client)
    body = client.get("/drift").json()
    assert body["total"] == 3
    assert {r["device"] for r in body["items"]} == {"dc1-leaf-01", "dc1-leaf-02", "dc2-leaf-01"}

    assert client.get("/drift", params={"device": "dc1-leaf-01"}).json()["total"] == 1
    assert client.get("/drift", params={"severity": "critical"}).json()["total"] == 1
    assert client.get("/drift", params={"kind": "state"}).json()["total"] == 1
    assert client.get("/drift", params={"status": "detected"}).json()["total"] == 3
    assert client.get("/drift", params={"site": "dc1"}).json()["total"] == 2
    assert client.get("/drift", params={"site": "dc2"}).json()["total"] == 1
    page = client.get("/drift", params={"limit": 2, "offset": 2}).json()
    assert page["total"] == 3 and len(page["items"]) == 1
    assert page["limit"] == 2 and page["offset"] == 2
    assert ids["high"]  # seeded ids are real


async def test_detail_returns_record_decisions_and_audit(engine: AsyncEngine) -> None:
    client = _client(engine, "viewer")
    ids = await _seed(client)
    resp = client.get(f"/drift/{ids['high']}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["record"]["device"] == "dc1-leaf-01"
    assert body["record"]["severity"] == "high"
    assert body["record"]["running_hash"] == _hash(CHANGED)
    assert len(body["decisions"]) == 1
    assert body["decisions"][0]["action"] == "rollback"
    assert isinstance(body["audit"], list)
    assert client.get("/drift/not-a-uuid").status_code == 404


async def test_summary_shape(engine: AsyncEngine) -> None:
    client = _client(engine, "viewer")
    await _seed(client)
    body = client.get("/drift/summary").json()
    assert body["by_severity"] == {"high": 1, "critical": 1, "low": 1}
    assert body["by_kind"] == {"unmanaged": 1, "intent": 1, "state": 1}
    assert body["by_status"] == {"detected": 3}
    assert body["total_open"] == 3
    assert len(body["last_scan_per_site"]) == 1
    scan = body["last_scan_per_site"][0]
    assert scan["site"] == "dc1" and scan["status"] == "completed"
    assert scan["devices_with_drift"] == 2
    assert set(body["scanned_devices"]) == {"dc1-leaf-01", "dc1-leaf-02", "dc2-leaf-01"}
    assert isinstance(body["never_scanned_devices"], list)
    assert isinstance(body["never_scanned_best_effort"], bool)


# -- ignore rules ---------------------------------------------------------------


async def test_ignore_rules_replace_and_reject_bad_regex(engine: AsyncEngine) -> None:
    client = _client(engine, "admin")
    resp = client.put(
        "/drift/ignore-rules",
        json={
            "rules": [{"pattern": "uptime is", "platform": "*", "scope": "global", "target": ""}]
        },
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 1
    assert resp.json()[0]["pattern"] == "uptime is"
    assert resp.json()[0]["created_by"] == "approver1"
    bad = client.put("/drift/ignore-rules", json={"rules": [{"pattern": "([bad"}]})
    assert bad.status_code == 422
    # The failed replacement must not have wiped the good rules.
    assert len(client.get("/drift/ignore-rules").json()) == 1
    empty = client.put("/drift/ignore-rules", json={"rules": []})
    assert empty.status_code == 200 and empty.json() == []


# -- actions --------------------------------------------------------------------


async def test_rollback_requires_reason_and_ticket(engine: AsyncEngine) -> None:
    client = _client(engine, "approver")
    ids = await _seed(client)
    assert (
        client.post(
            f"/drift/{ids['high']}/rollback", json={"reason": "", "ticket_ref": "RITM1"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/drift/{ids['high']}/rollback", json={"reason": "r", "ticket_ref": ""}
        ).status_code
        == 422
    )


async def test_rollback_runs_with_fake_engine_and_stays_open_unconfirmed(
    engine: AsyncEngine,
) -> None:
    # The fake engine "applies" nothing and the fake collect returns no
    # config, so confirm_rollback cannot confirm: the record ends
    # under_review, not resolved — the honest outcome for this wiring.
    client = _client(engine, "approver")
    ids = await _seed(client)
    resp = client.post(
        f"/drift/{ids['high']}/rollback",
        json={"reason": "restore baseline", "ticket_ref": "RITM0001234"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "unconfirmed"
    assert body["change_ticket"].startswith("DRIFT-RB-")
    detail = client.get(f"/drift/{ids['high']}").json()
    assert detail["record"]["status"] == "under_review"
    assert detail["record"]["linked_ticket"] == body["change_ticket"]


async def test_self_approval_blocked_on_exception_and_merge(engine: AsyncEngine) -> None:
    client = _client(engine, "approver", username="alice")
    ids = await _seed(client)
    expiry = (datetime.now(UTC) + timedelta(days=7)).isoformat()
    resp = client.post(
        f"/drift/{ids['crit']}/exception",
        json={
            "reason": "mine",
            "ticket_ref": "RITM1",
            "owner": "alice",
            "expiry": expiry,
        },
    )
    assert resp.status_code == 403, resp.text
    assert (
        client.post(
            f"/drift/{ids['crit']}/merge",
            json={"reason": "mine", "ticket_ref": "RITM1"},
        ).status_code
        == 403
    )


async def test_exception_grants_and_emergency_accepts(engine: AsyncEngine) -> None:
    approver = _client(engine, "approver")
    ids = await _seed(approver)
    expiry = (datetime.now(UTC) + timedelta(days=7)).isoformat()
    resp = approver.post(
        f"/drift/{ids['high']}/exception",
        json={
            "reason": "known good",
            "ticket_ref": "RITM0001234",
            "owner": "netops",
            "expiry": expiry,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "exception_active"
    detail = approver.get(f"/drift/{ids['high']}").json()
    assert detail["record"]["status"] == "exception_active"

    operator = _client(engine, "operator")
    resp = operator.post(
        f"/drift/{ids['low']}/emergency-accept",
        json={"reason": "outage", "ticket_ref": "INC1", "deadline_hours": 24},
    )
    assert resp.status_code == 200, resp.text
    assert "deadline" in resp.json()
    viewer = _client(engine, "viewer")
    assert (
        viewer.post(
            f"/drift/{ids['low']}/emergency-accept",
            json={"reason": "x", "ticket_ref": "INC1"},
        ).status_code
        == 403
    )


async def test_merge_without_git_configured_returns_503(engine: AsyncEngine) -> None:
    client = _client(engine, "approver")
    ids = await _seed(client)
    resp = client.post(
        f"/drift/{ids['high']}/merge",
        json={"reason": "keep it", "ticket_ref": "RITM1"},
    )
    assert resp.status_code == 503, resp.text


async def test_mr_merged_endpoint_validates_branch(engine: AsyncEngine) -> None:
    client = _client(engine, "operator")
    ids = await _seed(client)
    assert client.post(f"/drift/{ids['high']}/mr-merged", json={"merged": True}).status_code == 422
    assert (
        client.post(
            f"/drift/{ids['high']}/mr-merged",
            json={"merged": True, "branch": "drift/other/00000000"},
        ).status_code
        == 422
    )


async def test_scan_endpoint_still_works(engine: AsyncEngine) -> None:
    client = _client(engine, "operator")
    resp = client.post("/drift/scan", json={"site": "dc1"})
    assert resp.status_code == 202, resp.text
    assert resp.json()["workflow_id"].startswith("drift-scan-dc1-")


def test_ignore_rule_model_importable() -> None:
    # Guard against a refactor dropping the table the API queries.
    assert DriftIgnoreRule.__tablename__ == "drift_ignore_rules"
