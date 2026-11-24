"""Workflow builder API: roles, versions, publish/enable, runs, approvals, webhooks."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from vireonet.api.security import CurrentUser, get_current_user
from vireonet.config import Settings
from vireonet.db import Base
from vireonet.flows.examples import EXAMPLES
from vireonet.main import create_app

FLOW = {
    "name": "Hello",
    "trigger": {"type": "manual"},
    "definition": {
        "inputs": [{"name": "who", "type": "text", "default": "world"}],
        "steps": [{"type": "log", "message": "hi {{ inputs.who }}"}],
    },
}


def make(tmp_path, **settings: Any):  # type: ignore[no-untyped-def]
    url = f"sqlite+aiosqlite:///{tmp_path}/flows.db"

    async def init() -> None:
        e = create_async_engine(url)
        async with e.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        await e.dispose()

    asyncio.run(init())
    app = create_app(
        Settings(
            **{
                "adapter_mode": "fake",
                "dev": True,
                "database_url": url,
                "policy_file": "/none",
                **settings,
            }
        )
    )
    client = TestClient(app)

    def as_role(role: str, name: str = "") -> TestClient:
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            "u-" + role, name or role, role
        )
        return client

    return app, as_role


def test_catalog_validate_test_trigger_and_simulate(tmp_path) -> None:
    _app, as_role = make(tmp_path)
    c = as_role("viewer")
    cat = c.get("/flows/catalog").json()
    assert (
        len(cat["steps"]) > 25
        and "zabbix" in cat["sources"]
        and len(cat["examples"]) == len(EXAMPLES)
    )
    bad = c.post("/flows/validate", json={"definition": {"steps": [{"type": "nope"}]}}).json()
    assert bad["valid"] is False and "unknown step type" in bad["errors"][0]
    warn = c.post(
        "/flows/validate", json={"definition": {"steps": [{"type": "script", "code": "result=1"}]}}
    ).json()
    assert warn["valid"] and "admin" in warn["warnings"][0]
    os_up = EXAMPLES["os-upgrade-itsm"]
    t = c.post(
        "/flows/test-trigger", json={"trigger": os_up["trigger"]}
    ).json()  # catalog sample event
    assert (
        t["matched"]
        and t["captures"]["device"] == "leaf-01"
        and t["captures"]["version"] == "17.9.4"
    )
    miss = c.post(
        "/flows/test-trigger",
        json={
            "trigger": os_up["trigger"],
            "event": {
                "source": "itsm",
                "event": "created",
                "ticket": {"summary": "VLAN 5", "status": "Implement"},
            },
        },
    ).json()
    assert miss["matched"] is False and miss["reason"] == "conditions did not match"
    assert c.post("/flows/simulate", json={"definition": FLOW["definition"]}).status_code == 403
    sim = (
        as_role("operator")
        .post("/flows/simulate", json={"definition": FLOW["definition"], "inputs": {"who": "ops"}})
        .json()
    )
    assert sim["result"]["status"] == "succeeded" and sim["log"][0]["output"]["message"] == "hi ops"


def test_lifecycle_roles_versions_publish_enable_run(tmp_path) -> None:
    app, as_role = make(tmp_path)
    assert as_role("viewer").post("/flows", json=FLOW).status_code == 403
    created = as_role("operator").post("/flows", json=FLOW)
    assert created.status_code == 201
    body = created.json()
    fid, token = body["id"], body["webhook_token"]
    assert body["webhook_url"] == f"/webhooks/flows/{fid}" and token
    assert "webhook_token" not in as_role("viewer").get(f"/flows/{fid}").json()
    dup = as_role("operator").post("/flows", json=FLOW)
    assert dup.status_code == 422 and "already exists" in dup.json()["detail"]["errors"][0]
    v2 = (
        as_role("operator")
        .put(
            f"/flows/{fid}",
            json={"definition": {"steps": [{"type": "log", "message": "v2"}]}, "comment": "edit"},
        )
        .json()
    )
    assert v2["version"] == 2
    assert [
        v["version"] for v in as_role("viewer").get(f"/flows/{fid}/versions").json()["items"]
    ] == [2, 1]
    assert as_role("operator").post(f"/flows/{fid}/publish").status_code == 403  # approver decides
    assert as_role("approver").post(f"/flows/{fid}/enable").status_code == 409  # not published yet
    assert (
        as_role("approver")
        .post(f"/flows/{fid}/publish", params={"version": 1})
        .json()["published_version"]
        == 1
    )
    assert as_role("approver").post(f"/flows/{fid}/enable").json()["enabled"] is True
    run = as_role("operator").post(f"/flows/{fid}/run", json={"inputs": {"who": "x"}})
    assert run.status_code == 202
    rid = run.json()["id"]
    assert rid in app.state.temporal_client.flow_starts
    runs = as_role("viewer").get("/flows/runs", params={"flow_id": fid}).json()["items"]
    assert runs[0]["id"] == rid and runs[0]["status"] == "queued"
    detail = as_role("viewer").get(f"/flows/runs/{rid}").json()
    assert detail["inputs"] == {"who": "x"} and detail["trigger_event"]["source"] == "manual"
    listed = as_role("viewer").get("/flows").json()["items"]
    assert listed[0]["runs"] == 1 and listed[0]["trigger"]["type"] == "manual"
    assert as_role("approver").delete(f"/flows/{fid}").status_code == 403
    assert as_role("admin").delete(f"/flows/{fid}").status_code == 204
    assert as_role("viewer").get(f"/flows/{fid}").status_code == 404


def test_scripts_need_admin_and_export_import(tmp_path) -> None:
    _app, as_role = make(tmp_path)
    scripted = {
        **FLOW,
        "name": "S",
        "definition": {"steps": [{"type": "script", "code": "result = 1"}]},
    }
    assert as_role("operator").post("/flows", json=scripted).status_code == 422
    fid = as_role("admin").post("/flows", json=scripted).json()["id"]
    exported = as_role("viewer").get(f"/flows/{fid}/export").json()
    assert exported["format"] == "vireonet-flow/1"
    assert (
        as_role("operator").post("/flows/import", json={"data": exported, "name": "S2"}).status_code
        == 422
    )  # script
    ok = as_role("admin").post("/flows/import", json={"data": exported, "name": "S2"})
    assert ok.status_code == 201 and ok.json()["name"] == "S2"
    assert as_role("admin").post("/flows/import", json={"data": {"name": "x"}}).status_code == 422
    for key in EXAMPLES:
        ex = as_role("viewer").get(f"/flows/examples/{key}").json()
        assert (
            as_role("admin").post("/flows", json={**ex, "name": ex["name"] + " (copy)"}).status_code
            == 201
        ), key


def test_schedule_trigger_is_synced_on_publish_enable(tmp_path) -> None:
    app, as_role = make(tmp_path)
    fid = (
        as_role("operator")
        .post(
            "/flows",
            json={**FLOW, "name": "Nightly", "trigger": {"type": "schedule", "cron": "0 2 * * *"}},
        )
        .json()["id"]
    )
    as_role("approver").post(f"/flows/{fid}/publish")
    assert as_role("approver").post(f"/flows/{fid}/enable").json()["schedule"] == "recorded"
    assert app.state.temporal_client.flow_schedules[fid] == {
        "trigger": {"type": "schedule", "cron": "0 2 * * *"},
        "enabled": True,
    }
    as_role("approver").post(f"/flows/{fid}/enable", params={"enabled": False})
    assert app.state.temporal_client.flow_schedules[fid]["enabled"] is False


def test_approvals_and_cancel(tmp_path) -> None:
    app, as_role = make(tmp_path)
    fid = as_role("operator").post("/flows", json=FLOW).json()["id"]
    rid = as_role("operator").post(f"/flows/{fid}/run", json={}).json()["id"]
    assert (
        as_role("approver")
        .post(f"/flows/runs/{rid}/decide/s9", json={"approved": True})
        .status_code
        == 409
    )
    store = app.state.flow_store
    asyncio.run(
        store.append_log(
            rid,
            [],
            status="waiting",
            waiting={"s9": {"step_id": "s9", "role": "approver", "message": "ok?"}},
        )
    )
    assert (
        as_role("operator")
        .post(f"/flows/runs/{rid}/decide/s9", json={"approved": True})
        .status_code
        == 403
    )
    ok = as_role("approver", "dana").post(
        f"/flows/runs/{rid}/decide/s9", json={"approved": True, "comment": "go"}
    )
    assert ok.status_code == 200
    assert (
        f"flow-run-{rid}",
        "decide",
        json.dumps(["s9", True, "dana", "go"]),
    ) in app.state.temporal_client.signals
    assert as_role("operator").post(f"/flows/runs/{rid}/cancel").status_code == 200
    asyncio.run(store.finish(rid, {"status": "failed"}))
    assert as_role("operator").post(f"/flows/runs/{rid}/cancel").status_code == 409


def test_flow_webhook_and_adapter_event_webhooks(tmp_path) -> None:
    app, as_role = make(tmp_path, dev=False, webhook_secret="hook", flow_event_token="evt")
    hook = {
        **FLOW,
        "name": "Hook",
        "trigger": {
            "type": "webhook",
            "conditions": {"field": "body.alert", "op": "equals", "value": "InterfaceDown"},
        },
    }
    created = as_role("operator").post("/flows", json=hook).json()
    fid, token = created["id"], created["webhook_token"]
    as_role("approver").post(f"/flows/{fid}/publish")
    as_role("approver").post(f"/flows/{fid}/enable")
    c = as_role("viewer")
    assert c.post(f"/webhooks/flows/{fid}", json={"alert": "InterfaceDown"}).status_code == 401
    r = c.post(
        f"/webhooks/flows/{fid}",
        json={"alert": "InterfaceDown"},
        headers={"X-VireoNet-Flow-Token": token},
    ).json()
    assert r["status"] == "started"
    zab = {**FLOW, "name": "Zab", "trigger": EXAMPLES["zabbix-link-down"]["trigger"]}
    zid = as_role("operator").post("/flows", json=zab).json()["id"]
    as_role("approver").post(f"/flows/{zid}/publish")
    as_role("approver").post(f"/flows/{zid}/enable")
    payload = {
        "event_id": "77",
        "event_value": "1",
        "severity": "4",
        "host": "leaf-02",
        "trigger_name": "Link down on Ethernet1/49",
    }
    assert c.post("/webhooks/flows/source/zabbix", json=payload).status_code == 401
    res = c.post(
        "/webhooks/flows/source/zabbix", json=payload, headers={"X-VireoNet-Event-Token": "evt"}
    ).json()
    assert res["results"][0]["flows"][0]["status"] == "started"
    again = c.post(
        "/webhooks/flows/source/zabbix", json=payload, headers={"Authorization": "Bearer evt"}
    ).json()
    assert again["results"][0]["flows"][0]["status"] == "duplicate"
    raw = json.dumps({"event": "updated", "model": "device", "data": {"name": "x"}}).encode()
    sig = hmac.new(b"evt", raw, hashlib.sha512).hexdigest()
    nb = c.post(
        "/webhooks/flows/source/netbox",
        content=raw,
        headers={"X-Hook-Signature": sig, "Content-Type": "application/json"},
    )
    assert nb.status_code == 202 and nb.json()["events"] == 1
    assert (
        c.post(
            "/webhooks/flows/source/unicorn", json={}, headers={"X-VireoNet-Event-Token": "evt"}
        ).status_code
        == 404
    )


def test_itsm_state_webhook_starts_the_os_upgrade_flow(tmp_path) -> None:
    _app, as_role = make(tmp_path)
    ex = EXAMPLES["os-upgrade-itsm"]
    fid = (
        as_role("operator")
        .post(
            "/flows",
            json={"name": ex["name"], "trigger": ex["trigger"], "definition": ex["definition"]},
        )
        .json()["id"]
    )
    as_role("approver").post(f"/flows/{fid}/publish")
    as_role("approver").post(f"/flows/{fid}/enable")
    c = as_role("viewer")
    r = c.post("/webhooks/servicenow", json={"number": "CHG0030005"}).json()
    assert r["status"] == "flows" and r["flows"][0]["status"] == "started"
    assert r["flows"][0]["captures"]["version"] == "17.9.4"
    again = c.post("/webhooks/servicenow", json={"number": "CHG0030005"}).json()
    assert again["flows"][0]["status"] == "duplicate"
    # Excel tickets keep working as before, flows or not.
    assert (
        c.post("/webhooks/servicenow", json={"number": "CHG0030001"}).json()["status"] == "started"
    )


def test_controllers_api_and_adapter_rows(tmp_path) -> None:
    _app, as_role = make(tmp_path)
    c = as_role("viewer")
    items = {i["name"]: i for i in c.get("/controllers").json()["items"]}
    assert items["cloudvision"]["status"] == "ok" and items["panorama"]["native_upgrade"] is False
    devs = c.get("/controllers/cloudvision/devices").json()["items"]
    assert {d["name"] for d in devs} == {"spine-01", "spine-02", "leaf-03"}
    versions = {i["version"] for i in c.get("/controllers/cloudvision/images").json()["items"]}
    assert "4.32.1F" in versions
    assert c.get("/controllers/nope/devices").status_code == 404
    rows = [r for r in c.get("/adapters/settings").json() if r["category"] == "controller"]
    assert any(r["name"] == "controller: catalyst_center" and r["mode"] == "fake" for r in rows)
