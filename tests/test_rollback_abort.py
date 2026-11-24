"""Operator abort, manual rollback, durable backups, honest rollback results.

Workflow tests use the Temporal time-skipping test server (see
test_temporal_workflow.py for the download caveat).
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from vireonet.adapters.fakes import FakeAwxAdapter, FakeNetBoxAdapter, FakeServiceNowAdapter
from vireonet.adapters.protocols import AwxJob, NetBoxVlan
from vireonet.api.changes import get_service
from vireonet.api.security import CurrentUser, get_current_user
from vireonet.config import Settings
from vireonet.db import Base
from vireonet.main import create_app
from vireonet.models import AuditLog, Change
from vireonet.policy import PolicyEngine
from vireonet.safety import DeviceLockService
from vireonet.state_machine import ChangeState
from vireonet.temporal.activities import (
    ChangeActivities,
    CheckInput,
    RollbackInput,
    all_activities,
)
from vireonet.temporal.client import FakeTemporalClient
from vireonet.temporal.workflow import ChangeWorkflow, ChangeWorkflowInput

TICKET = "RITM0009999"
INTENT = {
    "change_type": "vlan_add",
    "vlan_id": 200,
    "vlan_name": "new",
    "devices": ["leaf-01"],
    "site": "dc1",
    "ticket_number": TICKET,
    "requested_by": "alice",
}


class FailingAwx(FakeAwxAdapter):
    """Fake AWX whose jobs for the given templates finish as 'failed'."""

    def __init__(self, failing: set[str]) -> None:
        super().__init__()
        self._failing = failing

    async def launch_job(
        self, template: str, extra_vars: dict[str, str], limit: str | None = None
    ) -> AwxJob:
        job = await super().launch_job(template, extra_vars, limit)
        if template in self._failing:
            job.status = "failed"
        return job


async def _db(tmp_path, state: ChangeState = ChangeState.VALIDATED):  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    change_id = str(uuid.uuid4())
    async with factory() as s:
        s.add(
            Change(
                id=uuid.UUID(change_id),
                ticket_number=TICKET,
                change_type="vlan_add",
                state=state,
                requester="alice",
                intent=INTENT,
                devices=["leaf-01"],
            )
        )
        await s.commit()
    return factory, change_id, engine


def _acts(factory, awx=None):  # type: ignore[no-untyped-def]
    # VLAN 200 is in NetBox (source of truth), so post-check passes unless
    # a test makes AWX fail on purpose.
    netbox = FakeNetBoxAdapter()
    netbox.vlans[200] = NetBoxVlan(200, "new", "dc1")
    return ChangeActivities(
        factory,
        servicenow=FakeServiceNowAdapter(),
        netbox=netbox,
        awx=awx or FakeAwxAdapter(),
        policy=PolicyEngine.default(),
    )


async def _state(factory, change_id: str) -> str:  # type: ignore[no-untyped-def]
    async with factory() as s:
        change = await s.get(Change, uuid.UUID(change_id))
        assert change is not None
        return str(change.state)


def _wf_input(change_id: str, soak: int = 0) -> ChangeWorkflowInput:
    return ChangeWorkflowInput(
        change_id=change_id,
        ticket_number=TICKET,
        intent=INTENT,
        requester="alice",
        ci_timeout_seconds=60,
        approval_timeout_seconds=60,
        soak_seconds=soak,
    )


async def _run(acts, change_id, *, soak=0, before_result=None):  # type: ignore[no-untyped-def]
    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        Worker(
            env.client,
            task_queue="test-q",
            workflows=[ChangeWorkflow],
            activities=all_activities(acts),
        ),
    ):
        handle = await env.client.start_workflow(
            ChangeWorkflow.run, _wf_input(change_id, soak), id=TICKET, task_queue="test-q"
        )
        if before_result is not None:
            await before_result(handle)
        return await handle.result()


async def _approve_all(handle) -> None:  # type: ignore[no-untyped-def]
    await handle.signal(ChangeWorkflow.ci_passed)
    await handle.signal(ChangeWorkflow.merge_completed)
    await handle.signal(ChangeWorkflow.approve, "ok")


# --- Activities: durable backups --------------------------------------------


async def test_rollback_uses_backups_from_input_on_a_fresh_worker(tmp_path) -> None:
    factory, change_id, engine = await _db(tmp_path, ChangeState.PRE_CHECKED)
    pre = await _acts(factory).pre_check_change(
        CheckInput(change_id=change_id, intent=INTENT, kind="pre")
    )
    assert pre["ok"], pre
    backups = pre["evidence"]["backups"]
    assert backups
    # A different worker (fresh instance, empty memory) can still roll back.
    other = _acts(factory)
    rb = await other.rollback_change(
        RollbackInput(change_id=change_id, intent=INTENT, backups=backups)
    )
    assert rb["ok"], rb
    assert await DeviceLockService(factory).is_locked("leaf-01") is None
    await engine.dispose()


async def test_rollback_without_backups_reports_failure(tmp_path) -> None:
    factory, change_id, engine = await _db(tmp_path, ChangeState.APPLIED)
    rb = await _acts(factory).rollback_change(RollbackInput(change_id=change_id, intent=INTENT))
    assert rb["ok"] is False
    assert "no pre-change backups" in rb["failures"][0]
    await engine.dispose()


async def test_failed_awx_rollback_reports_failure_and_keeps_locks(tmp_path) -> None:
    factory, change_id, engine = await _db(tmp_path, ChangeState.PRE_CHECKED)
    acts = _acts(factory, FailingAwx({"rollback"}))
    pre = await acts.pre_check_change(CheckInput(change_id=change_id, intent=INTENT, kind="pre"))
    assert pre["ok"], pre
    rb = await acts.rollback_change(
        RollbackInput(change_id=change_id, intent=INTENT, backups=pre["evidence"]["backups"])
    )
    assert rb["ok"] is False
    assert await DeviceLockService(factory).is_locked("leaf-01") == TICKET
    await engine.dispose()


# --- Workflow ----------------------------------------------------------------


async def test_apply_failure_rolls_back_for_real(tmp_path) -> None:
    factory, change_id, engine = await _db(tmp_path)
    awx = FailingAwx({"apply"})
    result = await _run(_acts(factory, awx), change_id, before_result=_approve_all)
    assert result == "rolled_back"
    assert await _state(factory, change_id) == "rolled_back"
    rollback_jobs = [j for j in awx.jobs.values() if j.template == "rollback"]
    assert len(rollback_jobs) == 1
    # The workflow carried the backup ids into the rollback job.
    assert rollback_jobs[0].extra_vars["backups"] not in ("", "{}")
    await engine.dispose()


async def test_failed_rollback_is_never_reported_as_rolled_back(tmp_path) -> None:
    factory, change_id, engine = await _db(tmp_path)
    awx = FailingAwx({"apply", "rollback"})
    result = await _run(_acts(factory, awx), change_id, before_result=_approve_all)
    assert result == "rollback_failed"
    assert await _state(factory, change_id) == "rollback_failed"
    await engine.dispose()


async def test_abort_while_waiting_for_ci_rejects_without_touching_devices(tmp_path) -> None:
    factory, change_id, engine = await _db(tmp_path)
    awx = FakeAwxAdapter()

    async def abort(handle) -> None:  # type: ignore[no-untyped-def]
        await handle.signal(ChangeWorkflow.reject, "operator abort")

    result = await _run(_acts(factory, awx), change_id, before_result=abort)
    assert result == "rejected"
    assert await _state(factory, change_id) == "rejected"
    assert awx.jobs == {}
    await engine.dispose()


async def test_manual_rollback_during_soak(tmp_path) -> None:
    factory, change_id, engine = await _db(tmp_path)
    awx = FakeAwxAdapter()

    async def approve_then_rollback(handle) -> None:  # type: ignore[no-untyped-def]
        await _approve_all(handle)
        for _ in range(200):
            if (await handle.query(ChangeWorkflow.get_state))["phase"] == "soak":
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("workflow never reached the soak phase")
        await handle.signal(ChangeWorkflow.request_rollback, "network looks wrong")

    result = await _run(
        _acts(factory, awx), change_id, soak=3600, before_result=approve_then_rollback
    )
    assert result == "rolled_back"
    assert any(j.template == "rollback" for j in awx.jobs.values())
    async with factory() as s:
        rows = (await s.execute(AuditLog.__table__.select())).fetchall()
    assert any(r.to_state == "rolled_back" and r.evidence.get("manual") for r in rows)
    await engine.dispose()


# --- API ---------------------------------------------------------------------


def _api(state: ChangeState):  # type: ignore[no-untyped-def]
    temporal = FakeTemporalClient()
    change_id = str(uuid.uuid4())
    change = Change(
        id=uuid.UUID(change_id),
        ticket_number=TICKET,
        change_type="vlan_add",
        state=state,
        requester="alice",
        intent={},
        devices=["leaf-01"],
    )

    class StubService:
        async def get(self, cid):  # type: ignore[no-untyped-def]
            return change if cid == change_id else None

    app = create_app(
        Settings(adapter_mode="fake", dev=True, database_url="sqlite+aiosqlite:///:memory:"),
        temporal_client=temporal,
    )
    app.dependency_overrides[get_service] = lambda: StubService()
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="u1", username="op1", role="operator"
    )
    return TestClient(app), temporal, change_id


def test_operator_can_abort_before_apply() -> None:
    client, temporal, cid = _api(ChangeState.PRE_CHECKED)
    resp = client.post(f"/changes/{cid}/signals/abort", json={"reason": "wrong vlan"})
    assert resp.status_code == 202, resp.text
    assert temporal.signals == [(TICKET, "reject", "wrong vlan (by op1)")]


def test_abort_after_apply_is_refused() -> None:
    client, temporal, cid = _api(ChangeState.APPLIED)
    assert client.post(f"/changes/{cid}/signals/abort").status_code == 409
    assert temporal.signals == []


def test_rollback_signals_the_workflow_and_writes_no_state() -> None:
    client, temporal, cid = _api(ChangeState.POST_CHECKED)
    resp = client.post(f"/changes/{cid}/signals/rollback", json={})
    assert resp.status_code == 202, resp.text
    assert resp.json()["state"] == "post_checked"
    assert temporal.signals[0][:2] == (TICKET, "request_rollback")


def test_rollback_refused_outside_apply_window() -> None:
    client, temporal, cid = _api(ChangeState.CLOSED)
    assert client.post(f"/changes/{cid}/signals/rollback").status_code == 409
    assert temporal.signals == []
