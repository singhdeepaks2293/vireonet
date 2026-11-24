"""Doc-conformance checks for telemetry backends and the LibreNMS monitoring adapter."""

from __future__ import annotations

import json

import httpx
import respx

from vireonet.adapters.base import AdapterConfig
from vireonet.adapters.monitoring import MonitoringAdapter
from vireonet.telemetry.prometheus import PrometheusBackend
from vireonet.telemetry.prtg import PRTGBackend
from vireonet.telemetry.zabbix import ZabbixBackend


def _transport(handler):  # type: ignore[no-untyped-def]
    return httpx.MockTransport(handler)


# --- Zabbix ------------------------------------------------------------------


def _item(itemid: str, name: str, key: str, value: str) -> dict[str, str]:
    return {
        "itemid": itemid,
        "hostid": "10",
        "name": name,
        "key_": key,
        "lastvalue": value,
        "lastclock": "1790000000",
    }


async def test_zabbix_item_get_params_and_non_traffic_items_ignored() -> None:
    seen: list[dict] = []  # type: ignore[type-arg]

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body)
        result = {
            "host.get": [{"hostid": "10", "host": "srv-01"}],
            "item.get": [
                _item("1", "Interface eth0: Bits received", 'net.if.in["eth0"]', "1000"),
                _item("2", "Interface eth0: Bits sent", 'net.if.out["eth0"]', "2000"),
                _item(
                    "3",
                    "Interface eth0: Inbound packets with errors",
                    'net.if.in["eth0",errors]',
                    "7",
                ),
                _item(
                    "4",
                    "Interface eth0: Inbound packets discarded",
                    'net.if.in["eth0",dropped]',
                    "9",
                ),
                _item(
                    "5",
                    "Interface Gi0/1: Inbound packets with errors",
                    "net.if.in.errors[ifInErrors.1]",
                    "5",
                ),
                _item(
                    "6",
                    "Interface Gi0/1: Outbound packets discarded",
                    "net.if.out.discards[ifOutDiscards.1]",
                    "6",
                ),
            ],
        }[body["method"]]
        return httpx.Response(200, json={"jsonrpc": "2.0", "result": result, "id": body["id"]})

    z = ZabbixBackend("https://zbx", token="tok", transport=_transport(handler))
    rates = await z.interface_rates(["srv-01"])
    assert [(r.device, r.interface, r.in_bps, r.out_bps) for r in rates] == [
        ("srv-01", "eth0", 1000.0, 2000.0)
    ]
    params = next(b["params"] for b in seen if b["method"] == "item.get")
    assert "searchWildcardsEnabled" not in params
    assert params["search"] == {"key_": "net.if."}
    assert params["hostids"] == ["10"]


async def test_zabbix_unknown_host_skips_item_get() -> None:
    methods: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        methods.append(body["method"])
        return httpx.Response(200, json={"jsonrpc": "2.0", "result": [], "id": body["id"]})

    z = ZabbixBackend("https://zbx", token="tok", transport=_transport(handler))
    assert await z.interface_rates(["nope"]) == []
    assert await z.interface_history("nope", "eth0") == []
    assert "item.get" not in methods and methods == ["host.get", "host.get"]


# --- Prometheus --------------------------------------------------------------


async def test_prometheus_gnmic_regex_is_anchored_with_optional_port() -> None:
    queries: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        queries.append(req.url.params["query"])
        return httpx.Response(200, json={"status": "success", "data": {"result": []}})

    p = PrometheusBackend("https://prom", preset="gnmic", transport=_transport(handler))
    await p.interface_rates(["leaf-01", "spine.1"])
    assert 'source=~"(leaf\\\\-01|spine\\\\.1)(:\\\\d+)?"' in queries[0]

    pfx = PrometheusBackend(
        "https://prom", preset="gnmic", metric_prefix="gnmic", transport=_transport(handler)
    )
    await pfx.interface_rates(["r1"])
    assert queries[-1].startswith("rate(gnmic_interfaces_interface_state_counters_out_octets{")


# --- PRTG --------------------------------------------------------------------


