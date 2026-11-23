from __future__ import annotations

import respx
from httpx import Response

from tests.contract_suite import check_netbox
from vireonet.adapters.base import AdapterConfig
from vireonet.adapters.netbox import NetBoxAdapter

BASE = "https://netbox.example"
DEV1 = {
    "id": 1,
    "name": "leaf-01",
    "serial": "FOC1234A",
    "device_type": {"slug": "c9300"},
    "site": {"slug": "dc1"},
    "status": {"value": "active"},
    "platform": {"slug": "ios-xe"},
    "primary_ip4": {"address": "10.0.0.11/32"},
    "last_updated": "2026-01-01T00:00:00Z",
}
DEV2 = {
    "id": 2,
    "name": "leaf-02",
    "serial": "FOC1234B",
    "device_type": {"slug": "c9300"},
    "site": {"slug": "dc1"},
    "status": {"value": "staged"},
    "platform": {"slug": "ios-xe"},
    "primary_ip4": None,
    "last_updated": "2026-01-01T00:00:00Z",
}
VLAN100 = {"vid": 100, "name": "web", "site": {"slug": "dc1"}, "status": {"value": "active"}}


def _adapter() -> NetBoxAdapter:
    return NetBoxAdapter(AdapterConfig(name="netbox", base_url=BASE, max_retries=0), token="tok")


@respx.mock
async def test_netbox_real_contract() -> None:
    # Order matters: health, get_device, list_devices, get_vlan(100), get_vlan(999),
    # allocate_ip (prefix lookup), get_device(missing).
    respx.get(url__regex=r".*/api/dcim/devices/.*").mock(
        side_effect=[
            Response(200, json={"results": [DEV1], "next": None}),  # health
            Response(200, json={"results": [DEV1], "next": None}),  # get_device leaf-01
            Response(200, json={"results": [DEV1, DEV2], "next": None}),  # list_devices
            Response(200, json={"results": [], "next": None}),  # get_device missing
        ]
    )
    respx.get(url__regex=r".*/api/ipam/vlans/.*").mock(
        side_effect=[
            Response(200, json={"results": [VLAN100], "next": None}),
            Response(200, json={"results": [], "next": None}),
        ]
    )
    respx.get(url__regex=r".*/api/ipam/prefixes/.*").mock(
        return_value=Response(200, json={"results": [{"id": 7}], "next": None})
    )
    respx.post(url__regex=r".*/api/ipam/prefixes/7/available-ips/.*").mock(
        return_value=Response(201, json={"address": "10.0.1.101/24"})
    )
    await check_netbox(_adapter())


@respx.mock
async def test_netbox_pagination_follows_next() -> None:
    respx.get(url__regex=r".*/api/dcim/devices/.*").mock(
        side_effect=[
            Response(200, json={"results": [DEV1], "next": f"{BASE}/api/dcim/devices/?offset=1"}),
            Response(200, json={"results": [DEV2], "next": None}),
        ]
    )
    assert len(await _adapter().list_devices()) == 2


@respx.mock
async def test_netbox_optimistic_conflict() -> None:
    respx.get(url__regex=r".*/api/dcim/devices/.*").mock(
        return_value=Response(200, json={"results": [DEV1], "next": None})
    )
    import pytest

    from vireonet.adapters.errors import AdapterConflict

    with pytest.raises(AdapterConflict):
        await _adapter().update_device_status(
            "leaf-01", "active", expected_last_updated="2020-01-01T00:00:00Z"
        )


@respx.mock
async def test_netbox_inventory_export() -> None:
    respx.get(url__regex=r".*/api/dcim/devices/.*").mock(
        return_value=Response(200, json={"results": [DEV1, DEV2], "next": None})
    )
    inv = await _adapter().export_inventory_for_awx(site="dc1")
    assert inv["all"]["hosts"]["leaf-01"]["ansible_host"] == "10.0.0.11"  # type: ignore[index]
