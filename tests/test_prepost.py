"""Pre/Post checks: tests on TextFSM rows, intelligent diff, snapshots, the
change pipeline (check_profile), the flow action and the API."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import vireonet.models  # noqa: F401  (tables)
from vireonet.adapters.fakes import FakeNetBoxAdapter
from vireonet.api.security import CurrentUser, get_current_user
from vireonet.config import Settings
from vireonet.db import Base
from vireonet.execution.fake_engine import FakeExecutionEngine
from vireonet.execution.fake_network import FakeNetwork
from vireonet.main import create_app
from vireonet.prepost.engine import (
    ProfileError,
    compare,
    ensure_profile,
    evaluate,
    parsed_diff,
    raw_diff,
    validate_profile,
)
from vireonet.prepost.service import BUILTIN, PrePostService
from vireonet.topology.demo import seed_demo_netbox

PROFILE = {
    "name": "Interfaces",
    "commands": [
        {
            "id": "intf",
            "command": {"default": "show interfaces status"},
            "key": ["port"],
            "ignore_fields": ["last_input"],
            "tolerances": {"crc": {"abs": 5}, "rate": {"pct": 10}},
            "raw_ignore": [r"uptime \d+"],
        }
    ],
    "tests": [
        {"name": "no-down", "command": "intf", "type": "no_rows", "where": {"status": "down"}},
        {
            "name": "all-full-duplex",
            "command": "intf",
            "type": "all_rows",
            "expect": {"duplex": "full"},
        },
        {
            "name": "uplinks",
            "command": "intf",
            "type": "count",
            "where": {"port": {"op": "matches", "value": "^Te"}},
            "op": "gte",
            "value": 2,
        },
        {"name": "crc-ok", "command": "intf", "type": "field_range", "field": "crc", "max": 100},
        {"name": "none-removed", "command": "intf", "type": "no_rows_removed"},
        {"name": "none-added", "command": "intf", "type": "no_rows_added"},
        {"name": "vlan-same", "command": "intf", "type": "unchanged", "fields": ["vlan"]},
        {
            "name": "crc-delta",
            "command": "intf",
            "type": "delta",
            "field": "crc",
            "aggregate": "sum",
            "max_abs": 20,
        },
    ],
}
PRE_ROWS = [
    {
        "port": "Te1/1",
        "status": "up",
        "duplex": "full",
        "vlan": "trunk",
        "crc": "3",
        "rate": "100",
        "last_input": "00:00:01",
    },
    {
        "port": "Te1/2",
        "status": "up",
        "duplex": "full",
        "vlan": "trunk",
        "crc": "0",
        "rate": "200",
        "last_input": "00:00:02",
    },
    {
        "port": "Gi1/5",
        "status": "up",
        "duplex": "full",
        "vlan": "300",
        "crc": "1",
        "rate": "10",
        "last_input": "never",
    },
]


def snap(
    rows: list[dict[str, Any]] | None, raw: str = "x", label: str = "pre", error: str = ""
) -> dict[str, Any]:
    entry: dict[str, Any] = {"raw": raw}
    if rows is not None:
        entry["rows"] = rows
    else:
        entry["error"] = error
    return {"label": label, "devices": {"leaf-01": {"intf": entry}}}


def test_validation() -> None:
    assert validate_profile(PROFILE) == []
    errs = validate_profile(
        {
            "name": "x",
            "commands": [{"id": "A", "command": "conf t"}],
            "tests": [{"name": "t", "command": "zz", "type": "nope"}],
        }
    )
    text = " ".join(errs)
    assert (
        "lowercase" in text
        and "not a read-only command" in text
        and "type must be one of" in text
        and "command must be one of" in text
    )
    with pytest.raises(ProfileError):
        ensure_profile({"name": ""})
    for b in BUILTIN:
        assert validate_profile(b) == [], b["name"]


def test_single_snapshot_tests_on_parsed_rows() -> None:
    results = {r["name"]: r for r in evaluate(PROFILE, snap(PRE_ROWS))}
    assert all(results[n]["ok"] for n in ("no-down", "all-full-duplex", "uplinks", "crc-ok"))
    assert "none-removed" not in results  # compare tests need a pre snapshot
    bad = [dict(PRE_ROWS[0], status="down"), *PRE_ROWS[1:]]
    r = {x["name"]: x for x in evaluate(PROFILE, snap(bad))}
    assert not r["no-down"]["ok"] and "Te1/1" in r["no-down"]["message"]
    unparsed = evaluate(PROFILE, snap(None, error="no TextFSM template"))
    assert all(not x["ok"] and "TextFSM" in x["message"] for x in unparsed)


def test_compare_tests_and_intelligent_diff() -> None:
    post = [
        dict(
            PRE_ROWS[0], crc="6", rate="105", last_input="00:00:09"
        ),  # within tolerance, ignored field
        dict(PRE_ROWS[1], vlan="300", crc="40"),  # vlan changed, crc beyond tolerance
        {
            "port": "Gi1/9",
            "status": "up",
            "duplex": "full",
            "vlan": "10",
            "crc": "0",
            "rate": "1",
        },  # added; Gi1/5 removed
    ]
    out = compare(PROFILE, snap(PRE_ROWS), snap(post, label="post"))
    r = {x["name"]: x for x in out["tests"]}
    assert not r["none-removed"]["ok"] and "Gi1/5" in r["none-removed"]["message"]
    assert not r["none-added"]["ok"] and not r["vlan-same"]["ok"]
    assert not r["crc-delta"]["ok"] and "4 → 46" in r["crc-delta"]["message"]
    d = out["diffs"][0]["parsed"]
    assert d["added"] == 1 and d["removed"] == 1
    changed = {c["key"]: c["fields"] for c in d["changed"]}
    assert (
        "Te1/1" not in changed
    )  # crc +3 (abs 5) and rate +5 % (pct 10) tolerated; last_input ignored
    assert set(changed["Te1/2"]) == {"vlan", "crc"}
    assert out["ok"] is False and out["summary"]["failed"] >= 4


def test_raw_diff_ignores_volatile_lines_and_parsed_diff_without_key() -> None:
    cmd = PROFILE["commands"][0]
    assert raw_diff(cmd, "uptime 100\nline a", "uptime 999\nline a")["changed"] is False
    assert "+line b" in raw_diff(cmd, "line a", "line b")["diff"]
    nokey = parsed_diff({"key": []}, [{"a": 1}], [{"a": 1}, {"a": 2}])
    assert nokey["added"] == 1 and nokey["removed"] == 0


@pytest.fixture
async def svc(tmp_path):  # type: ignore[no-untyped-def]
    db = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/p.db")
    async with db.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(db, expire_on_commit=False)
    nb = FakeNetBoxAdapter()
    seed_demo_netbox(nb)
    net = FakeNetwork()
    yield (
        PrePostService(sessions, engine=FakeExecutionEngine(network=net), netbox=nb),
        net,
        sessions,
    )
    await db.dispose()


async def test_service_captures_real_snapshots_and_compares(svc) -> None:  # type: ignore[no-untyped-def]
    service, net, _ = svc
    names = {p["name"] for p in await service.list_profiles()}
    assert {"VLAN baseline", "Software version"} <= names
    pre = await service.capture(
        "VLAN baseline", ["leaf-01", "spine-01", "leaf-02"], label="pre", ticket="CHG1", user="t"
    )
    assert (
        pre["failed"] == 0
        and pre["data"]["devices"]["spine-01"]["vlans"]["template"]
        == "arista_eos_show_vlan.textfsm"
    )
    net.apply("leaf-01", {"change_type": "vlan_add", "vlan_id": 300, "vlan_name": "web300"})
    net.apply("spine-01", {"change_type": "vlan_remove", "vlan_id": 100})
    post = await service.capture(
        "VLAN baseline",
        ["leaf-01", "spine-01", "leaf-02"],
        label="post",
        ticket="CHG1",
        user="t",
        pre_id=pre["id"],
    )
    failed = {(r["device"], r["name"]) for r in post["results"] if not r["ok"]}
    assert failed == {("spine-01", "no-vlan-removed")}
    cmp = await service.compare(pre["id"], post["id"])
    by = {(d["device"], d["command"]): d for d in cmp["diffs"]}
    assert (
        by[("leaf-01", "vlans")]["parsed"]["added"] == 1
        and by[("leaf-02", "vlans")]["changed"] is False
    )
    assert any(s["id"] == post["id"] for s in await service.list_snapshots(ticket="CHG1"))
    v1 = await service.capture("Software version", ["leaf-01"], label="pre", user="t")
    net.stage_image("leaf-01", "17.9.4")
    net.activate_image("leaf-01", "17.9.4")
    v2 = await service.capture(
        "Software version", ["leaf-01"], label="post", user="t", pre_id=v1["id"]
    )
    d = (await service.compare(v1["id"], v2["id"]))["diffs"][0]
    assert d["parsed"]["removed"] == 1 and d["parsed"]["added"] == 1  # version row changed (no key)
    assert "+Cisco IOS Software" in d["raw"]["diff"] or "17.9.4" in d["raw"]["diff"]


async def test_change_pipeline_uses_the_check_profile(tmp_path) -> None:
    from vireonet.adapters.fakes import FakeAwxAdapter, FakeServiceNowAdapter
    from vireonet.models import Change
    from vireonet.policy import PolicyEngine
    from vireonet.state_machine import ChangeState
    from vireonet.temporal.activities import ApplyInput, ChangeActivities, CheckInput

    db = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/c.db")
    async with db.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(db, expire_on_commit=False)
    nb = FakeNetBoxAdapter()
    seed_demo_netbox(nb)
    net = FakeNetwork()
    acts = ChangeActivities(
        sessions,
        servicenow=FakeServiceNowAdapter(),
        netbox=nb,
        awx=FakeAwxAdapter(),
        policy=PolicyEngine.default(),
        engine=FakeExecutionEngine(network=net),
    )

    async def run(intent: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        cid = str(uuid.uuid4())
        async with sessions() as s:
            s.add(
                Change(
                    id=uuid.UUID(cid),
                    ticket_number=intent["ticket_number"],
                    change_type=intent["change_type"],
                    state=ChangeState.APPROVED,
                    requester="t",
                    intent=intent,
                    devices=intent["devices"],
                )
            )
            await s.commit()
        pre = await acts.pre_check_change(CheckInput(change_id=cid, intent=intent, kind="pre"))
        await acts.apply_change(ApplyInput(change_id=cid, intent=intent))
        post = await acts.post_check_change(CheckInput(change_id=cid, intent=intent, kind="post"))
        await acts._locks.release_all(
            intent["devices"], intent["ticket_number"]
        )  # closed by the workflow normally
        return pre, post

    base = {
        "devices": ["leaf-01"],
        "site": "dc1",
        "requested_by": "t",
        "check_profile": "VLAN baseline",
    }
    pre, post = await run(
        {
            **base,
            "change_type": "vlan_add",
            "ticket_number": "CHG-A",
            "vlan_id": 310,
            "vlan_name": "x",
        }
    )
    # (The NetBox built-in check fails here because NetBox is updated in a later step.)
    assert pre["ok"] and post["evidence"]["prepost"]["ok"], post["failures"]
    assert not any("no-vlan-removed" in f for f in post["failures"])
    assert (
        post["evidence"]["prepost"]["changed"] == ["leaf-01/vlans"]
        and post["evidence"]["prepost"]["pre_snapshot_id"]
    )
    pre2, post2 = await run(
        {**base, "change_type": "vlan_remove", "ticket_number": "CHG-B", "vlan_id": 100}
    )
    assert pre2["ok"] and not post2["ok"] and not post2["evidence"]["prepost"]["ok"]
    assert any("no-vlan-removed" in f for f in post2["failures"])
    await db.dispose()


async def test_flow_action(svc) -> None:  # type: ignore[no-untyped-def]
    from temporalio.exceptions import ApplicationError

    from vireonet.flows.activities import FlowActionInput, FlowActivities

    service, net, sessions = svc
    acts = FlowActivities(
        sessions,
        settings=SimpleNamespace(textfsm_user_dir="policies/textfsm"),
        engine=service._engine,
        netbox=service._netbox,
    )
    pre = await acts.flow_action(
        FlowActionInput(
            run_id="run-1",
            step_id="s",
            step_type="run_check_profile",
            params={"profile": "VLAN baseline", "devices": ["leaf-01"], "label": "pre"},
        )
    )
    assert pre["ok"] and pre["snapshot_id"]
    net.apply("leaf-01", {"change_type": "vlan_remove", "vlan_id": 100})
    with pytest.raises(ApplicationError, match="no-vlan-removed"):
        await acts.flow_action(
            FlowActionInput(
                run_id="run-1",
                step_id="s",
                step_type="run_check_profile",
                params={
                    "profile": "VLAN baseline",
                    "devices": ["leaf-01"],
                    "label": "post",
                    "compare_with": pre["snapshot_id"],
                },
            )
        )
    soft = await acts.flow_action(
        FlowActionInput(
            run_id="run-1",
            step_id="s",
            step_type="run_check_profile",
            params={
                "profile": "VLAN baseline",
                "devices": ["leaf-01"],
                "label": "post",
                "compare_with": pre["snapshot_id"],
                "fail_on_test_failure": False,
            },
        )
    )
    assert soft["ok"] is False and soft["diff"]["changed"] == ["leaf-01/vlans"]


def test_api(tmp_path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path}/a.db"

    async def init() -> None:
        e = create_async_engine(url)
        async with e.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        await e.dispose()

    asyncio.run(init())
    app = create_app(Settings(adapter_mode="fake", database_url=url))

    def as_role(role: str) -> TestClient:
        app.dependency_overrides[get_current_user] = lambda: CurrentUser("u", role, role)
        return TestClient(app)

    v = as_role("viewer")
    assert {p["name"] for p in v.get("/prepost/profiles").json()["items"]} >= {"VLAN baseline"}
    assert (
        v.post(
            "/prepost/snapshots", json={"profile": "VLAN baseline", "devices": ["leaf-01"]}
        ).status_code
        == 403
    )
    op = as_role("operator")
    a = op.post(
        "/prepost/snapshots",
        json={"profile": "VLAN baseline", "devices": ["leaf-01", "spine-01"], "label": "pre"},
    ).json()
    b = op.post(
        "/prepost/snapshots",
        json={
            "profile": "VLAN baseline",
            "devices": ["leaf-01", "spine-01"],
            "label": "post",
            "compare_with": a["id"],
        },
    ).json()
    cmp = op.post("/prepost/compare", json={"pre": a["id"], "post": b["id"]}).json()
    assert cmp["ok"] and cmp["summary"]["changed"] == 0
    assert op.put("/prepost/profiles", json={**PROFILE, "name": "Mine"}).json()["version"] == 1
    assert op.put("/prepost/profiles", json={**PROFILE, "name": "Mine"}).json()["version"] == 2
    assert op.put("/prepost/profiles", json={"name": "bad", "commands": []}).status_code == 422
    assert op.delete("/prepost/profiles/Mine").status_code == 403
    assert as_role("approver").delete("/prepost/profiles/Mine").status_code == 204