async def test_prtg_params_and_traffic_in_prefix_match() -> None:
    calls: list[dict[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        params = dict(req.url.params)
        calls.append(params)
        if params.get("content") == "sensors":
            return httpx.Response(
                200, json={"sensors": [{"objid": 2001, "device": "fw-01", "sensor": "port1"}]}
            )
        return httpx.Response(
            200,
            json={
                "channels": [
                    {"name": "Traffic In", "lastvalue": "1 Mbit/s", "lastvalue_raw": 125000},
                    {"name": "Traffic Out", "lastvalue": "200 kbit/s", "lastvalue_raw": 25000},
                    {"name": "Traffic Total", "lastvalue_raw": 150000},
                ]
            },
        )

    rates = await PRTGBackend(
        "https://prtg", token="k", transport=_transport(handler)
    ).interface_rates(["fw-01"])
    assert [(r.interface, r.in_bps, r.out_bps) for r in rates] == [("port1", 1e6, 2e5)]
    assert calls[0]["filter_tags"] == "@tag(bandwidthsensor)"
    assert "lastvalue_" in calls[1]["columns"].split(",")


async def test_prtg_historicdata_is_cached() -> None:
    hist_calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal hist_calls
        if req.url.path.startswith("/api/historicdata"):
            hist_calls += 1
            return httpx.Response(
                200,
                json={
                    "histdata": [
                        {
                            "datetime": "t1",
                            "Traffic In (speed)(RAW)": 10,
                            "Traffic Out (speed)(RAW)": 5,
                        }
                    ]
                },
            )
        return httpx.Response(
            200, json={"sensors": [{"objid": 2001, "device": "fw-01", "sensor": "port1"}]}
        )

    b = PRTGBackend("https://prtg", token="k", transport=_transport(handler))
    first = await b.interface_history("fw-01", "port1")
    second = await b.interface_history("fw-01", "port1")
    assert first == second and first[0].in_bps == 80.0 and first[0].out_bps == 40.0
    assert hist_calls == 1


# --- LibreNMS monitoring adapter ----------------------------------------------


def _lnms() -> MonitoringAdapter:
    return MonitoringAdapter(
        AdapterConfig(name="monitoring", base_url="https://lnms.example", max_retries=0),
        backend="librenms",
        base_url="https://lnms.example",
        token="t",
    )


@respx.mock
async def test_librenms_enable_disable_bodies() -> None:
    route = respx.patch("https://lnms.example/api/v0/devices/leaf-01").mock(
        return_value=httpx.Response(200, json=[{"status": "ok"}])
    )
    a = _lnms()
    assert await a.disable_monitoring("leaf-01") is True
    assert await a.enable_monitoring("leaf-01") is True
    bodies = [json.loads(c.request.content) for c in route.calls]
    assert bodies == [{"field": "disabled", "data": 1}, {"field": "disabled", "data": 0}]
    assert route.calls[0].request.headers["X-Auth-Token"] == "t"


@respx.mock
async def test_librenms_silence_uses_maintenance_endpoint() -> None:
    route = respx.post("https://lnms.example/api/v0/devices/leaf-01/maintenance").mock(
        return_value=httpx.Response(200, json={"status": "ok", "message": "moved"})
    )
    patch = respx.patch("https://lnms.example/api/v0/devices/leaf-01")
    a = _lnms()
    sid = await a.silence("leaf-01", 90)
    body = json.loads(route.calls[0].request.content)
    assert body["duration"] == "01:30"
    assert body["title"] and body["notes"]
    assert set(body) <= {"title", "notes", "start", "duration", "behavior"}
    # No API to end maintenance: unsilence makes no call, it expires by duration.
    await a.unsilence(sid)
    assert route.call_count == 1 and not patch.called


@respx.mock
async def test_librenms_silence_falls_back_to_disable_on_404() -> None:
    respx.post("https://lnms.example/api/v0/devices/leaf-01/maintenance").mock(
        return_value=httpx.Response(404)
    )
    patch = respx.patch("https://lnms.example/api/v0/devices/leaf-01").mock(
        return_value=httpx.Response(200, json=[{"status": "ok"}])
    )
    a = _lnms()
    sid = await a.silence("leaf-01", 30)
    await a.unsilence(sid)
    bodies = [json.loads(c.request.content) for c in patch.calls]
    assert bodies == [{"field": "disabled", "data": 1}, {"field": "disabled", "data": 0}]
