"""D3: schedules, debounce, change-in-progress suppression, API, metrics."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from prometheus_client import generate_latest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from vireonet.api.security import CurrentUser, get_current_user
from vireonet.config import Settings
from vireonet.drift.activities import (
    DriftActivities,
    DriftCollectInput,
    DriftStoreFindingsInput,
)
from vireonet.drift.attribution import UnknownAttribution, link_to_change
from vireonet.drift.detect import DriftFinding
from vireonet.drift.events import DebouncedScanner, verify_event_token
from vireonet.drift.metrics import record_drift_record
from vireonet.drift.schedules import build_drift_schedules
from vireonet.execution.types import StepResult, StepStatus
from vireonet.main import create_app
from vireonet.models import Change
from vireonet.reconciliation.drift import DriftStore
from vireonet.state_machine import ChangeState

BASELINE = "hostname leaf-01\ninterface GigabitEthernet0/1\n description uplink\n"
RUNNING_CHANGED = "hostname leaf-01\ninterface GigabitEthernet0/1\n description CHANGED\n"


# -- schedules ------------------------------------------------------------------


def test_build_drift_schedules_one_per_site() -> None:
    settings = Settings(
        adapter_mode="fake",
        drift_sites=["dc1", "dc2"],
        drift_cron="0 2 * * *",
        database_url="sqlite+aiosqlite:///:memory:",
    )
    specs = build_drift_schedules(settings)
    assert [s.site for s in specs] == ["dc1", "dc2"]
    assert [s.name for s in specs] == ["drift-scan-dc1", "drift-scan-dc2"]
    assert all(s.cron == "0 2 * * *" for s in specs)
    assert all(0 <= s.jitter_seconds < 300 for s in specs)
    assert all(s.max_parallel == 5 for s in specs)
    # Jitter is deterministic per site.
    assert build_drift_schedules(settings)[0].jitter_seconds == specs[0].jitter_seconds


def test_build_drift_schedules_rejects_bad_cron() -> None:
    settings = Settings(
        adapter_mode="fake",
        drift_sites=["dc1"],
        drift_cron="not a cron",
        database_url="sqlite+aiosqlite:///:memory:",
    )
    with pytest.raises(ValueError, match="invalid drift cron"):
        build_drift_schedules(settings)


# -- debounce -------------------------------------------------------------------


def test_debounce_collapses_repeated_events() -> None:
    scanner = DebouncedScanner()
    now = datetime.now(UTC)
    assert scanner.should_scan("leaf-01", now) is True
    assert scanner.should_scan("leaf-01", now + timedelta(seconds=30)) is False
    assert scanner.should_scan("leaf-01", now + timedelta(seconds=60)) is False
    assert scanner.should_scan("leaf-02", now + timedelta(seconds=60)) is True
    assert scanner.should_scan("leaf-01", now + timedelta(minutes=6)) is True


def test_verify_event_token() -> None:
    body = b'{"device": "leaf-01"}'
    headers = {"X-VireoNet-Event-Token": "s3cret"}
    verify_event_token(headers, body, "s3cret")
    sig = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    verify_event_token({"X-VireoNet-Signature": f"sha256={sig}"}, body, "s3cret")
    with pytest.raises(Exception, match="401"):
        verify_event_token({"X-VireoNet-Event-Token": "wrong"}, body, "s3cret")
    with pytest.raises(Exception, match="401"):
        verify_event_token({}, body, "s3cret")
    with pytest.raises(Exception, match="401"):
        verify_event_token(headers, body, "")  # no secret configured: fail closed


# -- attribution ----------------------------------------------------------------


async def test_unknown_attribution_and_link_to_change() -> None:
    assert await UnknownAttribution().attribute("leaf-01", "hostname leaf-01\n") == {
        "actor": "unknown",
        "at": None,
    }
    changes = [
        {"ticket_number": "RITM1", "devices": ["leaf-01"]},
        {"ticket_number": "RITM2", "devices": ["leaf-02"]},
    ]
    assert link_to_change("leaf-01", "RITM1", changes) is True
    assert link_to_change("leaf-01", "RITM2", changes) is False
    assert link_to_change("leaf-02", "RITM1", changes) is False


# -- change-in-progress suppression ----------------------------------------------


async def test_change_in_progress_suppresses_new_record(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        session.add(
            Change(
                ticket_number="RITM1",
                change_type="vlan_add",
                state=ChangeState.APPLIED,
                requester="alice",
                intent={},
                devices=["leaf-01"],
            )
        )
        await session.commit()
    activities = DriftActivities(factory, drift_store=DriftStore(factory))
    finding = DriftFinding(
        kind="unmanaged",
        severity="high",
        diff="x",
        section="interfaces",
        detail={"running_hash": "abc", "baseline_hash": "def"},
    )
    result = await activities.store_findings(
        DriftStoreFindingsInput(
            device="leaf-01", findings=[finding.model_dump(mode="json")], ticket="DRIFT-1"
        )
    )
    assert result["stored"] == []
    assert result["linked"] == [
        {"dedup_key": "leaf-01|unmanaged|abc", "ticket": "RITM1", "kind": "unmanaged"}
    ]
    records = await DriftStore(factory).list_records(device="leaf-01")
    assert records == []


async def test_no_active_change_stores_record(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    activities = DriftActivities(factory, drift_store=DriftStore(factory))
    finding = DriftFinding(
        kind="unmanaged",
        severity="high",
        diff="x",
        section="interfaces",
        detail={"running_hash": "abc", "baseline_hash": "def"},
    )
    result = await activities.store_findings(
        DriftStoreFindingsInput(
            device="leaf-01", findings=[finding.model_dump(mode="json")], ticket="DRIFT-1"
        )
    )
    assert len(result["stored"]) == 1
    # A repeat finding dedups: last_seen is touched, no second record.
    result2 = await activities.store_findings(
        DriftStoreFindingsInput(
            device="leaf-01", findings=[finding.model_dump(mode="json")], ticket="DRIFT-1"
        )
    )
    assert result2["stored"] == []
    assert result2["updated"] == result["stored"]
    records = await DriftStore(factory).list_records(device="leaf-01")
    assert len(records) == 1


# -- collector failure is per-device, not a crash ---------------------------------


class _FailingEngine:
    async def collect_state(self, req: object) -> StepResult:
        return StepResult(
            status=StepStatus.FAILED, step="collect_state", device="leaf-01", job_id=1
        )


class _OkEngine:
    async def collect_state(self, req: object) -> StepResult:
        return StepResult(
            status=StepStatus.SUCCESSFUL,
            step="collect_state",
            device="leaf-02",
            job_id=2,
            output={"running_config": RUNNING_CHANGED},
        )


async def test_unreachable_device_recorded_as_failure_not_crash(
    engine: AsyncEngine,
) -> None:
    from vireonet.drift.collector import DriftCollector
    from vireonet.safety import DeviceLockService

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    locks = DeviceLockService(factory)
    activities = DriftActivities(
        factory,
        drift_store=DriftStore(factory),
        collector=DriftCollector(_FailingEngine(), locks),
    )
    result = await activities.collect_device(
        DriftCollectInput(device="leaf-01", platform="ios", ticket="DRIFT-1")
    )
    assert result["ok"] is False
    assert "error" in result

    # collect_many: one device fails, the other still collects.
    from vireonet.drift.collector import DriftCollector as _C

    class _MixedEngine:
        def __init__(self) -> None:
            self._ok = _OkEngine()
            self._bad = _FailingEngine()

        async def collect_state(self, req: object) -> StepResult:
            device = req.device.name  # type: ignore[attr-defined]
            if device == "leaf-01":
                return await self._bad.collect_state(req)
            return await self._ok.collect_state(req)

    mixed = _C(_MixedEngine(), locks)
    out = await mixed.collect_many(
        [
            {"device": "leaf-01", "platform": "ios"},
            {"device": "leaf-02", "platform": "ios"},
        ],
        "DRIFT-1",
    )
    assert isinstance(out["leaf-01"], Exception)
    assert out["leaf-02"] == RUNNING_CHANGED


# -- metrics ----------------------------------------------------------------------


def test_metrics_counters_increment() -> None:
    before = generate_latest().decode()
    record_drift_record("high", "unmanaged")
    after = generate_latest().decode()
    assert 'vireonet_drift_records_total{kind="unmanaged",severity="high"}' in after

    # The counter value grew by exactly one.
    def _value(text: str) -> float:
        for line in text.splitlines():
            if line.startswith('vireonet_drift_records_total{kind="unmanaged",severity="high"}'):
                return float(line.rsplit(" ", 1)[-1])
        return 0.0

    assert _value(after) == _value(before) + 1.0


# -- API --------------------------------------------------------------------------


def _app_with_secret(secret: str = "") -> TestClient:
    settings = Settings(
        adapter_mode="fake",
        dev=True,
        drift_event_secret=secret,
        database_url="sqlite+aiosqlite:///:memory:",
    )
    app = create_app(settings)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="u1", username="op", role="operator"
    )
    return TestClient(app)


def test_drift_scan_starts_workflow() -> None:
    client = _app_with_secret()
    resp = client.post("/drift/scan", json={"site": "dc1"})
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["workflow_id"].startswith("drift-scan-dc1-")
    temporal = client.app.state.temporal_client  # type: ignore[attr-defined]
    assert len(temporal.drift_scans) == 1
    assert temporal.drift_scans[0].site == "dc1"


def test_drift_events_fail_closed_without_secret() -> None:
    client = _app_with_secret("")
    resp = client.post("/drift/events", json={"device": "leaf-01", "message": "config change"})
    assert resp.status_code == 401


def test_drift_events_debounced() -> None:
    client = _app_with_secret("s3cret")
    headers = {"X-VireoNet-Event-Token": "s3cret"}
    first = client.post(
        "/drift/events", json={"device": "leaf-01", "message": "x"}, headers=headers
    )
    assert first.status_code == 202, first.text
    assert first.json()["debounced"] is False
    second = client.post(
        "/drift/events", json={"device": "leaf-01", "message": "x"}, headers=headers
    )
    assert second.status_code == 202
    assert second.json()["debounced"] is True
    temporal = client.app.state.temporal_client  # type: ignore[attr-defined]
    assert len(temporal.drift_scans) == 1


def test_drift_events_hmac_signature() -> None:
    client = _app_with_secret("s3cret")
    payload = {"device": "leaf-02", "message": "x"}
    body = json.dumps(payload).encode()
    sig = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    resp = client.post(
        "/drift/events",
        content=body,
        headers={"X-VireoNet-Signature": f"sha256={sig}", "Content-Type": "application/json"},
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["debounced"] is False
