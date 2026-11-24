"""Telemetry backends (mocked HTTP), fake traffic, topology traffic mapping."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from vireonet.adapters.fakes import FakeNetBoxAdapter
from vireonet.api.security import CurrentUser, get_current_user
from vireonet.config import Settings
from vireonet.main import create_app
from vireonet.telemetry.base import InterfaceRate, TelemetryError
from vireonet.telemetry.factory import build_telemetry
from vireonet.telemetry.fake import FakeTelemetry
from vireonet.telemetry.librenms import LibreNMSBackend
from vireonet.telemetry.prometheus import PrometheusBackend
from vireonet.telemetry.prtg import PRTGBackend
from vireonet.telemetry.solarwinds import SolarWindsBackend
from vireonet.telemetry.zabbix import ZabbixBackend
from vireonet.topology.service import build_topology


def _transport(handler):  # type: ignore[no-untyped-def]
    return httpx.MockTransport(handler)


# --- Zabbix ------------------------------------------------------------------


async def test_zabbix_rates_from_snmp_template_items() -> None:
    seen: list[dict] = []  # type: ignore[type-arg]

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body)
        assert req.headers["Authorization"] == "Bearer tok"
        result = {
            "host.get": [{"hostid": "10", "host": "leaf-01"}],
            "item.get": [
                {
                    "itemid": "1",
                    "hostid": "10",
                    "name": "Interface Gi0/1(uplink): Bits received",
                    "key_": "net.if.in[ifHCInOctets.1]",
                    "lastvalue": "1500000000",
                    "lastclock": "1790000000",
                },
                {
                    "itemid": "2",
                    "hostid": "10",
                    "name": "Interface Gi0/1(uplink): Bits sent",
                    "key_": "net.if.out[ifHCOutOctets.1]",
                    "lastvalue": "250000000",
                    "lastclock": "1790000000",
                },
                {
                    "itemid": "3",
                    "hostid": "10",
                    "name": "Interface Gi0/1: Operational status",
                    "key_": "net.if.status[ifOperStatus.1]",
                    "lastvalue": "1",
                    "lastclock": "1790000000",
                },
            ],
        }[body["method"]]
        return httpx.Response(200, json={"jsonrpc": "2.0", "result": result, "id": body["id"]})

    z = ZabbixBackend("https://zbx", token="tok", transport=_transport(handler))
    rates = await z.interface_rates(["leaf-01"])
    assert len(rates) == 1
    r = rates[0]
    assert (r.device, r.interface, r.in_bps, r.out_bps) == ("leaf-01", "Gi0/1", 1.5e9, 2.5e8)
    assert seen[0]["params"]["filter"] == {"host": ["leaf-01"]}


async def test_zabbix_api_error_is_reported() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "error": {"message": "Not authorised.", "data": "x"}, "id": 1},
        )

    with pytest.raises(TelemetryError, match="Not authorised"):
        await ZabbixBackend(
            "https://zbx", token="bad", transport=_transport(handler)
        ).interface_rates(["a"])


# --- Prometheus (SNMP + gNMI) ---------------------------------------------------


@pytest.mark.parametrize(
    "preset,dev_label,if_label,metric",
    [
        ("snmp_exporter", "instance", "ifName", "ifHCInOctets"),
        ("gnmic", "source", "interface_name", "interfaces_interface_state_counters_in_octets"),
    ],
)
async def test_prometheus_presets(preset: str, dev_label: str, if_label: str, metric: str) -> None:
    queries: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        q = req.url.params["query"]
        queries.append(q)
        value = "800" if "in_octets" in q or "InOctets" in q else "200"
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "result": [
                        {
                            "metric": {dev_label: "leaf-01:161", if_label: "Ethernet1"},
                            "value": [1790000000, value],
                        }
                    ]
                },
            },
        )

    p = PrometheusBackend("https://prom", preset=preset, transport=_transport(handler))
    r = (await p.interface_rates(["leaf-01"]))[0]
    assert (r.device, r.interface, r.in_bps, r.out_bps) == ("leaf-01", "Ethernet1", 800.0, 200.0)
    assert metric in queries[0] and '=~"(leaf\\\\-01)(:\\\\d+)?"' in queries[0]


# --- LibreNMS / PRTG / SolarWinds -----------------------------------------------


async def test_librenms_converts_octets_to_bits() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["X-Auth-Token"] == "t"
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "ports": [
                    {
                        "ifName": "xe-0/0/1",
                        "ifInOctets_rate": "1000",
                        "ifOutOctets_rate": "500",
                        "ifSpeed": "10000000000",
                        "ifOperStatus": "up",
                    }
                ],
            },
        )

    r = (
        await LibreNMSBackend(
            "https://lnms", token="t", transport=_transport(handler)
        ).interface_rates(["r1"])
    )[0]
    assert (r.in_bps, r.out_bps, r.speed_bps, r.in_util) == (8000.0, 4000.0, 1e10, 8e-7)
    with pytest.raises(TelemetryError, match="graphs"):
        await LibreNMSBackend("https://lnms", token="t").interface_history("r1", "x")


async def test_prtg_traffic_channels() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.params["apitoken"] == "k"
        if req.url.params.get("content") == "sensors":
            return httpx.Response(
                200,
                json={
                    "sensors": [
                        {"objid": 2001, "device": "fw-01", "sensor": "port1"},
                        {"objid": 2002, "device": "other", "sensor": "port1"},
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "channels": [
                    {"name": "Traffic In (speed)", "lastvalue_raw": 125000},
                    {"name": "Traffic Out (speed)", "lastvalue_raw": 25000},
                ]
            },
        )

    rates = await PRTGBackend(
        "https://prtg", token="k", transport=_transport(handler)
    ).interface_rates(["fw-01"])
    assert [(r.device, r.interface, r.in_bps, r.out_bps) for r in rates] == [
        ("fw-01", "port1", 1e6, 2e5)
    ]


async def test_solarwinds_swql() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        assert "Orion.NPM.Interfaces" in body["query"] and body["parameters"]["devices"] == [
            "leaf-02"
        ]
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "Device": "leaf-02",
                        "Interface": "Ethernet1/49",
                        "InBps": 4e9,
                        "OutBps": 1e9,
                        "Speed": 1e10,
                        "StatusDescription": "Up",
                    }
                ]
            },
        )

    r = (
        await SolarWindsBackend(
            "https://orion:17774", username="u", password="p", transport=_transport(handler)
        ).interface_rates(["leaf-02"])
    )[0]
    assert (r.in_bps, r.out_bps, r.out_util) == (4e9, 1e9, 0.1)


async def test_http_errors_become_telemetry_errors() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="nope")

    with pytest.raises(TelemetryError, match="authentication failed"):
        await LibreNMSBackend(
            "https://lnms", token="t", transport=_transport(handler)
        ).interface_rates(["r1"])


# --- Fake traffic ---------------------------------------------------------------------


async def test_fake_traffic_is_deterministic_and_bounded() -> None:
    at = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
    fake = FakeTelemetry(now=lambda: at)
    a = await fake.interface_rates(["leaf-01"])
    b = await FakeTelemetry(now=lambda: at).interface_rates(["leaf-01"])
    assert [(r.in_bps, r.out_bps) for r in a] == [(r.in_bps, r.out_bps) for r in b]
    assert all(0 < r.in_bps <= r.speed_bps and 0 <= r.out_bps <= r.speed_bps for r in a)  # type: ignore[operator]
    hist = await fake.interface_history("leaf-01", "Ethernet1", minutes=60, step_seconds=300)
    assert len(hist) == 13


def test_factory() -> None:
    assert build_telemetry(Settings(adapter_mode="real")) is None
    assert isinstance(build_telemetry(Settings(adapter_mode="fake")), FakeTelemetry)
    z = build_telemetry(
        Settings(telemetry_backend="zabbix", telemetry_base_url="https://z", telemetry_token="t")
    )
    assert isinstance(z, ZabbixBackend)


# --- Topology -------------------------------------------------------------------------


class _Telemetry:
    name = "test"

    def __init__(self, rates: list[InterfaceRate]) -> None:
        self.rates = rates

    async def interface_rates(self, devices: list[str]) -> list[InterfaceRate]:
        return self.rates


async def test_topology_link_traffic_uses_either_end() -> None:
    netbox = FakeNetBoxAdapter()

    async def cables(site=None):  # type: ignore[no-untyped-def]
        return [
            {
                "a_device": "leaf-01",
                "a_interface": "Gi0/1",
                "b_device": "leaf-02",
                "b_interface": "Gi0/2",
            },
            {
                "a_device": "leaf-01",
                "a_interface": "Gi0/3",
                "b_device": "leaf-02",
                "b_interface": "Gi0/4",
            },
            {
                "a_device": "leaf-01",
                "a_interface": "Gi0/9",
                "b_device": "ghost",
                "b_interface": "x",
            },
        ]

    netbox.list_cables = cables  # type: ignore[attr-defined]
    telemetry = _Telemetry(
        [
            InterfaceRate("leaf-01", "Gi0/1", in_bps=1e6, out_bps=9e8, speed_bps=1e9),
            InterfaceRate("leaf-02", "Gi0/4", in_bps=5e8, out_bps=2e8, speed_bps=1e9),
        ]
    )
    topo = await build_topology(netbox=netbox, telemetry=telemetry)  # type: ignore[arg-type]
    l0, l1 = topo["links"]
    assert (l0["a_to_b_bps"], l0["b_to_a_bps"], l0["utilization"]) == (9e8, 1e6, 0.9)
    # Only B has data: A->B is what B receives.
    assert (l1["a_to_b_bps"], l1["b_to_a_bps"]) == (5e8, 2e8)
    assert len(topo["links"]) == 2  # cable to a device NetBox does not list is dropped


async def test_topology_survives_telemetry_failure() -> None:
    class Broken:
        name = "zabbix"

        async def interface_rates(self, devices):  # type: ignore[no-untyped-def]
            raise TelemetryError("zabbix: timeout")

    topo = await build_topology(netbox=FakeNetBoxAdapter(), telemetry=Broken(), demo=True)  # type: ignore[arg-type]
    assert topo["nodes"] and topo["links"]
    assert topo["errors"] == ["telemetry (zabbix): zabbix: timeout"]
    assert all(link["a_to_b_bps"] is None for link in topo["links"])


def test_topology_api_in_fake_mode_has_demo_traffic() -> None:
    app = create_app(
        Settings(adapter_mode="fake", dev=True, database_url="sqlite+aiosqlite:///:memory:")
    )
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("u", "v", "viewer")
    client = TestClient(app)
    topo = client.get("/topology").json()
    assert topo["telemetry"] == "fake"
    platforms = {n["platform"] for n in topo["nodes"]}
    assert {"eos", "nxos", "junos", "fortios", "panos", "sros", "exos"} <= platforms
    assert all(link["a_to_b_bps"] is not None for link in topo["links"])
    hist = client.get(
        "/telemetry/interfaces/spine-01/history", params={"interface": "Ethernet1"}
    ).json()
    assert len(hist["points"]) > 5


async def test_netbox_list_cables_keeps_interface_to_interface_only() -> None:
    from vireonet.adapters.base import AdapterConfig
    from vireonet.adapters.netbox import NetBoxAdapter

    nb = NetBoxAdapter.__new__(NetBoxAdapter)

    async def rows(path, params):  # type: ignore[no-untyped-def]
        assert path == "/api/dcim/cables/" and params == {"site": "dc1"}
        iface = "dcim.interface"
        return [
            {
                "a_terminations": [
                    {"object_type": iface, "object": {"name": "Eth1", "device": {"name": "a"}}}
                ],
                "b_terminations": [
                    {"object_type": iface, "object": {"name": "Eth2", "device": {"name": "b"}}}
                ],
            },
            {
                "a_terminations": [
                    {"object_type": iface, "object": {"name": "Eth3", "device": {"name": "a"}}}
                ],
                "b_terminations": [{"object_type": "circuits.circuittermination", "object": {}}],
            },
        ]

    nb._get_all = rows  # type: ignore[method-assign]
    assert AdapterConfig  # imported for parity with other adapter tests
    assert await nb.list_cables("dc1") == [
        {"a_device": "a", "a_interface": "Eth1", "b_device": "b", "b_interface": "Eth2"}
    ]
