"""Neural-network models (Intelligence tab): quality floors measured on held-out
data, the API and the flow actions."""

from __future__ import annotations

import warnings
from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from vireonet.api.security import CurrentUser, get_current_user
from vireonet.config import Settings
from vireonet.logs.fake_syslog import fake_show_logging
from vireonet.logs.parser import parse_show_logging
from vireonet.main import create_app
from vireonet.ml import neural
from vireonet.ml.datasets import TrafficSeries, synthetic_changes
from vireonet.topology.demo import DEMO_LINKS, DEMO_NODES, demo_interfaces

warnings.filterwarnings("ignore")
NODES = [n["name"] for n in DEMO_NODES]
LINKS = [(a, c) for a, _b, c, _d in DEMO_LINKS]


def _events():  # type: ignore[no-untyped-def]
    out = []
    for n in DEMO_NODES:
        text = fake_show_logging(
            n["name"],
            n["platform"],
            demo_interfaces(n["name"]),
            now=datetime(2026, 11, 23, 17, tzinfo=UTC),
        )
        out += parse_show_logging(n["name"], n["platform"], text, year=2026)[0]
    return out


def test_log_sequences_flag_new_kinds_of_events_without_false_positives() -> None:
    r = neural.log_sequences(_events())
    flagged = {d["device"]: d for d in r["devices"] if d["anomalous"]}
    # PSU failure and BGP flaps bring event types never seen before on those devices.
    assert set(flagged) == {"spine-02", "border-01"}
    assert any("PS_FAIL" in x for x in flagged["spine-02"]["new_event_types"])
    assert r["top_k_accuracy"] > 0.7
    with pytest.raises(ValueError):
        neural.log_sequences([])


def test_change_risk_learns_the_risk_drivers() -> None:
    trained = neural.train_change_risk(synthetic_changes(3000))
    assert trained["auc"] > 0.65  # held-out AUC; the synthetic ground truth is noisy by design
    risky = neural.change_risk(
        trained,
        change_type="static_route_remove",
        devices=12,
        platforms=3,
        hour=23,
        emergency=True,
        prior_failure_rate=0.25,
    )
    safe = neural.change_risk(trained, change_type="vlan_add", devices=1, platforms=1, hour=2)
    assert (
        risky["risk"] > 3 * safe["risk"]
        and risky["level"] in ("medium", "high")
        and safe["level"] == "low"
    )
    assert {f["feature"] for f in trained["importances"][:5]} & {
        "devices",
        "prior_failure_rate",
        "emergency",
        "platforms",
    }


def test_root_cause_ranks_the_silent_device_whose_neighbours_alarm() -> None:
    # border-01 is dead (silent); everything connected to it alarms.
    neigh = {b for a, b in LINKS if a == "border-01"} | {a for a, b in LINKS if b == "border-01"}
    r = neural.root_cause(NODES, LINKS, {d: 1.0 for d in neigh})
    assert r["auc"] > 0.9 and r["ranking"][0]["device"] == "border-01"


def test_ticket_text_classification_and_device_extraction() -> None:
    t = neural.ticket_text("pls add vlan 300 on leaf 1 and leaf 3", NODES)
    assert (t["change_type"], t["devices"], t["fields"]) == (
        "vlan_add",
        ["leaf-01", "leaf-03"],
        {"vlan_id": 300},
    )
    assert t["accuracy"] > 0.9
    assert (
        neural.ticket_text("OS upgrade spine-02 to 4.32.1F please", NODES)["change_type"]
        == "os_upgrade"
    )
    assert (
        neural.ticket_text("open port 443 from 10.1.1.5 to 172.16.0.9 on fw-01", NODES)[
            "change_type"
        ]
        == "acl_entry_add"
    )
    assert neural.extract_devices("upgrade LEAF02 and spine 1", NODES) == ["leaf-02", "spine-01"]


def test_forecast_beats_naive_on_trending_traffic_and_finds_saturation() -> None:
    rnd = np.random.default_rng(7)
    t = np.arange(400)
    series = []
    for k in range(6):
        base = 0.3 + 0.15 * np.sin(2 * np.pi * t / 72 + k) + rnd.normal(0, 0.01, t.size)
        grow = base + (0.0012 * t if k == 0 else 0)
        series.append(
            TrafficSeries(
                f"leaf-0{k + 1}",
                "Ethernet1/49",
                [str(i) for i in t],
                np.clip(grow, 0, 1.5),
                np.clip(base * 0.8, 0, 1),
            )
        )
    r = neural.forecast(series, horizon=72)  # one full daily cycle
    assert r["mae"] < 0.05 and r["beats_naive"]
    first = r["series"][0]
    assert first["device"] == "leaf-01" and first["saturates_in_minutes"] is not None
    with pytest.raises(ValueError):
        neural.forecast([])


def test_neural_api_and_flow_actions() -> None:
    app = create_app(Settings(adapter_mode="fake", database_url="sqlite+aiosqlite:///:memory:"))
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("u", "v", "viewer")
    c = TestClient(app)
    seq = c.post("/ml/neural/log-sequences", json={}).json()
    assert {d["device"] for d in seq["devices"] if d["anomalous"]} >= {"spine-02"}
    risk = c.post(
        "/ml/neural/change-risk",
        json={"change_type": "acl_entry_remove", "devices": 10, "platforms": 3, "emergency": True},
    ).json()
    assert 0 <= risk["risk"] <= 1 and risk["advisory"] is True
    rc = c.post(
        "/ml/neural/root-cause", json={"alarms": {"leaf-01": 1, "leaf-02": 1, "leaf-03": 1}}
    ).json()
    assert rc["ranking"][0]["device"].startswith("spine")
    tk = c.post("/ml/neural/ticket", json={"text": "remove vlan 40 from leaf 2"}).json()
    assert tk["change_type"] == "vlan_remove" and tk["devices"] == ["leaf-02"]
    fc = c.post("/ml/neural/forecast", json={"hours": 24}).json()
    assert fc["advisory"] is True and fc["beats_naive"] is True  # on the demo telemetry
    assert all(0 <= v <= 1 for s in fc["series"] for v in s["forecast"])

    import asyncio

    from vireonet.adapters.fakes import FakeNetBoxAdapter
    from vireonet.flows.activities import FlowActionInput, FlowActivities
    from vireonet.topology.demo import seed_demo_netbox

    nb = FakeNetBoxAdapter()
    seed_demo_netbox(nb)
    acts = FlowActivities(None, settings=SimpleNamespace(), netbox=nb)
    out = asyncio.run(
        acts.flow_action(
            FlowActionInput(
                run_id="r",
                step_id="s",
                step_type="predict_change_risk",
                params={"change_type": "vlan_add", "devices": 1},
            )
        )
    )
    assert out["level"] == "low"
    cls = asyncio.run(
        acts.flow_action(
            FlowActionInput(
                run_id="r",
                step_id="s",
                step_type="classify_ticket",
                params={"text": "OS UPGRADE leaf-01 to 17.9.4"},
            )
        )
    )
    assert (
        cls["change_type"] == "os_upgrade"
        and cls["devices"] == ["leaf-01"]
        and cls["fields"]["version"] == "17.9.4"
    )
