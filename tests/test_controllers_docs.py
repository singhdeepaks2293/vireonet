"""Vendor adapters against the request paths, bodies and response shapes in
each vendor's official API documentation / SDK (MockTransport only)."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import unquote_plus

import httpx
import pytest

from vireonet.controllers.base import ControllerConfig, ControllerDevice, ControllerError
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


def mk(cls: Any, handler: Any, **cfg: Any) -> Any:
    base = {
        "name": "c1",
        "type": "x",
        "url": "https://ctl.example",
        "username": "u",
        "password": "p",
    }
    return cls(ControllerConfig(**{**base, **cfg}), transport=httpx.MockTransport(handler))


def body(req: httpx.Request) -> Any:
    return json.loads(req.content) if req.content else None


# 1. CloudVision ----------------------------------------------------------------------------


async def test_cloudvision_add_temp_action_then_save_topology_then_execute() -> None:
    calls: list[tuple[str, str, dict[str, str], Any]] = []

    def h(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        calls.append((req.method, p, dict(req.url.params), body(req)))
        if p == "/cvpservice/inventory/devices":
            return httpx.Response(
                200,
                json=[
                    {
                        "hostname": "spine-01",
                        "fqdn": "spine-01.dc1.example",
                        "systemMacAddress": "00:1c:73:aa",
                        "version": "4.30.2F",
                    },
                    {"hostname": "spine-02", "systemMacAddress": "00:1c:73:bb"},
                ],
            )
        if p == "/cvpservice/image/v2/getImageBundles.do":
            return httpx.Response(
                200, json={"data": [{"key": "imagebundle_1", "name": "EOS-4.32.1F"}]}
            )
        if p == "/cvpservice/provisioning/addTempAction.do":
            return httpx.Response(200, json={"data": {"status": "success"}})
        if p == "/cvpservice/provisioning/v2/saveTopology.do":
            return httpx.Response(200, json={"data": {"status": "success", "taskIds": ["42"]}})
        if p == "/cvpservice/task/executeTask.do":
            return httpx.Response(200, json={"data": "success"})
        return httpx.Response(404)

    c = mk(CloudVision, h, token="t")
    dev = await c.get_device("spine-01")
    assert dev.extra["fqdn"] == "spine-01.dc1.example"
    job = await c.upgrade(dev, "4.32.1F")
    posts = [x for x in calls if x[0] == "POST"]
    assert [x[1] for x in posts] == [
        "/cvpservice/provisioning/addTempAction.do",
        "/cvpservice/provisioning/v2/saveTopology.do",
        "/cvpservice/task/executeTask.do",
    ]
    assert posts[0][2] == {"format": "topology", "queryParam": "", "nodeId": "root"}
    item = posts[0][3]["data"][0]
    assert item["toName"] == "spine-01.dc1.example" and item["action"] == "associate"
    assert item["nodeType"] == "imagebundle" and item["toIdType"] == "netelement"
    assert posts[1][3] == [] and posts[2][3] == {"data": ["42"]} and job.id == "42"
    # no fqdn in inventory: hostname is used
    await c.upgrade(await c.get_device("spine-02"), "4.32.1F")
    last_add = [x for x in calls if x[1].endswith("addTempAction.do")][-1]
    assert last_add[3]["data"][0]["toName"] == "spine-02"
    assert "changecontrol/v1" in (CloudVision.__doc__ or "")


# 2. Apstra ----------------------------------------------------------------------------------


async def test_apstra_agent_lookup_dos_upgrade_and_job_state() -> None:
    state = {"s": "inprogress"}
    seen: list[tuple[str, str, Any]] = []

    def h(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        seen.append((req.method, p, body(req)))
        if p == "/api/aaa/login":
            return httpx.Response(201, json={"token": "AP"})
        if p == "/api/systems":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "525400ABCDEF",
                            "device_key": "525400ABCDEF",
                            "facts": {
                                "hostname": None,
                                "os_version": "22.4R3",
                                "os_family": "junos",
                            },
                            "user_config": {"hostname": "leaf-07"},
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
                            "id": "img-old",
                            "image_name": "junos-install-qfx-5e-x86-64-22.4R3.25.tgz",
                            "description": "",
                            "platform": "junos",
                            "checksum": "aa",
                            "image_url": "http://x/old.tgz",
                            "type": "junos",
                        },
                        {
                            "id": "img-new",
                            "image_name": "jinstall-qfx.tgz",
                            "description": "Junos 23.4R2-S2 for QFX",
                            "platform": "junos",
                            "checksum": "bb",
                            "image_url": "http://x/new.tgz",
                            "type": "junos",
                        },
                    ]
                },
            )
        if p == "/api/system-agents":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"id": "other", "status": {"system_id": "XYZ"}},
                        {"id": "agent-77", "status": {"system_id": "525400ABCDEF"}},
                    ]
                },
            )
        if p == "/api/system-agents/agent-77/dos-upgrade":
            return httpx.Response(202, json={})
        if p == "/api/system-agents/agent-77":
            return httpx.Response(
                200, json={"id": "agent-77", "last_job_status": {"state": state["s"]}}
            )
        return httpx.Response(404)

    c = mk(Apstra, h)
    dev = await c.get_device("leaf-07")
    imgs = await c.list_images()
    assert [i.version for i in imgs] == ["22.4R3.25", "23.4R2-S2"]
    assert imgs[1].extra["checksum"] == "bb"
    job = await c.upgrade(dev, "23.4R2-S2")
    assert ("POST", "/api/system-agents/agent-77/dos-upgrade", {"image_id": "img-new"}) in seen
    assert job.id == "agent-77|23.4R2-S2"
    assert (await c.job(job.id)).status == "running"
    state["s"] = "failed"
    assert (await c.job(job.id)).status == "failed"
    state["s"] = "success"
    assert (await c.job(job.id)).status == "succeeded"


# 3. ExtremeCloud IQ ---------------------------------------------------------------------------


async def test_xiq_paging_metadata_deployment_and_operation() -> None:
    deployments: list[Any] = []

    def h(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/devices":
            page = int(req.url.params["page"])
            assert req.url.params["limit"] == "100"
            rows = {
                1: [{"id": 1, "hostname": "a1"}],
                2: [{"id": 2, "hostname": "a2", "product_type": "5320-16P-4XE"}],
            }
            return httpx.Response(200, json={"page": page, "total_pages": 2, "data": rows[page]})
        if p == "/deployments/firmware-metadatas":
            assert body(req) == {"device_ids": [2]}
            return httpx.Response(
                200,
                json={
                    "firmware_metadatas": {
                        "2": [
                            {
                                "firmware_id": 31,
                                "version": "32.6.1.5",
                                "display_version": "32.6.1.5-patch1",
                            },
                            {
                                "firmware_id": 33,
                                "version": "33.1.1.31",
                                "display_version": "33.1.1.31",
                            },
                        ]
                    }
                },
            )
        if p == "/deployments":
            assert req.url.params["async"] == "true"
            deployments.append(body(req))
            if len(deployments) == 1:
                return httpx.Response(202, json={"id": "op-1", "done": False, "metadata": {}})
            return httpx.Response(202, json={"id": 801})
        if p == "/operations/op-1":
            return httpx.Response(200, json={"id": "op-1", "done": True, "response": {"id": 800}})
        if p == "/deployments/800/status":
            return httpx.Response(200, json={"2": {"schedule_status": "COMPLETED"}})
        if p == "/deployments/801/status":
            return httpx.Response(200, json={"2": {"schedule_status": "TIME_OUT"}})
        return httpx.Response(404)

    x = mk(ExtremeCloudIq, h, url="", token="xt")
    assert [d.name for d in await x.list_devices()] == ["a1", "a2"]
    dev = await x.get_device("a2")
    job = await x.upgrade(dev, "33.1.1.31")
    assert deployments[0] == {
        "devices": {"ids": [2]},
        "policy": {
            "enable_complete_configuration_update": True,
            "firmware_upgrade_policy": {"enable_enforce_upgrade": True},
            "firmware_upgrade_versions": [
                {"firmware_id": 33, "device_id": 2, "product_type": "5320-16P-4XE"}
            ],
            "firmware_activate_option": {"activation_delay_seconds": 0},
        },
    }
    assert job.id == "op:op-1"
    done = await x.job(job.id)
    assert done.status == "succeeded" and done.id == "dep:800"
    staged = await x.distribute(dev, "33.1.1.31")
    assert deployments[1]["policy"]["firmware_activate_option"] == {
        "enable_activate_at_next_reboot": True
    }
    assert (await x.job(staged.id)).status == "failed"


# 4. Aruba Central -----------------------------------------------------------------------------


async def test_aruba_versions_upgrade_lists_and_status() -> None:
    fw = {"v": "16.11.0010"}
    status = {"state": "Downloading", "reason": ""}
    queries: list[dict[str, str]] = []

    def h(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/monitoring/v1/switches":
            return httpx.Response(
                200,
                json={
                    "switches": [
                        {
                            "serial": "SG2",
                            "name": "s-01",
                            "switch_type": "AOS-S",
                            "firmware_version": fw["v"],
                        },
                        {
                            "serial": "SG3",
                            "name": "cx-02",
                            "switch_type": "AOS-CX",
                            "firmware_version": "10.12",
                        },
                    ]
                },
            )
        if p == "/firmware/v1/versions":
            queries.append(dict(req.url.params))
            return httpx.Response(
                200, json={"data": [{"firmware_version": "16.11.0012", "release_status": "Latest"}]}
            )
        if p == "/firmware/v1/upgrade":
            b = body(req)
            if b["serial"] == "SG3":
                return httpx.Response(
                    200,
                    json={"success_list": [], "failed_list": ["SG3"], "message": "not eligible"},
                )
            assert b["device_type"] == "HP"
            return httpx.Response(
                200, json={"success_list": ["SG2"], "failed_list": [], "message": "ok"}
            )
        if p == "/firmware/v1/status":
            assert req.url.params["serial"] == "SG2"
            return httpx.Response(200, json=status)
        return httpx.Response(404)

    a = mk(ArubaCentral, h, token="ac")
    sw = await a.get_device("s-01")
    assert sw.extra["device_type"] == "HP" and sw.platform == "aos-s"
    imgs = await a.list_images(device=sw)
    assert queries[-1] == {"device_type": "HP"} and imgs[0].version == "16.11.0012"
    job = await a.upgrade(sw, "16.11.0012")
    assert job.id == "SG2|16.11.0012"
    assert (await a.job(job.id)).status == "running"
    status["state"] = "Upgrade Failed"
    assert (await a.job(job.id)).status == "failed"
    status["state"] = "In progress"
    fw["v"] = "16.11.0012"
    assert (await a.job(job.id)).status == "succeeded"  # confirmed by the switch's firmware
    status["state"] = "Firmware upgraded"
    assert (await a.job(job.id)).status == "succeeded"
    with pytest.raises(ControllerError, match="rejected: not eligible"):
        await a.upgrade(await a.get_device("cx-02"), "10.13")


# 5. Nokia NSP -------------------------------------------------------------------------------


async def test_nsp_execution_array_envelope_env_and_states() -> None:
    state = {"s": "RUNNING"}
    sent: list[Any] = []

    def h(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/wfm/api/v1/execution":
            sent.append(body(req))
            return httpx.Response(
                200, json={"response": {"status": 0, "data": [{"id": "ex-9", "state": "RUNNING"}]}}
            )
        if p == "/wfm/api/v1/execution/ex-9":
            return httpx.Response(
                200, json={"response": {"data": [{"id": "ex-9", "state": state["s"]}]}}
            )
        return httpx.Response(404)

    n = mk(NokiaNsp, h, token="n", options={"upgrade_workflow": "wf", "wfm_env": "Lab"})
    dev = ControllerDevice("10.0.0.1", "pe-1", "sros")
    job = await n.distribute(dev, "24.7.R1")
    assert job.id == "ex-9" and sent[0]["params"] == {"env": "Lab"}
    assert sent[0]["input"]["phase"] == "stage"
    assert (await n.job("ex-9")).status == "running"
    for s, want in [
        ("FAILED", "failed"),
        ("CANCELED", "failed"),
        ("ERROR", "failed"),
        ("SUCCESS", "succeeded"),
    ]:
        state["s"] = s
        assert (await n.job("ex-9")).status == want
    assert "UNVERIFIABLE" in (NokiaNsp.__doc__ or "")
    assert "nsp-ne-upgrade-with-phases" in (NokiaNsp.__doc__ or "")


# 6. FortiManager ---------------------------------------------------------------------------


async def test_fortimanager_exec_version_list_and_version_guards() -> None:
    reqs: list[dict[str, Any]] = []

    def h(req: httpx.Request) -> httpx.Response:
        b = body(req)
        reqs.append(b)
        url = b["params"][0]["url"]
        if url == "/dvmdb/adom/root/device":
            return httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "status": {"code": 0},
                            "data": [
                                {
                                    "name": "fw-9",
                                    "os_ver": 7,
                                    "mr": 2,
                                    "patch": -1,
                                    "platform_str": "FortiGate-100F",
                                }
                            ],
                        }
                    ]
                },
            )
        if url == "/um/image/version/list":
            return httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "status": {"code": 0},
                            "data": {
                                "version_list": [
                                    {
                                        "platform": "FortiGate-100F",
                                        "versions": [
                                            {"version": "7.2.5-b1517"},
                                            {"version": "7.4.3-b2573"},
                                        ],
                                    }
                                ]
                            },
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"result": [{"status": {"code": -6, "message": "bad"}}]})

    f = mk(FortiManager, h, token="fmg")
    dev = await f.get_device("fw-9")
    assert dev.version == "7.2.0"
    imgs = await f.list_images(device=dev)
    req = reqs[-1]
    assert req["method"] == "exec" and req["params"][0]["data"] == {
        "product": "FGT",
        "platform": "FortiGate-100F",
    }
    assert [i.version for i in imgs] == ["7.2.5", "7.4.3"]
    assert imgs[0].name.endswith("7.2.5-b1517") and imgs[0].extra["full_version"] == "7.2.5-b1517"
    await f.rpc("get", "/dvmdb/adom/root/device", verbose=1)
    assert all("verbose" not in r["params"][0] and "verbose" not in r for r in reqs)


# 7. Panorama ---------------------------------------------------------------------------------


def _pan(job_xml: str) -> Any:
    cmds: list[str] = []

    def h(req: httpx.Request) -> httpx.Response:
        form = {
            k: unquote_plus(v)
            for k, v in (x.split("=", 1) for x in req.content.decode().split("&"))
        }
        cmd = form["cmd"]
        cmds.append(cmd)
        ok = "<response status='success'><result>{}</result></response>"
        if "<show><devices><all/>" in cmd:
            return httpx.Response(
                200,
                text=ok.format(
                    "<devices><entry name='01'><serial>01</serial><hostname>fw-a</hostname><model>PA-3410</model></entry>"
                    "<entry name='02'><serial>02</serial><hostname>fw-b</hostname><model>PA-220</model></entry>"
                    "<entry name='03'><serial>03</serial><hostname>fw-c</hostname><model>PA-VM</model></entry></devices>"
                ),
            )
        if "<software><info/>" in cmd:
            return httpx.Response(
                200,
                text=ok.format(
                    "<sw-updates><versions>"
                    "<entry><version>11.1.2</version><filename>PanOS_vm-11.1.2</filename></entry>"
                    "<entry><version>11.1.2</version><filename>PanOS_3400-11.1.2</filename></entry>"
                    "<entry><version>11.1.2</version><filename>PanOS_200-11.1.2</filename></entry>"
                    "</versions></sw-updates>"
                ),
            )
        if "<upload>" in cmd:
            return httpx.Response(200, text=ok.format("<job>21</job>"))
        if "<show><jobs>" in cmd:
            return httpx.Response(200, text=ok.format(job_xml))
        return httpx.Response(200, text="<response status='error'><msg>bad</msg></response>")

    return mk(Panorama, h, token="k"), cmds


async def test_panorama_file_by_model_family_and_device_results() -> None:
    assert Panorama.family_prefix("PA-3410") == "PanOS_3400-"
    assert Panorama.family_prefix("PA-220") == "PanOS_200-"
    assert Panorama.family_prefix("PA-VM") == "PanOS_vm-"
    p, cmds = _pan(
        "<job><status>FIN</status><result>OK</result><devices>"
        "<entry><serial-no>01</serial-no><result>OK</result></entry>"
        "<entry><serial-no>02</serial-no><result>FAIL</result><status>upload failed</status></entry>"
        "</devices></job>"
    )
    for name, fname in [
        ("fw-a", "PanOS_3400-11.1.2"),
        ("fw-b", "PanOS_200-11.1.2"),
        ("fw-c", "PanOS_vm-11.1.2"),
    ]:
        await p.distribute(await p.get_device(name), "11.1.2")
        assert f"<file>{fname}</file>" in cmds[-1]
    failed = await p.job("21")
    assert failed.status == "failed" and "upload failed" in failed.message
    ok, _ = _pan(
        "<job><status>FIN</status><result>OK</result><devices>"
        "<entry><serial-no>01</serial-no><result>OK</result></entry></devices></job>"
    )
    assert (await ok.job("21")).status == "succeeded"
    over = mk(
        Panorama, lambda r: httpx.Response(404), token="k", options={"image_prefix": "PanOS_5400-"}
    )
    assert (
        await over._file(ControllerDevice("1", "x", model="PA-220"), "11.1.2")
        == "PanOS_5400-11.1.2"
    )


# 8. Mist -------------------------------------------------------------------------------------


async def test_mist_fwupdate_polling() -> None:
    dev = {"version": "22.4R3", "fwupdate": {"progress": 10, "status": "inprogress"}}

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v1/sites/s1/stats/devices":
            return httpx.Response(200, json=[{"id": "d1", "name": "sw", "type": "switch", **dev}])
        if req.url.path == "/api/v1/sites/s1/devices/d1/upgrade":
            return httpx.Response(200, json={"status": "inprogress"})
        return httpx.Response(404)

    m = mk(Mist, h, url="", token="t", options={"site_id": "s1"})
    d = await m.get_device("sw")
    stage = await m.distribute(d, "23.4R2")
    assert stage.status == "running"
    assert (await m.job(stage.id)).status == "running"
    dev["fwupdate"] = {"progress": 100, "status": "downloaded"}
    assert (await m.job(stage.id)).status == "succeeded"
    act = await m.activate(d, "23.4R2")
    assert (await m.job(act.id)).status == "running"  # downloaded is not upgraded
    dev["fwupdate"] = {"progress": 50, "status": "failed"}
    assert (await m.job(act.id)).status == "failed"
    dev["fwupdate"] = {"progress": 100, "status": "upgraded"}
    assert (await m.job(act.id)).status == "succeeded"
    dev["fwupdate"] = {}
    dev["version"] = "23.4R2"
    assert (await m.job(act.id)).status == "succeeded"


# 9. Meraki -----------------------------------------------------------------------------------


async def test_meraki_device_paging_follows_link_header() -> None:
    seen: list[dict[str, str]] = []

    def h(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v1/organizations/o1/devices"
        q = dict(req.url.params)
        seen.append(q)
        if "startingAfter" not in q:
            assert q["perPage"] == "1000"
            nxt = "https://api.meraki.com/api/v1/organizations/o1/devices?perPage=1000&startingAfter=Q2AA"
            return httpx.Response(
                200,
                json=[{"serial": "Q2AA", "name": "a"}],
                headers={"Link": f"<{nxt}>; rel=next"},
            )
        return httpx.Response(200, json=[{"serial": "Q2BB", "name": "b"}])

    m = mk(Meraki, h, url="", token="mk", options={"org_id": "o1"})
    assert [d.serial for d in await m.list_devices()] == ["Q2AA", "Q2BB"]
    assert seen[1]["startingAfter"] == "Q2AA"


# 10. Catalyst Center ------------------------------------------------------------------------


async def test_catalyst_center_upgrade_mode_only_when_set() -> None:
    bodies: list[Any] = []

    def h(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/dna/intent/api/v1/image/importation":
            return httpx.Response(
                200, json={"response": [{"imageUuid": "img-1", "name": "x", "version": "17.9.4"}]}
            )
        if p == "/dna/intent/api/v1/image/activation/device":
            bodies.append(body(req))
            return httpx.Response(202, json={"response": {"taskId": "t"}})
        return httpx.Response(404)

    dev = ControllerDevice("uuid-1", "leaf-01")
    await mk(CatalystCenter, h, token="T").activate(dev, "17.9.4")
    assert "deviceUpgradeMode" not in bodies[-1][0]
    await mk(
        CatalystCenter, h, token="T", options={"device_upgrade_mode": "currentlyExists"}
    ).activate(dev, "17.9.4")
    assert bodies[-1][0]["deviceUpgradeMode"] == "currentlyExists"
