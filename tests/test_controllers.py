"""Vendor controller adapters against mocked APIs, the fake controllers, the
factory, and controller steps inside flows."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from vireonet.config import Settings
from vireonet.controllers import build_controllers
from vireonet.controllers.base import (
    ControllerConfig,
    ControllerDevice,
    ControllerError,
    ControllerImage,
    find_image,
)
from vireonet.controllers.fake import FakeController
from vireonet.controllers.vendors import (
    Apstra,
    ArubaCentral,
    CatalystCenter,
    CloudVision,
    ExtremeCloudIq,
    FortiManager,
    Meraki,
    Mist,
    NokiaNsp,
    Panorama,
)
from vireonet.execution.fake_network import FakeNetwork
from vireonet.topology.demo import DEMO_NODES

Handler = Any


def mk(cls: Any, handler: Handler, **cfg: Any) -> Any:
    base = {
        "name": "c1",
        "type": "x",
        "url": "https://ctl.example",
        "username": "u",
        "password": "p",
    }
    return cls(ControllerConfig(**{**base, **cfg}), transport=httpx.MockTransport(handler))


# --- Catalyst Center ----------------------------------------------------------------------


async def test_catalyst_center_swim_flow_and_relogin() -> None:
    calls: list[tuple[str, str, Any]] = []
    tokens = iter(["T1", "T2"])

    def h(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        calls.append((req.method, req.url.path, body))
        p = req.url.path
        if p == "/dna/system/api/v1/auth/token":
            assert req.headers["Authorization"].startswith("Basic ")
            return httpx.Response(200, json={"Token": next(tokens)})
        if (
            req.headers.get("X-Auth-Token") == "T1"
            and p.endswith("network-device")
            and "hostname" in str(req.url)
        ):
            return httpx.Response(401)  # expired token: re-login once
        if p == "/dna/intent/api/v1/network-device":
            return httpx.Response(
                200,
                json={
                    "response": [
                        {
                            "id": "uuid-1",
                            "hostname": "leaf-01",
                            "softwareVersion": "17.6.5",
                            "softwareType": "IOS-XE",
                            "platformId": "C9300-48P",
                            "serialNumber": "FOC1",
                            "reachabilityStatus": "Reachable",
                        }
                    ]
                },
            )
        if p == "/dna/intent/api/v1/image/importation":
            return httpx.Response(
                200,
                json={
                    "response": [
                        {
                            "imageUuid": "img-1",
                            "name": "cat9k_iosxe.17.09.04.SPA.bin",
                            "version": "17.9.4",
                            "family": "CAT9K",
                        }
                    ]
                },
            )
        if p == "/dna/intent/api/v1/image/distribution":
            return httpx.Response(
                202, json={"response": {"taskId": "task-d", "url": "/task/task-d"}}
            )
        if p == "/dna/intent/api/v1/image/activation/device":
            return httpx.Response(202, json={"response": {"taskId": "task-a"}})
        if p == "/dna/intent/api/v1/task/task-a":
            return httpx.Response(
                200, json={"response": {"isError": False, "progress": "done", "endTime": 1}}
            )
        if p == "/dna/intent/api/v1/task/task-x":
            return httpx.Response(
                200, json={"response": {"isError": True, "failureReason": "flash full"}}
            )
        return httpx.Response(404)

    c = mk(CatalystCenter, h)
    dev = await c.get_device("leaf-01")
    assert (dev.id, dev.version, dev.platform) == ("uuid-1", "17.6.5", "ios-xe")
    assert [
        x for x in calls if x[1].endswith("auth/token")
    ].__len__() == 2  # re-logged in after 401
    assert (await c.distribute(dev, "17.9.4")).id == "task-d"
    assert calls[-1][2] == [{"deviceUuid": "uuid-1", "imageUuid": "img-1"}]
    job = await c.upgrade(dev, "17.9.4")
    assert calls[-1][2][0]["distributeIfNeeded"] is True and calls[-1][2][0]["imageUuidList"] == [
        "img-1"
    ]
    assert (await c.job(job.id)).status == "succeeded"
    failed = await c.job("task-x")
    assert failed.status == "failed" and failed.message == "flash full"
    with pytest.raises(ControllerError, match="no image for version '99.1'"):
        await c.distribute(dev, "99.1")


# --- Mist ------------------------------------------------------------------------------------


async def test_mist_upgrade_by_device_version() -> None:
    state = {"version": "22.4R3"}
    posts: list[Any] = []

    def h(req: httpx.Request) -> httpx.Response:
        assert req.headers["Authorization"] == "Token mist-token"
        if req.url.path == "/api/v1/sites/s1/stats/devices":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "d1",
                        "name": "leaf-04",
                        "type": "switch",
                        "version": state["version"],
                        "model": "EX4400",
                        "status": "connected",
                    }
                ],
            )
        if req.url.path == "/api/v1/sites/s1/devices/versions":
            return httpx.Response(200, json=[{"model": "EX4400", "version": "23.4R1"}])
        if req.url.path == "/api/v1/sites/s1/devices/d1/upgrade":
            posts.append(json.loads(req.content))
            return httpx.Response(200, json={"status": "inprogress"})
        return httpx.Response(404)

    c = mk(Mist, h, url="", token="mist-token", options={"site_id": "s1"})
    assert c.base == "https://api.mist.com"
    dev = await c.get_device("leaf-04")
    assert (await c.list_images())[0].version == "23.4R1"
    d = await c.distribute(dev, "23.4R1")
    assert posts[-1] == {"version": "23.4R1", "reboot": False} and not d.done
    job = await c.upgrade(dev, "23.4R1")
    assert posts[-1] == {"version": "23.4R1", "reboot": True}
    assert (await c.job(job.id)).status == "running"
    state["version"] = "23.4R1"
    assert (await c.job(job.id)).status == "succeeded"
    with pytest.raises(ControllerError, match="site_id"):
        await mk(Mist, h, token="t").list_devices()


# --- CloudVision -------------------------------------------------------------------------------


async def test_cloudvision_image_bundle_task() -> None:
    seen: list[tuple[str, Any]] = []

    def h(req: httpx.Request) -> httpx.Response:
        assert req.headers["Authorization"] == "Bearer cv-token"
        p = req.url.path
        body = json.loads(req.content) if req.content else None
        seen.append((p, body))
        if p == "/cvpservice/inventory/devices":
            return httpx.Response(
                200,
                json=[
                    {
                        "hostname": "spine-01",
                        "systemMacAddress": "00:1c:73:aa",
                        "version": "4.30.2F",
                        "serialNumber": "JPE1",
                        "modelName": "DCS-7050",
                        "streamingStatus": "active",
                    }
                ],
            )
        if p == "/cvpservice/image/v2/getImageBundles.do":
            return httpx.Response(
                200, json={"data": [{"key": "imagebundle_1", "name": "EOS-4.32.1F"}]}
            )
        if p == "/cvpservice/provisioning/addTempAction.do":
            return httpx.Response(200, json={"data": {"status": "success"}})
        if p == "/cvpservice/provisioning/v2/saveTopology.do":
            assert body == []
            return httpx.Response(200, json={"data": {"status": "success", "taskIds": ["42"]}})
        if p == "/cvpservice/task/executeTask.do":
            return httpx.Response(200, json={"data": "success"})
        if p == "/cvpservice/task/getTaskById.do":
            return httpx.Response(200, json={"workOrderUserDefinedStatus": "Completed"})
        return httpx.Response(404)

    c = mk(CloudVision, h, token="cv-token")
    dev = await c.get_device("spine-01")
    job = await c.upgrade(dev, "4.32.1F")
    assoc = next(b for p, b in seen if p.endswith("addTempAction.do"))
    assert assoc["data"][0] | {} == {
        **assoc["data"][0],
        "action": "associate",
        "nodeId": "imagebundle_1",
        "toId": "00:1c:73:aa",
        "nodeType": "imagebundle",
    }
    assert ("/cvpservice/task/executeTask.do", {"data": ["42"]}) in seen
    assert job.id == "42" and (await c.job("42")).status == "succeeded"
    with pytest.raises(ControllerError, match="one task"):
        await c.distribute(dev, "4.32.1F")


# --- Apstra, FortiManager, NSP, XIQ, Meraki, Aruba ------------------------------------------------


async def test_apstra_login_systems_and_upgrade() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/api/aaa/login":
            assert json.loads(req.content) == {"username": "u", "password": "p"}
            return httpx.Response(201, json={"token": "AP"})
        assert req.headers["AuthToken"] == "AP"
        if p == "/api/systems":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "sys1",
                            "device_key": "K1",
                            "facts": {
                                "hostname": "leaf-04",
                                "os_version": "22.4R3",
                                "os_family": "junos",
                                "hw_model": "QFX5120",
                            },
                        }
                    ]
                },
            )
        if p == "/api/device-os/images":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "osimg-1",
                            "image_name": "junos-23.4R1.tgz",
                            "platform": "junos",
                        }
                    ]
                },
            )
        if p == "/api/system-agents":
            return httpx.Response(
                200, json={"items": [{"id": "agent-9", "status": {"system_id": "K1"}}]}
            )
        if p == "/api/system-agents/agent-9/dos-upgrade":
            assert json.loads(req.content) == {"image_id": "osimg-1"}
            return httpx.Response(202, json={})
        if p == "/api/system-agents/agent-9":
            return httpx.Response(200, json={"last_job_status": {"state": "success"}})
        return httpx.Response(404)

    c = mk(Apstra, h)
    dev = await c.get_device("leaf-04")
    job = await c.upgrade(dev, "23.4R1")
    assert job.id == "agent-9|23.4R1" and (await c.job(job.id)).status == "succeeded"


async def test_fortimanager_jsonrpc_session() -> None:
    bodies: list[dict[str, Any]] = []

    def h(req: httpx.Request) -> httpx.Response:
        b = json.loads(req.content)
        bodies.append(b)
        url = b["params"][0]["url"]
        if url == "/sys/login/user":
            return httpx.Response(200, json={"result": [{"status": {"code": 0}}], "session": "S1"})
        assert b.get("session") == "S1"
        if url == "/dvmdb/adom/root/device":
            return httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "status": {"code": 0},
                            "data": [
                                {
                                    "name": "fw-01",
                                    "sn": "FG600F",
                                    "os_ver": 7,
                                    "mr": 2,
                                    "patch": 8,
                                    "conn_status": 1,
                                    "platform_str": "FortiGate-600F",
                                }
                            ],
                        }
                    ]
                },
            )
        if url == "/um/image/upgrade":
            assert b["params"][0]["data"]["image"] == {"release": "7.4.3"}
            return httpx.Response(
                200, json={"result": [{"status": {"code": 0}, "data": {"taskid": 77}}]}
            )
        if url == "/task/task/77":
            return httpx.Response(
                200,
                json={"result": [{"status": {"code": 0}, "data": {"percent": 100, "num_err": 0}}]},
            )
        return httpx.Response(
            200, json={"result": [{"status": {"code": -6, "message": "invalid url"}}]}
        )

    c = mk(FortiManager, h)
    dev = await c.get_device("fw-01")
    assert dev.version == "7.2.8"
    job = await c.upgrade(dev, "7.4.3")
    assert job.id == "77" and (await c.job("77")).status == "succeeded"
    with pytest.raises(ControllerError, match="invalid url"):
        await c.rpc("get", "/nope")


async def test_panorama_xml_distribute_install_reboot() -> None:
    cmds: list[str] = []
    jobs = {"11": ("FIN", "OK"), "12": ("FIN", "OK"), "13": ("ACT", "PEND")}

    def h(req: httpx.Request) -> httpx.Response:
        form = dict(x.split("=", 1) for x in req.content.decode().split("&"))
        from urllib.parse import unquote_plus

        form = {k: unquote_plus(v) for k, v in form.items()}
        if form.get("type") == "keygen":
            return httpx.Response(
                200, text="<response status='success'><result><key>K1</key></result></response>"
            )
        assert req.headers["X-PAN-KEY"] == "K1"
        cmd = form["cmd"]
        cmds.append(cmd)
        if "<show><devices><all/>" in cmd:
            return httpx.Response(
                200,
                text="<response status='success'><result><devices><entry name='0079'><serial>0079</serial><hostname>fw-02</hostname><sw-version>10.2.9</sw-version><model>PA-3410</model><connected>yes</connected></entry></devices></result></response>",
            )
        if "<upload>" in cmd:
            return httpx.Response(
                200, text="<response status='success'><result><job>11</job></result></response>"
            )
        if "<install>" in cmd:
            return httpx.Response(
                200, text="<response status='success'><result><job>12</job></result></response>"
            )
        if "<reboot>" in cmd:
            return httpx.Response(
                200, text="<response status='success'><result><job>13</job></result></response>"
            )
        if "<show><jobs>" in cmd:
            jid = cmd.split("<id>")[1].split("</id>")[0]
            st, res = jobs[jid]
            return httpx.Response(
                200,
                text=f"<response status='success'><result><job><status>{st}</status><result>{res}</result><progress>50</progress></job></result></response>",
            )
        return httpx.Response(200, text="<response status='error'><msg>bad</msg></response>")

    c = mk(Panorama, h, options={"image_prefix": "PanOS_3400-"})
    dev = await c.get_device("fw-02")
    assert (dev.serial, dev.version) == ("0079", "10.2.9")
    d = await c.distribute(dev, "11.1.2")
    assert "<file>PanOS_3400-11.1.2</file><devices>0079</devices>" in cmds[-1]
    assert (await c.job(d.id)).status == "succeeded"
    a = await c.activate(dev, "11.1.2")
    after_install = await c.job(a.id)  # install finished -> reboot job starts
    assert after_install.status == "running" and after_install.id == "13" and "<reboot>" in cmds[-1]
    with pytest.raises(ControllerError, match="distribute then activate"):
        await c.upgrade(dev, "11.1.2")


async def test_nsp_runs_the_upgrade_workflow() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/rest-gateway/rest/api/v1/auth/token":
            return httpx.Response(200, json={"access_token": "N1"})
        assert req.headers["Authorization"] == "Bearer N1"
        if p == "/restconf/data/nsp-equipment:network/network-element":
            return httpx.Response(
                200,
                json={
                    "nsp-equipment:network-element": [
                        {
                            "ne-id": "10.0.0.9",
                            "ne-name": "border-01",
                            "product": "7750 SR",
                            "version": "23.10.R1",
                        }
                    ]
                },
            )
        if p == "/wfm/api/v1/execution":
            b = json.loads(req.content)
            assert b == {
                "workflow_id": "sros_upgrade",
                "input": {
                    "neId": "10.0.0.9",
                    "neName": "border-01",
                    "version": "24.7.R1",
                    "phase": "full",
                },
                "params": {"env": "DefaultEnv"},
            }
            return httpx.Response(200, json={"response": {"data": [{"id": "ex-1"}]}})
        if p == "/wfm/api/v1/execution/ex-1":
            return httpx.Response(200, json={"response": {"data": {"state": "SUCCESS"}}})
        return httpx.Response(404)

    c = mk(NokiaNsp, h, options={"upgrade_workflow": "sros_upgrade"})
    dev = await c.get_device("border-01")
    job = await c.upgrade(dev, "24.7.R1")
    assert (await c.job(job.id)).status == "succeeded"
    with pytest.raises(ControllerError, match="upgrade_workflow"):
        await mk(NokiaNsp, h).upgrade(dev, "24.7.R1")


async def test_xiq_meraki_aruba() -> None:
    def xiq(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/login":
            return httpx.Response(200, json={"access_token": "X1"})
        if req.url.path == "/devices":
            return httpx.Response(
                200,
                json={
                    "page": 1,
                    "total_pages": 1,
                    "data": [
                        {
                            "id": 5,
                            "hostname": "access-01",
                            "software_version": "32.6.1",
                            "product_type": "X440G2_24t_10G4",
                        }
                    ],
                },
            )
        if req.url.path == "/deployments/firmware-metadatas":
            assert json.loads(req.content) == {"device_ids": [5]}
            return httpx.Response(
                200,
                json={"firmware_metadatas": {"5": [{"firmware_id": 9, "version": "33.1.1"}]}},
            )
        if req.url.path == "/deployments":
            b = json.loads(req.content)
            assert b["policy"]["firmware_upgrade_versions"] == [
                {"firmware_id": 9, "device_id": 5, "product_type": "X440G2_24t_10G4"}
            ]
            return httpx.Response(202, json={"id": 700})
        if req.url.path == "/deployments/700/status":
            return httpx.Response(200, json={"5": {"schedule_status": "COMPLETED"}})
        return httpx.Response(404)

    x = mk(ExtremeCloudIq, xiq, url="")
    xj = await x.upgrade(await x.get_device("access-01"), "33.1.1")
    assert xj.id == "dep:700" and (await x.job(xj.id)).status == "succeeded"

    def meraki(req: httpx.Request) -> httpx.Response:
        assert req.headers["Authorization"] == "Bearer mk"
        if req.url.path == "/api/v1/organizations/o1/devices":
            return httpx.Response(
                200,
                json=[
                    {
                        "serial": "Q2XX",
                        "name": "ms-01",
                        "firmware": "switch-16-7",
                        "networkId": "N1",
                        "productType": "switch",
                    }
                ],
            )
        if req.url.path == "/api/v1/networks/N1/firmwareUpgrades" and req.method == "GET":
            return httpx.Response(
                200,
                json={
                    "products": {
                        "switch": {
                            "availableVersions": [
                                {"id": 3001, "firmware": "switch-17-1", "shortName": "MS 17.1"}
                            ]
                        }
                    }
                },
            )
        if req.url.path == "/api/v1/networks/N1/firmwareUpgrades":
            b = json.loads(req.content)
            assert b["products"]["switch"]["nextUpgrade"]["toVersion"] == {"id": "3001"}
            return httpx.Response(200, json={})
        if req.url.path == "/api/v1/devices/Q2XX":
            return httpx.Response(200, json={"firmware": "switch-17-1"})
        return httpx.Response(404)

    m = mk(Meraki, meraki, url="", token="mk", options={"org_id": "o1"})
    job = await m.upgrade(await m.get_device("ms-01"), "switch-17-1")
    assert (await m.job(job.id)).status == "succeeded"

    def aruba(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/monitoring/v1/switches":
            return httpx.Response(
                200,
                json={
                    "switches": [
                        {
                            "serial": "SG1",
                            "name": "cx-01",
                            "firmware_version": "10.12.1000",
                            "status": "Up",
                        }
                    ]
                },
            )
        if req.url.path == "/firmware/v1/upgrade":
            assert json.loads(req.content)["firmware_version"] == "10.13.1010"
            return httpx.Response(
                200, json={"success_list": ["SG1"], "failed_list": [], "message": "ok"}
            )
        if req.url.path == "/firmware/v1/status":
            assert req.url.params["serial"] == "SG1"
            return httpx.Response(200, json={"state": "Upgrade successful", "reason": ""})
        return httpx.Response(404)

    a = mk(ArubaCentral, aruba, token="ac")
    job = await a.upgrade(await a.get_device("cx-01"), "10.13.1010")
    assert job.id == "SG1|10.13.1010" and (await a.job(job.id)).status == "succeeded"


# --- base helpers, factory, fakes ---------------------------------------------------------------------


def test_find_image_and_factory(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    imgs = [
        ControllerImage("1", "cat9k_iosxe.17.09.04.SPA.bin", "17.9.4", "ios-xe"),
        ControllerImage("2", "EOS-4.32.1F.swi", "", "eos"),
    ]
    assert find_image(imgs, "17.9.4").id == "1" and find_image(imgs, "4.32.1f").id == "2"
    monkeypatch.setenv("DNAC_PW", "secret-pw")
    s = Settings(
        adapter_mode="real",
        controllers=json.dumps(
            [
                {
                    "name": "dnac-dc1",
                    "type": "catalyst_center",
                    "url": "https://dnac",
                    "username": "admin",
                    "password_env": "DNAC_PW",
                }
            ]
        ),
    )
    ctls = build_controllers(s)
    assert list(ctls) == ["dnac-dc1"] and ctls["dnac-dc1"].cfg.password == "secret-pw"  # type: ignore[attr-defined]
    with pytest.raises(ControllerError, match="unknown type"):
        build_controllers(
            Settings(adapter_mode="real", controllers='[{"name": "x", "type": "nope"}]')
        )
    fake = build_controllers(Settings(adapter_mode="fake"))
    assert {
        "catalyst_center",
        "cloudvision",
        "panorama",
        "nsp",
        "mist",
        "fortimanager",
        "xiq",
    } <= set(fake)


async def test_fake_controller_changes_the_network() -> None:
    net = FakeNetwork()
    cv = FakeController("cloudvision", net, list(DEMO_NODES), polls_to_finish=2)
    assert {d.name for d in await cv.list_devices()} == {"spine-01", "spine-02", "leaf-03"}
    dev = await cv.get_device("spine-01")
    job = await cv.upgrade(dev, "4.32.1F")
    assert (await cv.job(job.id)).status == "running"
    assert (await cv.job(job.id)).status == "succeeded"
    assert "4.32.1F" in net.respond("spine-01", "eos", "show version")
    pan = FakeController("panorama", net, list(DEMO_NODES))
    fw = await pan.get_device("fw-02")
    act = await pan.activate(fw, "11.1.2")  # not distributed first: fails like a real device
    await pan.job(act.id)
    assert (await pan.job(act.id)).status == "failed"
    with pytest.raises(ControllerError, match="no image"):
        await cv.upgrade(dev, "9.9.9")


# --- controller steps in flows ---------------------------------------------------------------------------


async def _flow_run(steps: list[dict[str, Any]], *, net: FakeNetwork) -> tuple[dict[str, Any], Any]:
    from vireonet.adapters.fakes import FakeNetBoxAdapter
    from vireonet.flows.activities import FlowActionInput, FlowActivities
    from vireonet.flows.engine import Interpreter, MemoryRuntime
    from vireonet.flows.schema import ensure_valid
    from vireonet.topology.demo import seed_demo_netbox

    nb = FakeNetBoxAdapter()
    seed_demo_netbox(nb)
    ctls = build_controllers(Settings(adapter_mode="fake"), network=net)
    acts = FlowActivities(
        None, settings=SimpleNamespace(controller_poll_seconds=5), netbox=nb, controllers=ctls
    )

    async def action(t: str, p: dict[str, Any]) -> Any:
        return await acts.flow_action(
            FlowActionInput(run_id="r", step_id="s", step_type=t, params=p)
        )

    rt = MemoryRuntime(action)
    d = ensure_valid({"steps": steps}, None, allow_scripts=True)
    return await Interpreter(rt).run(d), rt


async def test_flow_upgrades_through_auto_selected_controllers() -> None:
    net = FakeNetwork()
    result, rt = await _flow_run(
        [
            {
                "id": "a",
                "type": "controller_upgrade",
                "device": "spine-01",
                "version": "4.32.1F",
                "save_as": "eos",
            },
            {
                "id": "b",
                "type": "controller_upgrade",
                "device": "fw-02",
                "version": "11.1.2",
                "save_as": "pan",
            },
            {"id": "c", "type": "controller_get_device", "device": "fw-02", "save_as": "fw"},
            {
                "id": "d",
                "type": "controller_api",
                "controller": "nsp",
                "method": "GET",
                "path": "/restconf/data/x",
                "save_as": "raw",
            },
        ],
        net=net,
    )
    v = result["vars"]
    assert result["status"] == "succeeded", result
    assert v["eos"]["controller"] == "cloudvision" and v["eos"]["job"]["status"] == "succeeded"
    assert v["pan"]["controller"] == "panorama" and v["pan"]["action"] == "distribute+activate"
    assert v["fw"]["version"] == "11.1.2" and "11.1.2" in net.respond(
        "fw-02", "panos", "show version"
    )
    assert v["raw"]["fake"] is True and rt.slept and all(s == 5 for s in rt.slept)


async def test_failed_controller_job_fails_the_step() -> None:
    net = FakeNetwork()
    result, _ = await _flow_run(
        [{"id": "x", "type": "controller_activate", "device": "fw-02", "version": "11.1.2"}],
        net=net,
    )
    assert result["status"] == "failed" and "not on flash" in result["error"]
    bad, _ = await _flow_run(
        [
            {
                "id": "y",
                "type": "controller_upgrade",
                "controller": "nope",
                "device": "leaf-01",
                "version": "1",
            }
        ],
        net=net,
    )
    assert "no controller named 'nope'" in bad["error"]


def test_controller_device_dict() -> None:
    assert ControllerDevice("1", "a").to_dict()["reachable"] is True


async def test_fake_software_state_is_shared_between_processes(tmp_path) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import vireonet.models  # noqa: F401  (registers the tables)
    from vireonet.db import Base

    db = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/s.db")
    async with db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(db, expire_on_commit=False)
    worker = FakeController("cloudvision", FakeNetwork(), list(DEMO_NODES), sessions=sessions)
    api = FakeController("cloudvision", FakeNetwork(), list(DEMO_NODES), sessions=sessions)
    job = await worker.upgrade(await worker.get_device("spine-01"), "4.32.1F")
    await worker.job(job.id)
    await worker.job(job.id)
    assert (await api.get_device("spine-01")).version == "4.32.1F"
    assert {d.name: d.version for d in await api.list_devices()}["spine-02"] == "4.30.2F"
    await db.dispose()
