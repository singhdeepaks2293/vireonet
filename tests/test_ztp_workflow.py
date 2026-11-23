from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from vireonet.adapters.fakes import (
    FakeAwxAdapter,
    FakeBatfishAdapter,
    FakeDhcpAdapter,
    FakeNetBoxAdapter,
    FakeServiceNowAdapter,
)
from vireonet.db import Base
from vireonet.models import ZtpDevice
from vireonet.temporal.ztp_activities import ZtpActivities, all_ztp_activities
from vireonet.temporal.ztp_workflow import CallbackPayload, ZtpWorkflow, ZtpWorkflowInput
from vireonet.ztp.service import ZtpService


async def _setup(tmp_path):  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add(
            ZtpDevice(
                hostname="leaf-01",
                serial="FOC1234A",
                mac_address="aa:bb:cc:dd:ee:ff",
                model="C9300-48P",
                platform="ios-xe",
                site="dc1",
                stage="registered",
                ticket_number="ZTP-FOC1234A",
            )
        )
        await s.commit()
    ztp = ZtpService(factory, netbox=FakeNetBoxAdapter())
    acts = ZtpActivities(
        factory,
        netbox=FakeNetBoxAdapter(),
        dhcp=FakeDhcpAdapter(),
        awx=FakeAwxAdapter(),
        batfish=FakeBatfishAdapter(),
        servicenow=FakeServiceNowAdapter(),
        ztp_service=ztp,
    )
    return factory, acts, engine


async def test_ztp_happy_path_active(tmp_path) -> None:
    factory, acts, engine = await _setup(tmp_path)
    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        Worker(
            env.client,
            task_queue="test-q",
            workflows=[ZtpWorkflow],
            activities=all_ztp_activities(acts),
        ),
    ):
        handle = await env.client.start_workflow(
            ZtpWorkflow.run,
            ZtpWorkflowInput(
                serial="FOC1234A",
                hostname="leaf-01",
                mac_address="aa:bb:cc:dd:ee:ff",
                model="C9300-48P",
                platform="ios-xe",
                site="dc1",
                ticket_number="ZTP-FOC1234A",
                callback_timeout_seconds=60,
            ),
            id="ZTP-FOC1234A",
            task_queue="test-q",
        )
        await handle.signal(
            ZtpWorkflow.device_callback,
            CallbackPayload(
                serial="FOC1234A",
                model="C9300-48P",
                platform="ios-xe",
                version="17.9.1",
                ip_address="10.0.1.101",
            ),
        )
        assert await handle.result() == "active"
    await engine.dispose()


async def test_ztp_identity_failed(tmp_path) -> None:
    factory, acts, engine = await _setup(tmp_path)
    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        Worker(
            env.client,
            task_queue="test-q",
            workflows=[ZtpWorkflow],
            activities=all_ztp_activities(acts),
        ),
    ):
        handle = await env.client.start_workflow(
            ZtpWorkflow.run,
            ZtpWorkflowInput(
                serial="FOC1234A",
                hostname="leaf-01",
                mac_address="aa:bb:cc:dd:ee:ff",
                model="C9300-48P",
                platform="ios-xe",
                site="dc1",
                ticket_number="ZTP-FOC1234A",
                callback_timeout_seconds=60,
            ),
            id="ZTP-FOC1234A",
            task_queue="test-q",
        )
        await handle.signal(
            ZtpWorkflow.device_callback,
            CallbackPayload(serial="FOC1234A", model="C9500", platform="ios-xe"),
        )
        assert await handle.result() == "identity_failed"
    await engine.dispose()
