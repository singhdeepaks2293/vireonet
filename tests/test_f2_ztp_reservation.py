from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from vireonet.adapters.fakes import (
    FakeAwxAdapter,
    FakeBatfishAdapter,
    FakeDhcpAdapter,
    FakeNetBoxAdapter,
    FakeServiceNowAdapter,
)
from vireonet.adapters.protocols import DhcpReservation
from vireonet.db import Base
from vireonet.temporal.ztp_activities import ActivateInput, ZtpActivities
from vireonet.ztp.service import ZtpService


async def _service(
    tmp_path: Path, dhcp: FakeDhcpAdapter | None = None
) -> tuple[ZtpService, AsyncEngine]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    svc = ZtpService(
        factory,
        netbox=FakeNetBoxAdapter(),  # type: ignore[arg-type]
        token_ttl_seconds=900,
        dhcp=dhcp if dhcp is not None else FakeDhcpAdapter(),
    )
    await svc.register_device(
        hostname="leaf-01",
        serial="FOC1234A",
        mac_address="aa:bb:cc:dd:ee:ff",
        model="C9300-48P",
        platform="ios-xe",
        site="dc1",
    )
    return svc, engine


class RecordingDhcpAdapter(FakeDhcpAdapter):
    """Fake DHCP that records which server each call used (Kea vs ISC)."""

    def __init__(self) -> None:
        super().__init__()
        self.server = "kea"
        self.servers_seen: list[tuple[str, str]] = []

    async def render_config(self, reservations: list[DhcpReservation], server: str) -> str:
        self.servers_seen.append(("render_config", server))
        return str(await super().render_config(reservations, server))

    async def deploy_and_verify(
        self, config_text: str, server: str, *, hostname: str
    ) -> dict[str, object]:
        self.servers_seen.append(("deploy_and_verify", server))
        return dict(await super().deploy_and_verify(config_text, server, hostname=hostname))


async def test_create_reservation_then_idempotent(tmp_path: Path) -> None:
    svc, engine = await _service(tmp_path)
    first = await svc.create_reservation("FOC1234A")
    assert first["ip_address"] == "10.0.1.101"
    device = await svc.get_by_serial("FOC1234A")
    assert (
        device is not None
        and device.stage == "reservation_created"
        and device.ip_address == "10.0.1.101"
    )
    second = await svc.create_reservation("FOC1234A")
    assert second.get("already_created") is True
    assert second["ip_address"] == first["ip_address"]
    await engine.dispose()


async def test_remove_before_active_raises_then_succeeds_when_active(tmp_path: Path) -> None:
    svc, engine = await _service(tmp_path)
    await svc.create_reservation("FOC1234A")
    with pytest.raises(ValueError):
        await svc.remove_reservation("FOC1234A")
    await svc.set_stage("FOC1234A", "active")
    removed = await svc.remove_reservation("FOC1234A")
    assert removed.get("removed") is True
    await engine.dispose()


async def test_create_reservation_without_dhcp_raises(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    svc = ZtpService(factory, netbox=FakeNetBoxAdapter(), token_ttl_seconds=900)  # type: ignore[arg-type]
    await svc.register_device(
        hostname="leaf-01",
        serial="FOC1234A",
        mac_address="aa:bb:cc:dd:ee:ff",
        model="C9300-48P",
        platform="ios-xe",
        site="dc1",
    )
    with pytest.raises(ValueError):
        await svc.create_reservation("FOC1234A")
    await engine.dispose()


async def test_create_reservation_uses_adapter_server_not_hardcoded_isc(tmp_path: Path) -> None:
    dhcp = RecordingDhcpAdapter()
    svc, engine = await _service(tmp_path, dhcp)
    await svc.create_reservation("FOC1234A")
    assert dhcp.servers_seen, "expected render/deploy calls to be recorded"
    assert all(server == "kea" for _, server in dhcp.servers_seen), dhcp.servers_seen
    assert ("render_config", "kea") in dhcp.servers_seen
    assert ("deploy_and_verify", "kea") in dhcp.servers_seen
    await engine.dispose()


async def _activities(
    tmp_path: Path, dhcp: FakeDhcpAdapter | None
) -> tuple[ZtpActivities, ZtpService, AsyncEngine]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    svc = ZtpService(factory, netbox=FakeNetBoxAdapter(), token_ttl_seconds=900, dhcp=dhcp)  # type: ignore[arg-type]
    await svc.register_device(
        hostname="leaf-01",
        serial="FOC1234A",
        mac_address="aa:bb:cc:dd:ee:ff",
        model="C9300-48P",
        platform="ios-xe",
        site="dc1",
    )
    await svc.set_stage("FOC1234A", "day1_applied")
    acts = ZtpActivities(
        factory,
        netbox=FakeNetBoxAdapter(),  # type: ignore[arg-type]
        dhcp=dhcp if dhcp is not None else FakeDhcpAdapter(),
        awx=FakeAwxAdapter(),
        batfish=FakeBatfishAdapter(),
        servicenow=FakeServiceNowAdapter(),
        ztp_service=svc,
    )
    return acts, svc, engine


async def test_activate_removes_reservation_and_stays_active(tmp_path: Path) -> None:
    acts, svc, engine = await _activities(tmp_path, FakeDhcpAdapter())
    result = await acts.activate(
        ActivateInput(serial="FOC1234A", hostname="leaf-01", ticket_number="ZTP-FOC1234A")
    )
    assert result["ok"] is True
    assert result.get("dhcp_reservation_removed", {}).get("removed") is True
    device = await svc.get_by_serial("FOC1234A")
    assert device is not None and device.stage == "active"
    await engine.dispose()


async def test_activate_without_dhcp_still_activates(tmp_path: Path) -> None:
    acts, svc, engine = await _activities(tmp_path, None)
    result = await acts.activate(
        ActivateInput(serial="FOC1234A", hostname="leaf-01", ticket_number="ZTP-FOC1234A")
    )
    assert result["ok"] is True
    assert isinstance(result.get("dhcp_reservation_removal_failed"), str)
    device = await svc.get_by_serial("FOC1234A")
    assert device is not None and device.stage == "active"
    await engine.dispose()
