"""SDLC — Requirements R5/R6: services (M2/M4/M6).

ChangeService (duplicate ticket, list filters/pagination, rollback
legality), ZTP retry/reset legality, device locks (acquire/release/TTL).
In-memory SQLite + Fake adapters; no network, no Temporal.
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vireonet.adapters.fakes import FakeNetBoxAdapter, FakeServiceNowAdapter
from vireonet.change_state import ChangeState
from vireonet.models import Change
from vireonet.policy import PolicyEngine
from vireonet.safety import DeviceLockService, LockHeldError
from vireonet.services.changes import ChangeService
from vireonet.state_machine import record_transition
from vireonet.ztp.service import ZtpService


def _factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _changes(engine) -> ChangeService:
    return ChangeService(_factory(engine), servicenow=FakeServiceNowAdapter(), netbox=FakeNetBoxAdapter(), policy=PolicyEngine.default())


def _vlan(ticket: str, vlan_id: int = 200, devices: list[str] | None = None) -> dict:
    return {
        "devices": devices or ["leaf-01"], "site": "dc1", "ticket_number": ticket,
        "requested_by": "alice", "change_type": "vlan_add", "vlan_id": vlan_id, "vlan_name": "test",
    }


def _route(ticket: str) -> dict:
    return {
        "devices": ["leaf-01"], "site": "dc1", "ticket_number": ticket, "requested_by": "alice",
        "change_type": "static_route_add", "prefix": "10.1.0.0/24", "next_hop": "10.0.0.1",
    }


async def _transition(factory, change_id: str, to: ChangeState) -> None:
    import uuid

    async with factory() as session:
        change = await session.get(Change, uuid.UUID(change_id))
        assert change is not None
        await record_transition(session, change, to, actor="test")
        await session.commit()


async def test_duplicate_ticket_returns_existing(engine) -> None:
    svc = _changes(engine)
    first, created = await svc.create_from_intent(_vlan("RITM0001234"), actor="webhook")
    assert created is True
    assert first.state == ChangeState.VALIDATED
    second, created2 = await svc.create_from_intent(_vlan("RITM0001234"), actor="webhook")
    assert created2 is False
    assert second.id == first.id
    items, total = await svc.list()
    assert total == 1


async def test_create_rejected_when_device_staged(engine) -> None:
    svc = _changes(engine)
    change, created = await svc.create_from_intent(_vlan("RITM0001236", devices=["leaf-02"]), actor="webhook")
    assert created is True
    assert change.state == ChangeState.REJECTED


async def test_list_filters_and_pagination(engine) -> None:
    svc = _changes(engine)
    await svc.create_from_intent(_vlan("RITM0001234", 200), actor="webhook")
    await svc.create_from_intent(_route("RITM0001235"), actor="webhook")
    await svc.create_from_intent(_vlan("RITM0001236", 201, devices=["leaf-02"]), actor="webhook")

    _, total = await svc.list()
    assert total == 3
    _, validated = await svc.list(state="validated")
    assert validated == 2
    _, rejected = await svc.list(state="rejected")
    assert rejected == 1
    _, vlans = await svc.list(change_type="vlan_add")
    assert vlans == 2
    _, leaf01 = await svc.list(device="leaf-01")
    assert leaf01 == 2
    _, leaf02 = await svc.list(device="leaf-02")
    assert leaf02 == 1
    page1, _ = await svc.list(page=1, page_size=2, sort_by="ticket_number", sort_dir="asc")
    assert [c.ticket_number for c in page1] == ["RITM0001234", "RITM0001235"]
    page2, _ = await svc.list(page=2, page_size=2, sort_by="ticket_number", sort_dir="asc")
    assert [c.ticket_number for c in page2] == ["RITM0001236"]


async def test_get_detail_diff_renders_intent_lines(engine) -> None:
    svc = _changes(engine)
    change, _ = await svc.create_from_intent(_vlan("RITM0001234", 200), actor="webhook")
    detail = await svc.get_detail(str(change.id))
    assert detail is not None
    texts = [d["text"] for d in detail["diff"]]
    assert "# leaf-01" in texts
    assert any("200" in t for t in texts)
    assert detail["transitions"][0]["to_state"] == "validated"
    assert await svc.get_detail("00000000-0000-0000-0000-000000000000") is None


async def test_rollback_legal_only_from_applied_or_post_checked(engine) -> None:
    svc = _changes(engine)
    factory = _factory(engine)
    change, _ = await svc.create_from_intent(_vlan("RITM0001234", 200), actor="webhook")
    # Rollback from validated is illegal.
    with pytest.raises(ValueError, match="not legal"):
        await svc.rollback(str(change.id), actor="operator")
    for to in (ChangeState.PLANNED, ChangeState.CI_PASSED, ChangeState.APPROVED, ChangeState.PRE_CHECKED, ChangeState.APPLIED):
        await _transition(factory, str(change.id), to)
    rolled = await svc.rollback(str(change.id), actor="operator")
    assert rolled is not None and rolled.state == ChangeState.ROLLED_BACK
    # Rolling back twice is illegal.
    with pytest.raises(ValueError, match="not legal"):
        await svc.rollback(str(change.id), actor="operator")
    assert await svc.rollback("00000000-0000-0000-0000-000000000000", actor="operator") is None


def _ztp(engine) -> ZtpService:
    return ZtpService(_factory(engine), netbox=FakeNetBoxAdapter())


async def test_ztp_register_is_idempotent_by_serial(engine) -> None:
    svc = _ztp(engine)
    dev, created = await svc.register_device(hostname="leaf-01", serial="FOC1234A", mac_address="00:11:22:33:44:55", model="C9300-48P", platform="ios-xe", site="dc1")
    assert created is True and dev.ticket_number == "ZTP-FOC1234A"
    dev2, created2 = await svc.register_device(hostname="leaf-01", serial="FOC1234A", mac_address="00:11:22:33:44:55", model="C9300-48P", platform="ios-xe", site="dc1")
    assert created2 is False and dev2.id == dev.id


async def test_ztp_token_is_single_use(engine) -> None:
    svc = _ztp(engine)
    await svc.register_device(hostname="leaf-01", serial="FOC1234A", mac_address="00:11:22:33:44:55", model="C9300-48P", platform="ios-xe", site="dc1")
    token = await svc.issue_token("FOC1234A")
    assert await svc.verify_token("FOC1234A", token) is True
    assert await svc.verify_token("FOC1234A", token) is False  # already used
    assert await svc.verify_token("FOC1234A", "wrong-token") is False


async def test_ztp_retry_legal_only_from_failed_stages(engine) -> None:
    svc = _ztp(engine)
    await svc.register_device(hostname="leaf-01", serial="FOC1234A", mac_address="00:11:22:33:44:55", model="C9300-48P", platform="ios-xe", site="dc1")
    await svc.set_stage("FOC1234A", "failed")
    dev, _ = await svc.retry("FOC1234A")
    assert dev is not None and dev.stage == "registered"
    await svc.set_stage("FOC1234A", "active")
    with pytest.raises(ValueError, match="not legal"):
        await svc.retry("FOC1234A")
    assert await svc.retry("NOPE") == (None, None)


async def test_ztp_reset_clears_state_but_not_active(engine) -> None:
    svc = _ztp(engine)
    await svc.register_device(hostname="leaf-01", serial="FOC1234A", mac_address="00:11:22:33:44:55", model="C9300-48P", platform="ios-xe", site="dc1")
    await svc.set_stage("FOC1234A", "active", ip_address="10.0.1.101")
    with pytest.raises(ValueError, match="not legal for an active device"):
        await svc.reset("FOC1234A")
    await svc.set_stage("FOC1234A", "identity_failed")
    dev = await svc.reset("FOC1234A")
    assert dev is not None
    assert dev.stage == "registered" and dev.ip_address is None and dev.token_hash is None
    assert await svc.reset("NOPE") is None


async def test_lock_acquire_release_and_steal_expired(engine) -> None:
    svc = DeviceLockService(_factory(engine))
    assert await svc.acquire("leaf-01", "RITM0001234") is True
    with pytest.raises(LockHeldError, match="locked by RITM0001234"):
        await svc.acquire("leaf-01", "RITM0001235")
    assert await svc.is_locked("leaf-01") == "RITM0001234"
    await svc.release("leaf-01", "RITM0001234")
    assert await svc.is_locked("leaf-01") is None
    assert await svc.acquire("leaf-01", "RITM0001235") is True
    # Expire the lock in the DB, then a new ticket can steal it.
    factory = _factory(engine)
    async with factory() as session:
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import select

        from vireonet.models import DeviceLock

        row = (await session.execute(select(DeviceLock).where(DeviceLock.device == "leaf-01"))).scalar_one()
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    assert await svc.acquire("leaf-01", "RITM0001236") is True


async def test_lock_acquire_all_sorted_and_rolls_back(engine) -> None:
    svc = DeviceLockService(_factory(engine))
    await svc.acquire("leaf-02", "RITM0009999")
    with pytest.raises(LockHeldError):
        await svc.acquire_all(["leaf-02", "leaf-01"], "RITM0001234")
    # leaf-01 must have been released again after the failed acquire_all.
    assert await svc.is_locked("leaf-01") is None
    assert await svc.is_locked("leaf-02") == "RITM0009999"


async def test_lock_release_any_and_list(engine) -> None:
    svc = DeviceLockService(_factory(engine))
    assert await svc.release_any("leaf-01") is False
    await svc.acquire("leaf-01", "RITM0001234")
    await svc.acquire("leaf-02", "RITM0001234")
    locks = await svc.list()
    assert [lock.device for lock in locks] == ["leaf-01", "leaf-02"]
    assert await svc.release_any("leaf-01") is True
    assert await svc.is_locked("leaf-01") is None
