"""Shared contract suite. One function per interface; run against Fake (M1)
and against Real with respx-mocked HTTP (M2+). Tests assert the Protocol,
never Fake internals."""

from __future__ import annotations

import pytest

from vireonet.adapters.errors import AdapterNotFound
from vireonet.adapters.protocols import DhcpProtocol, NetBoxProtocol, ServiceNowProtocol


async def check_servicenow(a: ServiceNowProtocol) -> None:
    assert (await a.health_check()).status == "ok"
    assert "read_request" in a.capabilities()
    req = await a.get_request("RITM0001234")
    assert req.ticket == "RITM0001234"
    assert await a.add_work_note("RITM0001234", "note", "key-1") is True
    assert await a.add_work_note("RITM0001234", "note", "key-1") is False
    with pytest.raises(AdapterNotFound):
        await a.get_request("RITM9999999")


async def check_netbox(a: NetBoxProtocol) -> None:
    assert (await a.health_check()).status == "ok"
    dev = await a.get_device("leaf-01")
    assert dev.status == "active" and dev.serial == "FOC1234A"
    assert len(await a.list_devices(site="dc1")) == 2
    assert await a.get_vlan(100) is not None
    assert await a.get_vlan(999) is None
    assert (await a.allocate_ip("10.0.1.0/24", "ztp")).startswith("10.0.1.")
    with pytest.raises(AdapterNotFound):
        await a.get_device("no-such-device")


async def check_dhcp(a: DhcpProtocol) -> None:
    assert (await a.health_check()).status == "ok"
    assert "reservation" in a.capabilities()
    assert "remove_reservation" in a.capabilities()
    reservation = await a.generate_reservation("leaf-01", "aa:bb:cc:dd:ee:ff", "10.0.1.101")
    assert reservation.hostname == "leaf-01" and reservation.ip_address == "10.0.1.101"
    config = await a.render_config([reservation], "isc")
    assert await a.validate_config(config)
    assert await a.verify_reservation("leaf-01", config)
    evidence = await a.deploy_and_verify(config, "isc", hostname="leaf-01")
    assert evidence.get("verified") is True
    removed = await a.remove_reservation("leaf-01")
    assert removed.get("removed") is True
