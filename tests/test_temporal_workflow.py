"""Integration test with the Temporal test environment (time-skipping).

Requires the Temporal test server, which `temporalio` downloads on first
use. If it cannot be downloaded (offline sandbox), this file's tests fail
with a clear download error — that is an environment limitation, not a
workflow bug. See Known limitations.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from vireonet.adapters.fakes import FakeAwxAdapter, FakeNetBoxAdapter, FakeServiceNowAdapter
from vireonet.db import Base
from vireonet.models import Change
from vireonet.policy import PolicyEngine
from vireonet.state_machine import ChangeState
from vireonet.temporal.activities import ChangeActivities, all_activities
from vireonet.temporal.workflow import ChangeWorkflow, ChangeWorkflowInput

BASE_INTENT = {
    "change_type": "vlan_add",
    "vlan_id": 200,
    "vlan_name": "new",
    "devices": ["leaf-01"],
    "site": "dc1",
    "ticket_number": "RITM0001234",
    "requested_by": "alice",
}


@pytest.fixture()
def anyio_backend():  # noqa: D103 — temporal test env needs asyncio
    return "asyncio"


async def _setup_db(tmp_path):  # type: ignore[no-untyped-def]
    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    change_id = str(uuid.uuid4())
    async with factory() as s:
        s.add(
            Change(
                id=uuid.UUID(change_id),
                ticket_number="RITM0001234",
                change_type="vlan_add",
                state=ChangeState.VALIDATED,
                requester="alice",
                intent=BASE_INTENT,
                devices=["leaf-01"],
            )
        )
        await s.commit()
    return factory, change_id, engine


async def test_workflow_happy_path_closes(tmp_path) -> None:
    factory, change_id, engine = await _setup_db(tmp_path)
    activities = ChangeActivities(
        factory,
        servicenow=FakeServiceNowAdapter(),
        netbox=FakeNetBoxAdapter(),
        awx=FakeAwxAdapter(),
        policy=PolicyEngine.default(),
    )
    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        Worker(
            env.client,
            task_queue="test-q",
            workflows=[ChangeWorkflow],
            activities=all_activities(activities),
        ),
    ):
        handle = await env.client.start_workflow(
            ChangeWorkflow.run,
            ChangeWorkflowInput(
                change_id=change_id,
                ticket_number="RITM0001234",
                intent=BASE_INTENT,
                requester="alice",
                ci_timeout_seconds=60,
                approval_timeout_seconds=60,
            ),
            id="RITM0001234",
            task_queue="test-q",
        )
        await handle.signal(ChangeWorkflow.ci_passed)
        await handle.signal(ChangeWorkflow.merge_completed)
        await handle.signal(ChangeWorkflow.approve, "ok")
        assert await handle.result() == "closed"
    async with factory() as s:
        change = await s.get(Change, uuid.UUID(change_id))
        assert change is not None and str(change.state) == "closed"
    await engine.dispose()


async def test_workflow_ci_failed_exits(tmp_path) -> None:
    factory, change_id, engine = await _setup_db(tmp_path)
    activities = ChangeActivities(
        factory,
        servicenow=FakeServiceNowAdapter(),
        netbox=FakeNetBoxAdapter(),
        awx=FakeAwxAdapter(),
        policy=PolicyEngine.default(),
    )
    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        Worker(
            env.client,
            task_queue="test-q",
            workflows=[ChangeWorkflow],
            activities=all_activities(activities),
        ),
    ):
        handle = await env.client.start_workflow(
            ChangeWorkflow.run,
            ChangeWorkflowInput(
                change_id=change_id,
                ticket_number="RITM0001234",
                intent=BASE_INTENT,
                requester="alice",
                ci_timeout_seconds=60,
                approval_timeout_seconds=60,
            ),
            id="RITM0001234",
            task_queue="test-q",
        )
        await handle.signal(ChangeWorkflow.ci_failed, "batfish failed")
        assert await handle.result() == "ci_failed"
    async with factory() as s:
        change = await s.get(Change, uuid.UUID(change_id))
        assert change is not None and str(change.state) == "ci_failed"
    await engine.dispose()


async def test_workflow_reject_exits(tmp_path) -> None:
    factory, change_id, engine = await _setup_db(tmp_path)
    activities = ChangeActivities(
        factory,
        servicenow=FakeServiceNowAdapter(),
        netbox=FakeNetBoxAdapter(),
        awx=FakeAwxAdapter(),
        policy=PolicyEngine.default(),
    )
    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        Worker(
            env.client,
            task_queue="test-q",
            workflows=[ChangeWorkflow],
            activities=all_activities(activities),
        ),
    ):
        handle = await env.client.start_workflow(
            ChangeWorkflow.run,
            ChangeWorkflowInput(
                change_id=change_id,
                ticket_number="RITM0001234",
                intent=BASE_INTENT,
                requester="alice",
                ci_timeout_seconds=60,
                approval_timeout_seconds=60,
            ),
            id="RITM0001234",
            task_queue="test-q",
        )
        await handle.signal(ChangeWorkflow.ci_passed)
        await handle.signal(ChangeWorkflow.merge_completed)
        await handle.signal(ChangeWorkflow.reject, "not now")
        assert await handle.result() == "rejected"
    await engine.dispose()
