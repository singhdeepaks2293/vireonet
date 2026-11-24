"""Manual vs end-to-end automation, ITSM state triggers, planned windows."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from vireonet.adapters.fakes import FakeAwxAdapter, FakeNetBoxAdapter, FakeServiceNowAdapter
from vireonet.api.security import CurrentUser, get_current_user
from vireonet.config import Settings
from vireonet.db import Base
from vireonet.excel_push.window import parse_when
from vireonet.execution.fake_engine import FakeExecutionEngine
from vireonet.execution.fake_network import FakeNetwork
from vireonet.intent import parse_intent
from vireonet.main import create_app
from vireonet.models import AuditLog, Change
from vireonet.policy import PolicyEngine
from vireonet.state_machine import ChangeState
from vireonet.temporal.activities import ApplyInput, ChangeActivities, CheckInput
from vireonet.topology.demo import seed_demo_netbox

INTENT = {
    "change_type": "config_push",
    "devices": ["leaf-01"],
    "site": "dc1",
    "ticket_number": "CHG0039999",
    "requested_by": "carol",
    "configs": {"leaf-01": "vlan 310\n name auto"},
    "rollback_configs": {"leaf-01": "no vlan 310"},
}


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def test_parse_when_formats() -> None:
    assert parse_when("2026-11-24 01:00:00") == datetime(
        2026, 11, 24, 1, tzinfo=UTC
    )  # ServiceNow value
    assert parse_when("2026-11-24T01:00:00Z") == datetime(2026, 11, 24, 1, tzinfo=UTC)
    assert parse_when("2026-11-24T06:30:00+05:30") == datetime(2026, 11, 24, 1, tzinfo=UTC)
    assert parse_when("") is None and parse_when("not a date") is None


async def _acts(tmp_path, engine):  # type: ignore[no-untyped-def]
    db = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/a.db")
    async with db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(db, expire_on_commit=False)
    change_id = str(uuid.uuid4())
    async with factory() as s:
        s.add(
            Change(
                id=uuid.UUID(change_id),
                ticket_number="CHG0039999",
                change_type="config_push",
                state=ChangeState.VALIDATED,
                requester="carol",
                intent=INTENT,
                devices=["leaf-01"],
            )
        )
        await s.commit()
    netbox = FakeNetBoxAdapter()
    seed_demo_netbox(netbox)
    acts = ChangeActivities(
        factory,
        servicenow=FakeServiceNowAdapter(),
        netbox=netbox,
        awx=FakeAwxAdapter(),
        policy=PolicyEngine.default(),
        engine=engine,
    )
    return acts, change_id, factory, db


async def _run(acts, change_id, *, during=None, **inp):  # type: ignore[no-untyped-def]
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from vireonet.temporal.activities import all_activities
    from vireonet.temporal.workflow import ChangeWorkflow, ChangeWorkflowInput

    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        Worker(
            env.client,
            task_queue="auto-q",
            workflows=[ChangeWorkflow],
            activities=all_activities(acts),
        ),
    ):
        h = await env.client.start_workflow(
            ChangeWorkflow.run,
            ChangeWorkflowInput(
                change_id=change_id,
                ticket_number="CHG0039999",
                intent=INTENT,
                requester="carol",
                ci_timeout_seconds=60,
                approval_timeout_seconds=60,
                **inp,
            ),
            id=f"auto-{uuid.uuid4().hex[:6]}",
            task_queue="auto-q",
        )
        if during is not None:
            await during(h)
        return await h.result()


async def _reasons(factory, change_id):  # type: ignore[no-untyped-def]
    async with factory() as s:
        rows = (
            await s.execute(
                AuditLog.__table__.select().where(AuditLog.change_id == uuid.UUID(change_id))
            )
        ).fetchall()
    return {r.to_state: r.reason for r in rows}


async def test_fully_automatic_runs_end_to_end_without_any_signal(tmp_path) -> None:
    engine = FakeExecutionEngine(network=FakeNetwork())
    acts, cid, factory, db = await _acts(tmp_path, engine)
    now = datetime.now(UTC)
    result = await _run(
        acts,
        cid,
        skip_ci=True,
        pre_approved=True,
        approval_note="approved in servicenow: CHG0039999 entered state 'Implement'",
        window_start=iso(now - timedelta(minutes=1)),
        window_end=iso(now + timedelta(hours=1)),
    )
    assert result == "closed"
    reasons = await _reasons(factory, cid)
    assert reasons["ci_passed"].startswith("CI not applicable")
    assert "entered state 'Implement'" in reasons["approved"]
    assert any(c.step == "apply" for c in engine.calls)
    await db.dispose()


async def test_window_already_over_pushes_nothing(tmp_path) -> None:
    engine = FakeExecutionEngine(network=FakeNetwork())
    acts, cid, factory, db = await _acts(tmp_path, engine)
    now = datetime.now(UTC)
    result = await _run(
        acts,
        cid,
        skip_ci=True,
        pre_approved=True,
        window_start=iso(now - timedelta(hours=2)),
        window_end=iso(now - timedelta(minutes=1)),
    )
    assert result == "rejected"
    assert "planned window ended" in (await _reasons(factory, cid))["rejected"]
    assert engine.calls == []  # not even a backup: no device was touched
    await db.dispose()


async def test_waits_for_the_planned_start(tmp_path) -> None:
    from vireonet.temporal.workflow import ChangeWorkflow

    engine = FakeExecutionEngine(network=FakeNetwork())
    acts, cid, _factory, db = await _acts(tmp_path, engine)
    now = datetime.now(UTC)
    seen: dict[str, object] = {}

    async def during(h):  # type: ignore[no-untyped-def]
        for _ in range(100):
            phase = (await h.query(ChangeWorkflow.get_state))["phase"]
            if phase == "waiting_for_window":
                seen["waiting"] = True
                seen["calls_while_waiting"] = len(engine.calls)
                return
            await asyncio.sleep(0.05)

    result = await _run(
        acts,
        cid,
        during=during,
        skip_ci=True,
        pre_approved=True,
        window_start=iso(now + timedelta(seconds=4)),
        window_end=iso(now + timedelta(hours=1)),
    )
    assert result == "closed"
    assert seen == {"waiting": True, "calls_while_waiting": 0}
    await db.dispose()


async def test_abort_while_waiting_for_the_window(tmp_path) -> None:
    from vireonet.temporal.workflow import ChangeWorkflow

    engine = FakeExecutionEngine(network=FakeNetwork())
    acts, cid, _f, db = await _acts(tmp_path, engine)
    now = datetime.now(UTC)

    async def during(h):  # type: ignore[no-untyped-def]
        for _ in range(100):
            if (await h.query(ChangeWorkflow.get_state))["phase"] == "waiting_for_window":
                break
            await asyncio.sleep(0.05)
        await h.signal(ChangeWorkflow.reject, "cancelled in ITSM")

    result = await _run(
        acts,
        cid,
        during=during,
        skip_ci=True,
        pre_approved=True,
        window_start=iso(now + timedelta(hours=3)),
        window_end=iso(now + timedelta(hours=4)),
    )
    assert result == "rejected" and engine.calls == []
    await db.dispose()


async def test_apply_starts_no_wave_after_the_window_end(tmp_path) -> None:
    engine = FakeExecutionEngine(network=FakeNetwork())
    acts, cid, _f, db = await _acts(tmp_path, engine)
    await acts.pre_check_change(CheckInput(change_id=cid, intent=INTENT, kind="pre"))
    res = await acts.apply_change(
        ApplyInput(
            change_id=cid, intent=INTENT, window_end=iso(datetime.now(UTC) - timedelta(seconds=1))
        )
    )
    assert res["ok"] is False and "planned window ended" in res["failures"][0]
    assert not [c for c in engine.calls if c.step == "apply"]
    await db.dispose()


def test_policy_change_window_is_judged_at_the_planned_start() -> None:
    policy = PolicyEngine(
        change_window_days={"Mon", "Tue", "Wed", "Thu", "Fri"},
        change_window_start="01:00",
        change_window_end="05:00",
    )
    intent = parse_intent(INTENT)
    monday_2am = datetime(2026, 11, 23, 2, 0, tzinfo=UTC)
    monday_3pm = datetime(2026, 11, 23, 15, 0, tzinfo=UTC)
    assert policy.evaluate(intent, now=monday_2am).allowed
    assert not policy.evaluate(intent, now=monday_3pm).allowed


# --- API: triggers and modes -------------------------------------------------------------


def _client(tmp_path, role: str = "operator") -> TestClient:  # type: ignore[no-untyped-def]
    url = f"sqlite+aiosqlite:///{tmp_path}/api.db"

    async def init() -> None:
        e = create_async_engine(url)
        async with e.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        await e.dispose()

    asyncio.run(init())
    app = create_app(Settings(adapter_mode="fake", dev=True, database_url=url, policy_file="/none"))
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("u", "someone", role)
    return TestClient(app)


def test_state_webhook_triggers_only_in_trigger_states_and_inside_the_window(tmp_path) -> None:
    c = _client(tmp_path)
    ignored = c.post("/webhooks/servicenow", json={"number": "RITM0005001"}).json()
    assert ignored["status"] == "ignored" and "not a trigger state" in ignored["reason"]
    started = c.post("/webhooks/servicenow", json={"number": "CHG0030001"}).json()
    assert (
        started["status"] == "started"
        and started["automation"] == "full"
        and started["window"]["start"]
    )
    again = c.post("/webhooks/itsm/state", json={"ticket_number": "CHG0030001"}).json()
    assert again["status"] == "exists"
    waits = c.post("/webhooks/servicenow", json={"number": "CHG0030002"}).json()
    assert waits["status"] == "started"  # created now; the workflow waits for the start
    no_window = c.post("/webhooks/servicenow", json={"number": "CHG0030003"})
    assert no_window.status_code == 409 and "planned window" in no_window.json()["detail"]
    over = c.post("/webhooks/servicenow", json={"number": "CHG0030004"})
    assert over.status_code == 409 and "ended" in over.json()["detail"]


def test_fully_automatic_from_the_ui_needs_an_approver_and_a_window(tmp_path) -> None:
    import base64

    from vireonet.excel_push.workbook import demo_workbook

    b64 = base64.b64encode(demo_workbook()).decode()
    body = {
        "filename": "w.xlsx",
        "content_b64": b64,
        "ticket_number": "RITM0007001",
        "site": "dc1",
        "automation": "full",
    }
    assert _client(tmp_path, "operator").post("/config-push", json=body).status_code == 403
    approver = _client(tmp_path / "b" if False else tmp_path, "approver")
    assert approver.post("/config-push", json=body).status_code == 409  # no planned window
    now = datetime.now(UTC)
    ok = approver.post(
        "/config-push",
        json={**body, "planned_start": iso(now), "planned_end": iso(now + timedelta(hours=2))},
    ).json()
    assert ok["automation"] == "full" and ok["change"]["state"] == "validated"
    manual = approver.post(
        "/config-push", json={**body, "ticket_number": "RITM0007002", "automation": "manual"}
    ).json()
    assert manual["automation"] == "manual"
