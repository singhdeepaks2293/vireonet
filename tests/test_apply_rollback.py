from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from vireonet.adapters.fakes import FakeAwxAdapter, FakeNetBoxAdapter, FakeServiceNowAdapter
from vireonet.db import Base
from vireonet.models import Change
from vireonet.policy import PolicyEngine
from vireonet.state_machine import ChangeState
from vireonet.temporal.activities import ApplyInput, ChangeActivities, CheckInput, RollbackInput

INTENT = {
    "change_type": "vlan_add",
    "vlan_id": 200,
    "vlan_name": "new",
    "devices": ["leaf-01"],
    "site": "dc1",
    "ticket_number": "RITM1",
    "requested_by": "alice",
}


async def _activities(tmp_path):  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    change_id = str(uuid.uuid4())
    async with factory() as s:
        s.add(
            Change(
                id=uuid.UUID(change_id),
                ticket_number="RITM1",
                change_type="vlan_add",
                state=ChangeState.PRE_CHECKED,
                requester="alice",
                intent=INTENT,
                devices=["leaf-01"],
            )
        )
        await s.commit()
    acts = ChangeActivities(
        factory,
        servicenow=FakeServiceNowAdapter(),
        netbox=FakeNetBoxAdapter(),
        awx=FakeAwxAdapter(),
        policy=PolicyEngine.default(),
    )
    return acts, change_id, engine


async def test_apply_dry_run_then_waves_succeeds(tmp_path) -> None:
    acts, change_id, engine = await _activities(tmp_path)
    # Pre-check acquires lock + backup, so apply can run.
    pre = await acts.pre_check_change(CheckInput(change_id=change_id, intent=INTENT, kind="pre"))
    assert pre["ok"], pre
    res = await acts.apply_change(ApplyInput(change_id=change_id, intent=INTENT))
    assert res["ok"], res
    assert res["evidence"]["dry_run"]["ok"] is True
    await engine.dispose()


async def test_post_check_failure_triggers_rollback_activity(tmp_path) -> None:
    acts, change_id, engine = await _activities(tmp_path)
    # VLAN 200 is not in NetBox, so ConfigMatchesIntent post-check fails.
    post = await acts.post_check_change(CheckInput(change_id=change_id, intent=INTENT, kind="post"))
    assert not post["ok"]
    rb = await acts.rollback_change(
        RollbackInput(change_id=change_id, intent=INTENT, evidence=post)
    )
    assert rb["ok"]  # Fake AWX rollback succeeds
    await engine.dispose()
