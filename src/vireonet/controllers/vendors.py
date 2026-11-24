"""Controller adapters, one class per vendor.

Each docstring names the API it uses. ``UNCERTAIN`` marks calls built from
public documentation but not run against a live controller of that vendor;
docs/ADAPTERS.md lists them and how to check them.
"""

from __future__ import annotations

import base64
import re
import time
import xml.etree.ElementTree as ET  # noqa: S405 — XML from the operator's own Panorama
from typing import Any
from xml.sax.saxutils import escape

import httpx

from vireonet.controllers.base import (
    JOB_FAILED,
    JOB_RUNNING,
    JOB_SUCCEEDED,
    ControllerDevice,
    ControllerError,
    ControllerImage,
    ControllerJob,
    find_image,
)
from vireonet.controllers.http import HttpController


def _int(v: Any) -> int:
    """A non-negative int (FortiManager reports patch -1 when unknown)."""
    try:
        return max(int(v), 0)
    except (TypeError, ValueError):
        return 0


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


# --- Cisco Catalyst Center (DNA Center) -----------------------------------------------


class CatalystCenter(HttpController):
    """Catalyst Center Intent API + SWIM.

    Auth: POST /dna/system/api/v1/auth/token (basic) -> {"Token"}; header X-Auth-Token.
    Devices: GET /dna/intent/api/v1/network-device?hostname=…
    Images: GET /dna/intent/api/v1/image/importation
    Distribute: POST /dna/intent/api/v1/image/distribution [{deviceUuid, imageUuid}]
    Activate: POST /dna/intent/api/v1/image/activation/device [{…, distributeIfNeeded}]
    (deviceUpgradeMode only when options.device_upgrade_mode is set).
    Task: GET /dna/intent/api/v1/task/{taskId} -> {isError, progress, failureReason, endTime}
    """

    vendor = "Cisco Catalyst Center"
    platforms = ("ios", "ios-xe", "nxos")
    native_upgrade = True

    async def login(self, client: httpx.AsyncClient) -> dict[str, str]:
        if self.cfg.token:
            return {"X-Auth-Token": self.cfg.token}
        r = await client.post(
            f"{self.base}/dna/system/api/v1/auth/token",
            headers={"Authorization": _basic(self.cfg.username, self.cfg.password)},
        )
        if r.status_code >= 400:
            raise ControllerError(f"{self.name}: login failed (HTTP {r.status_code})")
        return {"X-Auth-Token": str(r.json()["Token"])}

    @staticmethod
    def _dev(d: dict[str, Any]) -> ControllerDevice:
        return ControllerDevice(
            id=str(d.get("id")),
            name=str(d.get("hostname", "")),
            platform=str(d.get("softwareType", "")).lower(),
            version=str(d.get("softwareVersion", "")),
            serial=str(d.get("serialNumber", "")),
            model=str(d.get("platformId", "")),
            reachable=d.get("reachabilityStatus", "Reachable") == "Reachable",
        )

    async def list_devices(self) -> list[ControllerDevice]:
        data = await self.request("GET", "/dna/intent/api/v1/network-device")
        return [self._dev(d) for d in data.get("response", [])]

    async def get_device(self, name: str) -> ControllerDevice:
        data = await self.request(
            "GET", "/dna/intent/api/v1/network-device", query={"hostname": name}
        )
        items = data.get("response") or []
        if not items:
            raise ControllerError(f"{self.name}: no device {name!r}")
        return self._dev(items[0])

    async def list_images(self, platform: str = "") -> list[ControllerImage]:
        data = await self.request("GET", "/dna/intent/api/v1/image/importation")
        return [
            ControllerImage(
                id=str(i.get("imageUuid")),
                name=str(i.get("name", "")),
                version=str(i.get("version", "")),
                platform=str(i.get("family", "")).lower(),
            )
            for i in data.get("response", [])
        ]

    async def _image(self, version: str) -> ControllerImage:
        return find_image(await self.list_images(), version)

    async def _task(self, data: dict[str, Any]) -> ControllerJob:
        resp = data.get("response") or {}
        return ControllerJob(
            id=str(resp.get("taskId")), status=JOB_RUNNING, message=str(resp.get("url", ""))
        )

    async def distribute(self, device: ControllerDevice, version: str) -> ControllerJob:
        img = await self._image(version)
        return await self._task(
            await self.request(
                "POST",
                "/dna/intent/api/v1/image/distribution",
                json=[{"deviceUuid": device.id, "imageUuid": img.id}],
            )
        )

    async def activate(
        self, device: ControllerDevice, version: str, *, distribute_if_needed: bool = False
    ) -> ControllerJob:
        img = await self._image(version)
        body = [
            {
                "activateLowerImageVersion": True,
                "distributeIfNeeded": distribute_if_needed,
                "deviceUuid": device.id,
                "imageUuidList": [img.id],
            }
        ]
        if self.opt("device_upgrade_mode"):
            body[0]["deviceUpgradeMode"] = str(self.opt("device_upgrade_mode"))
        return await self._task(
            await self.request("POST", "/dna/intent/api/v1/image/activation/device", json=body)
        )

    async def upgrade(self, device: ControllerDevice, version: str) -> ControllerJob:
        return await self.activate(device, version, distribute_if_needed=True)

    async def job(self, job_id: str) -> ControllerJob:
        r = (await self.request("GET", f"/dna/intent/api/v1/task/{job_id}")).get("response") or {}
        if r.get("isError"):
            return ControllerJob(
                job_id,
                JOB_FAILED,
                str(r.get("progress", "")),
                str(r.get("failureReason") or r.get("progress", "")),
            )
        return ControllerJob(
            job_id, JOB_SUCCEEDED if r.get("endTime") else JOB_RUNNING, str(r.get("progress", ""))
        )


# --- Juniper Mist ------------------------------------------------------------------------


class Mist(HttpController):
    """Juniper Mist cloud (switches / EX, SRX).

    Auth: header Authorization: Token <API token>. Options: org_id, site_id.
    Devices: GET /api/v1/sites/{site}/stats/devices?type=all
    Versions: GET /api/v1/sites/{site}/devices/versions?type=switch
    Upgrade: POST /api/v1/sites/{site}/devices/{id}/upgrade {"version", "reboot"}
    (reboot=false only downloads: that is "distribute").
    Job: Mist has no upgrade job id; the device stats ``fwupdate`` {progress, status}
    is polled (status upgraded/success -> done, failed/error -> failed), and a
    reported version equal to the target also counts as done.
    """

    vendor = "Juniper Mist"
    platforms = ("junos",)
    native_upgrade = True
    default_url = "https://api.mist.com"

    async def login(self, client: httpx.AsyncClient) -> dict[str, str]:
        return {"Authorization": f"Token {self.cfg.token}"}

    def _site(self) -> str:
        site = self.opt("site_id")
        if not site:
            raise ControllerError(f"{self.name}: options.site_id is required")
        return str(site)

    async def list_devices(self) -> list[ControllerDevice]:
        rows = await self.request(
            "GET", f"/api/v1/sites/{self._site()}/stats/devices", query={"type": "all"}
        )
        return [
            ControllerDevice(
                id=str(d.get("id")),
                name=str(d.get("name", "")),
                platform="junos"
                if d.get("type") in ("switch", "gateway")
                else str(d.get("type", "")),
                version=str(d.get("version", "")),
                serial=str(d.get("serial", "")),
                model=str(d.get("model", "")),
                reachable=d.get("status") == "connected",
                extra={"fwupdate": d.get("fwupdate") or {}},
            )
            for d in rows
        ]

    async def get_device(self, name: str) -> ControllerDevice:
        for d in await self.list_devices():
            if d.name == name:
                return d
        raise ControllerError(f"{self.name}: no device {name!r} in site {self._site()}")

    async def list_images(self, platform: str = "") -> list[ControllerImage]:
        rows = await self.request(
            "GET", f"/api/v1/sites/{self._site()}/devices/versions", query={"type": "switch"}
        )
        return [
            ControllerImage(
                id=str(v.get("version")),
                name=f"{v.get('model', '')} {v.get('version')}",
                version=str(v.get("version")),
                platform="junos",
                extra={"model": v.get("model"), "tag": v.get("tag")},
            )
            for v in rows
        ]

    async def _upgrade(self, device: ControllerDevice, version: str, reboot: bool) -> ControllerJob:
        await self.request(
            "POST",
            f"/api/v1/sites/{self._site()}/devices/{device.id}/upgrade",
            json={"version": version, "reboot": reboot},
        )
        return ControllerJob(
            id=f"{device.name}|{version}|{'activate' if reboot else 'download'}",
            status=JOB_RUNNING,
            message="upgrade requested" if reboot else "download requested",
        )

    async def distribute(self, device: ControllerDevice, version: str) -> ControllerJob:
        return await self._upgrade(device, version, reboot=False)

    async def activate(self, device: ControllerDevice, version: str) -> ControllerJob:
        return await self._upgrade(device, version, reboot=True)

    upgrade = activate

    async def job(self, job_id: str) -> ControllerJob:
        name, version, kind = job_id.split("|", 2)
        dev = await self.get_device(name)
        fw = dev.extra.get("fwupdate") or {}
        st = str(fw.get("status", "")).lower()
        progress = f"{fw.get('progress')}%" if fw.get("progress") is not None else ""
        done_states: tuple[str, ...] = ("upgraded", "success")
        if kind == "download":
            done_states = ("upgraded", "success", "downloaded")
        if dev.version == version or st in done_states:
            return ControllerJob(
                job_id, JOB_SUCCEEDED, progress or "100%", f"running {dev.version}"
            )
        if st in ("failed", "error"):
            return ControllerJob(job_id, JOB_FAILED, progress, f"fwupdate {st}")
        return ControllerJob(
            job_id, JOB_RUNNING, progress, f"running {dev.version}; waiting for {version} ({st})"
        )


# --- Juniper Apstra ----------------------------------------------------------------------


_JUNOS_VERSION = re.compile(
    r"\d{2}\.\d+[RXFD]\d+(?:\.\d+)?(?:-S\d+(?:\.\d+)?)?(?:-EVO)?", re.IGNORECASE
)
_DOTTED_VERSION = re.compile(r"\d+\.\d+(?:\.\d+)+[A-Za-z]?(?:\.\d+)?")


def _version_in(*texts: Any) -> str:
    """A software version found in free text (Junos style first, then dotted)."""
    for pat in (_JUNOS_VERSION, _DOTTED_VERSION):
        for t in texts:
            m = pat.search(str(t or ""))
            if m:
                return m.group(0)
    return ""


class Apstra(HttpController):
    """Juniper Apstra (data-center fabrics).

    Auth: POST /api/aaa/login {username, password} -> {"token"}; header AuthToken.
    Devices: GET /api/systems (facts.hostname may be null: user_config.hostname,
    then device_key).
    Images: GET /api/device-os/images (id, image_name, description, platform,
    checksum, image_url, type; no version field: it is read from image_name /
    description).
    Agent: GET /api/system-agents, the item whose status.system_id (or system_id)
    is the system's device_key / id; its id is the agent id.
    Upgrade: POST /api/system-agents/{agent_id}/dos-upgrade {"image_id"}.
    Job: GET /api/system-agents/{agent_id} -> last_job_status.state
    (init / inprogress / success / failed).
    """

    vendor = "Juniper Apstra"
    platforms = ("junos", "eos", "nxos", "sonic")
    native_upgrade = True

    async def login(self, client: httpx.AsyncClient) -> dict[str, str]:
        if self.cfg.token:
            return {"AuthToken": self.cfg.token}
        r = await client.post(
            f"{self.base}/api/aaa/login",
            json={"username": self.cfg.username, "password": self.cfg.password},
        )
        if r.status_code >= 400:
            raise ControllerError(f"{self.name}: login failed (HTTP {r.status_code})")
        return {"AuthToken": str(r.json()["token"])}

    async def list_devices(self) -> list[ControllerDevice]:
        items = (await self.request("GET", "/api/systems")).get("items", [])
        out = []
        for d in items:
            facts = d.get("facts") or {}
            user = d.get("user_config") or {}
            key = str(d.get("device_key") or d.get("id") or "")
            out.append(
                ControllerDevice(
                    id=str(d.get("id")),
                    name=str(facts.get("hostname") or user.get("hostname") or key),
                    platform=str(facts.get("os_family") or "").lower(),
                    version=str(facts.get("os_version") or ""),
                    serial=str(facts.get("serial_number") or key),
                    model=str(facts.get("hw_model") or ""),
                    extra={"device_key": key},
                )
            )
        return out

    async def get_device(self, name: str) -> ControllerDevice:
        for d in await self.list_devices():
            if d.name == name or d.extra.get("device_key") == name:
                return d
        raise ControllerError(f"{self.name}: no system {name!r}")

    async def list_images(self, platform: str = "") -> list[ControllerImage]:
        items = (await self.request("GET", "/api/device-os/images")).get("items", [])
        return [
            ControllerImage(
                id=str(i.get("id")),
                name=str(i.get("image_name", "")),
                version=_version_in(i.get("image_name"), i.get("description")),
                platform=str(i.get("platform") or "").lower(),
                extra={k: i.get(k) for k in ("description", "checksum", "image_url", "type")},
            )
            for i in items
        ]

    async def _agent_id(self, device: ControllerDevice) -> str:
        keys = {device.id, str(device.extra.get("device_key") or "")} - {""}
        items = (await self.request("GET", "/api/system-agents")).get("items", [])
        for a in items:
            sid = (a.get("status") or {}).get("system_id") or a.get("system_id")
            if sid and str(sid) in keys:
                return str(a.get("id"))
        raise ControllerError(f"{self.name}: no system agent for {device.name}")

    async def distribute(self, device: ControllerDevice, version: str) -> ControllerJob:
        raise ControllerError(
            f"{self.name}: Apstra stages and installs in one upgrade job: use activate / upgrade"
        )

    async def activate(self, device: ControllerDevice, version: str) -> ControllerJob:
        img = find_image(await self.list_images(), version)
        agent = await self._agent_id(device)
        await self.request(
            "POST", f"/api/system-agents/{agent}/dos-upgrade", json={"image_id": img.id}
        )
        return ControllerJob(id=f"{agent}|{version}", status=JOB_RUNNING)

    upgrade = activate

    async def job(self, job_id: str) -> ControllerJob:
        agent = job_id.split("|", 1)[0]
        a = await self.request("GET", f"/api/system-agents/{agent}")
        last = a.get("last_job_status") or (a.get("status") or {}).get("last_job_status") or {}
        state = str(last.get("state", "init")).lower()
        status = (
            JOB_SUCCEEDED
            if state == "success"
            else JOB_FAILED
            if state in ("failed", "error")
            else JOB_RUNNING
        )
        msg = str(last.get("error_message") or last.get("job_type") or "")
        return ControllerJob(job_id, status, state, msg)


# --- Arista CloudVision ---------------------------------------------------------------------


class CloudVision(HttpController):
    """Arista CloudVision Portal / CloudVision-as-a-Service (cvpservice REST).

    Auth: service-account token, header Authorization: Bearer <token>.
    Devices: GET /cvpservice/inventory/devices
    Images: GET /cvpservice/image/v2/getImageBundles.do
    Upgrade (cvprac apply_image_to_element): stage the "associate" action with
    POST /cvpservice/provisioning/addTempAction.do?format=topology&queryParam=&nodeId=root
    {"data": [item]}, save it with POST /cvpservice/provisioning/v2/saveTopology.do []
    (-> data.taskIds), then POST /cvpservice/task/executeTask.do {"data": taskIds}.
    The item's toName is the device FQDN when inventory reports one.
    Job: GET /cvpservice/task/getTaskById.do?taskId=… (workOrderUserDefinedStatus).

    Note: CVP 2025.2+ may disable the legacy networkprovisioning service these
    calls use; Resource API change controls (/api/resources/changecontrol/v1) are
    the long-term path.
    """

    vendor = "Arista CloudVision"
    platforms = ("eos",)
    native_upgrade = True

    async def login(self, client: httpx.AsyncClient) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.cfg.token}"}

    async def list_devices(self) -> list[ControllerDevice]:
        rows = await self.request("GET", "/cvpservice/inventory/devices")
        return [
            ControllerDevice(
                id=str(d.get("systemMacAddress")),
                name=str(d.get("hostname", "")),
                platform="eos",
                version=str(d.get("version", "")),
                serial=str(d.get("serialNumber", "")),
                model=str(d.get("modelName", "")),
                reachable=d.get("streamingStatus", "active") == "active",
                extra={"fqdn": d.get("fqdn") or ""},
            )
            for d in rows
        ]

    async def get_device(self, name: str) -> ControllerDevice:
        for d in await self.list_devices():
            if d.name == name or (d.extra.get("fqdn") and d.extra["fqdn"] == name):
                return d
        raise ControllerError(f"{self.name}: no device {name!r}")

    async def list_images(self, platform: str = "") -> list[ControllerImage]:
        data = await self.request(
            "GET", "/cvpservice/image/v2/getImageBundles.do", query={"startIndex": 0, "endIndex": 0}
        )
        out = []
        for b in data.get("data", []):
            name = str(b.get("name", ""))
            version = name.split("-", 1)[1] if name.upper().startswith("EOS-") else name
            out.append(
                ControllerImage(
                    id=str(b.get("key") or b.get("id")), name=name, version=version, platform="eos"
                )
            )
        return out

    async def distribute(self, device: ControllerDevice, version: str) -> ControllerJob:
        raise ControllerError(
            f"{self.name}: CloudVision stages and installs in one task: use activate / upgrade"
        )

    async def activate(self, device: ControllerDevice, version: str) -> ControllerJob:
        img = find_image(await self.list_images(), version)
        item: dict[str, Any] = {
            "info": f"Apply image: {img.name} to {device.name}",
            "infoPreview": f"<b>Apply image: {img.name}</b> to {device.name}",
            "note": "",
            "action": "associate",
            "nodeType": "imagebundle",
            "nodeId": img.id,
            "toId": device.id,
            "toIdType": "netelement",
            "fromId": "",
            "nodeName": img.name,
            "fromName": "",
            "toName": device.extra.get("fqdn") or device.name,
            "childTasks": [],
            "parentTask": "",
        }
        await self.request(
            "POST",
            "/cvpservice/provisioning/addTempAction.do",
            query={"format": "topology", "queryParam": "", "nodeId": "root"},
            json={"data": [item]},
        )
        saved = await self.request("POST", "/cvpservice/provisioning/v2/saveTopology.do", json=[])
        tasks = [str(t) for t in ((saved.get("data") or {}).get("taskIds") or [])]
        if not tasks:
            raise ControllerError(f"{self.name}: CloudVision created no task for {device.name}")
        await self.request("POST", "/cvpservice/task/executeTask.do", json={"data": tasks})
        return ControllerJob(id=tasks[0], status=JOB_RUNNING)

    upgrade = activate

    async def job(self, job_id: str) -> ControllerJob:
        t = await self.request("GET", "/cvpservice/task/getTaskById.do", query={"taskId": job_id})
        st = str(t.get("workOrderUserDefinedStatus", "")).lower()
        status = (
            JOB_SUCCEEDED
            if st == "completed"
            else JOB_FAILED
            if st in ("failed", "cancelled")
            else JOB_RUNNING
        )
        return ControllerJob(job_id, status, st)


# --- Fortinet FortiManager ------------------------------------------------------------------


class FortiManager(HttpController):
    """FortiManager JSON-RPC (/jsonrpc).

    Auth: API key (Authorization: Bearer) or exec /sys/login/user -> session.
    Devices: get /dvmdb/adom/{adom}/device. Option: adom (default "root").
    Images: exec /um/image/version/list {"product": "FGT", "platform": <model>}
    -> version_list[].versions[] (version "7.2.5-b1517": the -bNNNN build suffix is
    dropped from ControllerImage.version and kept in name / extra).
    UNCERTAIN: firmware upgrade exec /um/image/upgrade {adom, device, image.release,
    create_task}; task get /task/task/{id}.
    """

    vendor = "Fortinet FortiManager"
    platforms = ("fortios",)
    native_upgrade = True

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._session = ""
        self._id = 0

    async def login(self, client: httpx.AsyncClient) -> dict[str, str]:
        if self.cfg.token:
            return {"Authorization": f"Bearer {self.cfg.token}"}
        r = await client.post(
            f"{self.base}/jsonrpc",
            json={
                "id": 1,
                "method": "exec",
                "params": [
                    {
                        "url": "/sys/login/user",
                        "data": {"user": self.cfg.username, "passwd": self.cfg.password},
                    }
                ],
            },
        )
        self._session = str(r.json().get("session", ""))
        if not self._session:
            raise ControllerError(f"{self.name}: login failed")
        return {"Accept": "application/json"}  # the session itself goes in each request body

    async def rpc(self, method: str, url: str, **params: Any) -> Any:
        if not self._auth:  # log in first: the session goes into the request body
            async with httpx.AsyncClient(
                timeout=self.cfg.timeout_seconds,
                verify=self.cfg.verify_tls,
                transport=self._transport,
            ) as client:
                self._auth = await self.login(client)
        params.pop("verbose", None)  # verbose output changes the reply shape
        self._id += 1
        body: dict[str, Any] = {
            "id": self._id,
            "method": method,
            "params": [{"url": url, **params}],
        }
        data = await self.request(
            "POST",
            "/jsonrpc",
            json={**body, **({"session": self._session} if self._session else {})},
        )
        result = (data.get("result") or [{}])[0]
        code = (result.get("status") or {}).get("code", 0)
        if code != 0:
            raise ControllerError(
                f"{self.name}: {method} {url}: {(result.get('status') or {}).get('message')}"
            )
        return result.get("data")

    def _adom(self) -> str:
        return str(self.opt("adom", "root"))

    async def list_devices(self) -> list[ControllerDevice]:
        rows = (
            await self.rpc(
                "get",
                f"/dvmdb/adom/{self._adom()}/device",
                fields=["name", "sn", "platform_str", "os_ver", "mr", "patch", "conn_status"],
            )
            or []
        )
        return [
            ControllerDevice(
                id=str(d.get("name")),
                name=str(d.get("name")),
                platform="fortios",
                version=f"{_int(d.get('os_ver'))}.{_int(d.get('mr'))}.{_int(d.get('patch'))}",
                serial=str(d.get("sn", "")),
                model=str(d.get("platform_str", "")),
                reachable=d.get("conn_status") in (1, "up"),
            )
            for d in rows
        ]

    async def get_device(self, name: str) -> ControllerDevice:
        for d in await self.list_devices():
            if d.name == name:
                return d
        raise ControllerError(f"{self.name}: no device {name!r} in ADOM {self._adom()}")

    async def list_images(
        self, platform: str = "", device: ControllerDevice | None = None
    ) -> list[ControllerImage]:
        query: dict[str, Any] = {"product": str(self.opt("product", "FGT"))}
        model = (device.model if device else "") or str(self.opt("platform", ""))
        if model:
            query["platform"] = model
        data = await self.rpc("exec", "/um/image/version/list", data=query) or {}
        groups = data.get("version_list", []) if isinstance(data, dict) else data
        out = []
        for g in groups or []:
            for v in g.get("versions", []) or []:
                full = str(v.get("version") or v.get("release") or "")
                ver = re.sub(r"-b\d+$", "", full)
                out.append(
                    ControllerImage(
                        id=full,
                        name=f"{g.get('platform', 'FortiGate')} {full}",
                        version=ver,
                        platform="fortios",
                        extra={"full_version": full, "platform": g.get("platform"), **v},
                    )
                )
        return out

    async def distribute(self, device: ControllerDevice, version: str) -> ControllerJob:
        raise ControllerError(
            f"{self.name}: FortiManager downloads and installs in one upgrade task: use activate / upgrade"
        )

    async def activate(self, device: ControllerDevice, version: str) -> ControllerJob:
        data = await self.rpc(
            "exec",
            "/um/image/upgrade",
            data={
                "adom": self._adom(),
                "create_task": "enable",
                "device": [{"name": device.name, "vdom": "root"}],
                "image": {"release": version},
            },
        )
        return ControllerJob(id=str((data or {}).get("taskid")), status=JOB_RUNNING)

    upgrade = activate

    async def job(self, job_id: str) -> ControllerJob:
        t = await self.rpc("get", f"/task/task/{job_id}") or {}
        if int(t.get("num_err", 0) or 0) > 0:
            return ControllerJob(
                job_id, JOB_FAILED, f"{t.get('percent', 0)}%", "the upgrade task reported errors"
            )
        return ControllerJob(
            job_id,
            JOB_SUCCEEDED if int(t.get("percent", 0) or 0) >= 100 else JOB_RUNNING,
            f"{t.get('percent', 0)}%",
        )


# --- Palo Alto Networks Panorama -------------------------------------------------------------


class Panorama(HttpController):
    """Panorama XML API (managed firewalls).

    Auth: type=keygen -> key, header X-PAN-KEY (or an API key as token).
    Devices: op <show><devices><all/></devices></show>
    Distribute: op <request><batch><software><upload><file>…</file><devices>serial</devices>
    Activate: op <request><batch><software><install><file>…</file><devices>serial</devices>
    then <request><batch><reboot><devices>serial</devices></reboot></batch></request>.
    Job: op <show><jobs><id>N</id></jobs></show>: done when status FIN; succeeded only
    when the job result is OK and every .//devices/entry/result is OK.
    Image file: picked from the repository listing (<request><batch><software><info/>)
    by the firewall's model family (PA-3410 -> PanOS_3400-, PA-220 -> PanOS_200-,
    VM-series -> PanOS_vm-); options.image_prefix overrides the family prefix.
    """

    vendor = "Palo Alto Panorama"
    platforms = ("panos",)
    native_upgrade = False

    async def login(self, client: httpx.AsyncClient) -> dict[str, str]:
        if self.cfg.token:
            return {"X-PAN-KEY": self.cfg.token}
        r = await client.post(
            f"{self.base}/api/",
            data={"type": "keygen", "user": self.cfg.username, "password": self.cfg.password},
        )
        key = ET.fromstring(r.text).findtext(".//key")  # noqa: S314
        if not key:
            raise ControllerError(f"{self.name}: keygen failed")
        return {"X-PAN-KEY": key}

    async def op(self, cmd: str) -> ET.Element:
        text = await self.request(
            "POST", "/api/", data={"type": "op", "cmd": cmd}, expect_json=False
        )
        root = ET.fromstring(text)  # noqa: S314
        if root.get("status") != "success":
            raise ControllerError(f"{self.name}: {cmd[:60]}…: {''.join(root.itertext())[:200]}")
        return root

    async def list_devices(self) -> list[ControllerDevice]:
        root = await self.op("<show><devices><all/></devices></show>")
        return [
            ControllerDevice(
                id=str(e.get("name")),
                name=str(e.findtext("hostname", "")),
                platform="panos",
                version=str(e.findtext("sw-version", "")),
                serial=str(e.findtext("serial", e.get("name"))),
                model=str(e.findtext("model", "")),
                reachable=e.findtext("connected") == "yes",
            )
            for e in root.iter("entry")
            if e.find("hostname") is not None
        ]

    async def get_device(self, name: str) -> ControllerDevice:
        for d in await self.list_devices():
            if d.name == name or d.serial == name:
                return d
        raise ControllerError(f"{self.name}: no managed firewall {name!r}")

    async def list_images(self, platform: str = "") -> list[ControllerImage]:
        root = await self.op("<request><batch><software><info/></software></batch></request>")
        return [
            ControllerImage(
                id=str(e.findtext("filename", e.findtext("version", ""))),
                name=str(e.findtext("filename", "")),
                version=str(e.findtext("version", "")),
                platform="panos",
            )
            for e in root.iter("entry")
            if e.findtext("version")
        ]

    @staticmethod
    def family_prefix(model: str) -> str:
        """The repository file prefix for a firewall model's family."""
        m = model.upper()
        if "VM" in m or not m:
            return "PanOS_vm-"
        digits = re.search(r"(\d{3,4})", m)
        if not digits:
            return "PanOS_vm-"
        return f"PanOS_{digits.group(1)[:-2]}00-"

    async def _file(self, device: ControllerDevice, version: str) -> str:
        override = self.opt("image_prefix")
        if override:
            return f"{override}{version}"
        prefix = self.family_prefix(device.model)
        for img in await self.list_images():
            if img.name.startswith(prefix) and (
                img.version == version or img.name == f"{prefix}{version}"
            ):
                return img.name
        return f"{prefix}{version}"

    async def _job(self, root: ET.Element) -> ControllerJob:
        job = root.findtext(".//job")
        if not job:
            raise ControllerError(f"{self.name}: Panorama returned no job id")
        return ControllerJob(id=job, status=JOB_RUNNING)

    async def distribute(self, device: ControllerDevice, version: str) -> ControllerJob:
        f, s = escape(await self._file(device, version)), escape(device.serial)
        return await self._job(
            await self.op(
                f"<request><batch><software><upload><file>{f}</file><devices>{s}</devices></upload></software></batch></request>"
            )
        )

    async def activate(self, device: ControllerDevice, version: str) -> ControllerJob:
        f, s = escape(await self._file(device, version)), escape(device.serial)
        job = await self._job(
            await self.op(
                f"<request><batch><software><install><file>{f}</file><devices>{s}</devices></install></software></batch></request>"
            )
        )
        job.extra = {"reboot_serial": device.serial}
        job.id = f"{job.id}|reboot:{device.serial}"
        return job

    async def upgrade(self, device: ControllerDevice, version: str) -> ControllerJob:
        raise ControllerError(
            f"{self.name}: use distribute then activate (Panorama has no one-step upgrade)"
        )

    async def job(self, job_id: str) -> ControllerJob:
        jid, _, after = job_id.partition("|")
        root = await self.op(f"<show><jobs><id>{escape(jid)}</id></jobs></show>")
        node = root.find(".//job")
        if node is None:
            raise ControllerError(f"{self.name}: no job {jid}")
        status, result = node.findtext("status", ""), node.findtext("result", "")
        if status != "FIN":
            return ControllerJob(job_id, JOB_RUNNING, node.findtext("progress", ""))
        bad = [
            f"{e.get('name') or e.findtext('serial-no') or '?'}: {e.findtext('status', '')}"
            for e in node.findall(".//devices/entry")
            if e.findtext("result", "") != "OK"
        ]
        if result != "OK" or bad:
            lines = "; ".join(x.text or "" for x in root.iter("line"))
            return ControllerJob(
                job_id,
                JOB_FAILED,
                "100",
                "; ".join(x for x in (lines, "; ".join(bad)) if x)[:300] or result,
            )
        if after.startswith("reboot:"):
            reboot = await self._job(
                await self.op(
                    f"<request><batch><reboot><devices>{escape(after[7:])}</devices></reboot></batch></request>"
                )
            )
            return ControllerJob(reboot.id, JOB_RUNNING, "installed; rebooting")
        return ControllerJob(job_id, JOB_SUCCEEDED, "100")


# --- Nokia NSP -----------------------------------------------------------------------------------


class NokiaNsp(HttpController):
    """Nokia Network Services Platform.

    Auth: POST /rest-gateway/rest/api/v1/auth/token (basic, grant_type=client_credentials)
    -> access_token (Bearer).
    Devices: GET /restconf/data/nsp-equipment:network/network-element
    Upgrade: runs an NSP Workflow Manager workflow (option upgrade_workflow, e.g. the
    operator's SR OS upgrade workflow): POST /wfm/api/v1/execution
    {workflow_id, input, params: {env: options.wfm_env or "DefaultEnv"}} -> response.data
    is an array of executions (id = response.data[0].id), polled with
    GET /wfm/api/v1/execution/{id} (SUCCESS -> succeeded; FAILED / CANCELED /
    CANCELLED / ERROR -> failed; else running).
    NSP also has a native LSO operation, nsp-ne-upgrade-with-phases, which may
    replace the custom workflow.
    UNVERIFIABLE: RESTCONF field names, the WFM execution envelope and the GET
    execution path were not run against a live NSP.
    """

    vendor = "Nokia NSP"
    platforms = ("sros", "srlinux")
    native_upgrade = True

    async def login(self, client: httpx.AsyncClient) -> dict[str, str]:
        if self.cfg.token:
            return {"Authorization": f"Bearer {self.cfg.token}"}
        r = await client.post(
            f"{self.base}/rest-gateway/rest/api/v1/auth/token",
            json={"grant_type": "client_credentials"},
            headers={"Authorization": _basic(self.cfg.username, self.cfg.password)},
        )
        if r.status_code >= 400:
            raise ControllerError(f"{self.name}: login failed (HTTP {r.status_code})")
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    async def list_devices(self) -> list[ControllerDevice]:
        data = await self.request("GET", "/restconf/data/nsp-equipment:network/network-element")
        rows = data.get("nsp-equipment:network-element") or data.get("network-element") or []
        return [
            ControllerDevice(
                id=str(n.get("ne-id")),
                name=str(n.get("ne-name", "")),
                platform="srlinux" if "SRL" in str(n.get("product", "")).upper() else "sros",
                version=str(n.get("version", "")),
                serial=str(n.get("serial-number", "")),
                model=str(n.get("product", "")),
            )
            for n in rows
        ]

    async def get_device(self, name: str) -> ControllerDevice:
        for d in await self.list_devices():
            if d.name == name or d.id == name:
                return d
        raise ControllerError(f"{self.name}: no network element {name!r}")

    async def list_images(self, platform: str = "") -> list[ControllerImage]:
        return [
            ControllerImage(id=str(v), name=f"SR OS {v}", version=str(v), platform="sros")
            for v in self.opt("versions", [])
        ]

    async def distribute(self, device: ControllerDevice, version: str) -> ControllerJob:
        return await self._run(device, version, "stage")

    async def activate(self, device: ControllerDevice, version: str) -> ControllerJob:
        return await self._run(device, version, "activate")

    async def upgrade(self, device: ControllerDevice, version: str) -> ControllerJob:
        return await self._run(device, version, "full")

    async def _run(self, device: ControllerDevice, version: str, phase: str) -> ControllerJob:
        wf = self.opt("upgrade_workflow")
        if not wf:
            raise ControllerError(
                f"{self.name}: options.upgrade_workflow (the NSP workflow that upgrades a node) is required"
            )
        data = await self.request(
            "POST",
            "/wfm/api/v1/execution",
            json={
                "workflow_id": wf,
                "input": {
                    "neId": device.id,
                    "neName": device.name,
                    "version": version,
                    "phase": phase,
                },
                "params": {"env": str(self.opt("wfm_env", "DefaultEnv"))},
            },
        )
        ex = self._execution(data)
        if not ex.get("id"):
            raise ControllerError(f"{self.name}: NSP returned no workflow execution id")
        return ControllerJob(id=str(ex.get("id")), status=JOB_RUNNING)

    @staticmethod
    def _execution(data: Any) -> dict[str, Any]:
        """The execution inside {"response": {"data": [ {...} ] | {...}}}."""
        ex: Any = data
        if isinstance(data, dict) and isinstance(data.get("response"), dict):
            ex = data["response"].get("data", data)
        if isinstance(ex, list):
            ex = ex[0] if ex else {}
        return ex if isinstance(ex, dict) else {}

    async def job(self, job_id: str) -> ControllerJob:
        ex = self._execution(await self.request("GET", f"/wfm/api/v1/execution/{job_id}"))
        state = str(ex.get("state", "")).upper()
        status = (
            JOB_SUCCEEDED
            if state == "SUCCESS"
            else JOB_FAILED
            if state in ("FAILED", "CANCELED", "CANCELLED", "ERROR")
            else JOB_RUNNING
        )
        return ControllerJob(job_id, status, state, str(ex.get("state_info", "")))


# --- Extreme ExtremeCloud IQ ------------------------------------------------------------------------


_XIQ_FAILED = ("FAILED", "TIME_OUT", "ABORTED", "CANCELED", "CANCELLED")


class ExtremeCloudIq(HttpController):
    """ExtremeCloud IQ (cloud-managed Extreme switches / APs).

    Auth: POST /login {username, password} -> access_token (Bearer), or an API token.
    Devices: GET /devices?views=FULL&page=N&limit=100 (pages up to total_pages).
    Versions: POST /deployments/firmware-metadatas {"device_ids": [id]}
    -> firmware_metadatas {<key>: [{firmware_id, version, display_version, …}]}.
    Upgrade: POST /deployments?async=true {devices.ids, policy{
    enable_complete_configuration_update, firmware_upgrade_policy.enable_enforce_upgrade,
    firmware_upgrade_versions[{firmware_id, device_id, product_type}],
    firmware_activate_option}}: activation_delay_seconds 0 activates now,
    enable_activate_at_next_reboot only stages (distribute).
    Job: GET /deployments/{id}/status -> schedule_status (COMPLETED / FAILED /
    TIME_OUT / ABORTED); an async reply that is an operation is first followed with
    GET /operations/{id} until it yields the deployment id.
    """

    vendor = "ExtremeCloud IQ"
    platforms = ("exos", "voss")
    native_upgrade = True
    default_url = "https://api.extremecloudiq.com"
    page_limit = 100
    max_pages = 50

    async def login(self, client: httpx.AsyncClient) -> dict[str, str]:
        if self.cfg.token:
            return {"Authorization": f"Bearer {self.cfg.token}"}
        r = await client.post(
            f"{self.base}/login",
            json={"username": self.cfg.username, "password": self.cfg.password},
        )
        if r.status_code >= 400:
            raise ControllerError(f"{self.name}: login failed (HTTP {r.status_code})")
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    @staticmethod
    def _dev(d: dict[str, Any]) -> ControllerDevice:
        os_name = str(d.get("device_function") or d.get("os_type") or "").lower()
        return ControllerDevice(
            id=str(d.get("id")),
            name=str(d.get("hostname", "")),
            platform="voss" if "voss" in os_name or "fabric" in os_name else "exos",
            version=str(d.get("software_version", "")),
            serial=str(d.get("serial_number", "")),
            model=str(d.get("product_type", "")),
            reachable=bool(d.get("connected", True)),
            extra={"product_type": d.get("product_type")},
        )

    async def list_devices(self) -> list[ControllerDevice]:
        out: list[ControllerDevice] = []
        page = 1
        while page <= self.max_pages:
            data = await self.request(
                "GET", "/devices", query={"views": "FULL", "page": page, "limit": self.page_limit}
            )
            rows = data.get("data", []) or []
            out.extend(self._dev(d) for d in rows)
            total = data.get("total_pages")
            if total is not None:
                if page >= int(total):
                    break
            elif len(rows) < self.page_limit:
                break
            page += 1
        return out

    async def get_device(self, name: str) -> ControllerDevice:
        for d in await self.list_devices():
            if d.name == name or d.serial == name:
                return d
        raise ControllerError(f"{self.name}: no device {name!r}")

    @staticmethod
    def _id(device: ControllerDevice) -> int | str:
        return int(device.id) if device.id.isdigit() else device.id

    async def list_images(
        self, platform: str = "", device: ControllerDevice | None = None
    ) -> list[ControllerImage]:
        if device is None:
            devs = await self.list_devices()
            if not devs:
                return []
            device = devs[0]
        data = await self.request(
            "POST", "/deployments/firmware-metadatas", json={"device_ids": [self._id(device)]}
        )
        meta = data.get("firmware_metadatas") or {}
        rows = meta.get(device.id) or meta.get(str(device.id))
        if rows is None:
            rows = [r for v in meta.values() for r in (v or [])]
        return [
            ControllerImage(
                id=str(v.get("firmware_id")),
                name=f"{v.get('display_version') or v.get('version')} ({v.get('version')})",
                version=str(v.get("version")),
                platform=device.platform or "exos",
                extra={"firmware_id": v.get("firmware_id"), **v},
            )
            for v in rows
        ]

    async def _upgrade(
        self, device: ControllerDevice, version: str, activate: bool
    ) -> ControllerJob:
        img = find_image(await self.list_images(device=device), version)
        fid = img.extra.get("firmware_id", img.id)
        did = self._id(device)
        body = {
            "devices": {"ids": [did]},
            "policy": {
                "enable_complete_configuration_update": True,
                "firmware_upgrade_policy": {"enable_enforce_upgrade": True},
                "firmware_upgrade_versions": [
                    {
                        "firmware_id": int(fid) if str(fid).isdigit() else fid,
                        "device_id": did,
                        "product_type": device.extra.get("product_type") or device.model,
                    }
                ],
                "firmware_activate_option": {"activation_delay_seconds": 0}
                if activate
                else {"enable_activate_at_next_reboot": True},
            },
        }
        r = await self.request("POST", "/deployments", query={"async": "true"}, json=body)
        return ControllerJob(id=self._job_ref(r), status=JOB_RUNNING, extra={"version": version})

    @staticmethod
    def _job_ref(r: Any) -> str:
        """dep:<deployment id> or op:<operation id> from a deployment reply."""
        if not isinstance(r, dict):
            raise ControllerError("ExtremeCloud IQ returned no deployment")
        if r.get("deployment_id"):
            return f"dep:{r['deployment_id']}"
        if "done" in r or "metadata" in r or r.get("operation_id"):
            op = r.get("operation_id") or r.get("id") or str(r.get("name", "")).rsplit("/", 1)[-1]
            if op:
                return f"op:{op}"
        if r.get("id"):
            return f"dep:{r['id']}"
        raise ControllerError("ExtremeCloud IQ returned no deployment or operation id")

    async def distribute(self, device: ControllerDevice, version: str) -> ControllerJob:
        return await self._upgrade(device, version, activate=False)

    async def activate(self, device: ControllerDevice, version: str) -> ControllerJob:
        return await self._upgrade(device, version, activate=True)

    upgrade = activate

    async def job(self, job_id: str) -> ControllerJob:
        kind, _, ref = job_id.partition(":")
        if kind == "op":
            op = await self.request("GET", f"/operations/{ref}")
            if op.get("error"):
                return ControllerJob(job_id, JOB_FAILED, "", str(op["error"])[:300])
            if not op.get("done"):
                return ControllerJob(job_id, JOB_RUNNING, "operation pending")
            resp = op.get("response") or {}
            dep = (resp.get("deployment_id") or resp.get("id")) if isinstance(resp, dict) else None
            if not dep:
                return ControllerJob(
                    job_id, JOB_FAILED, "", "operation finished without a deployment"
                )
            ref = str(dep)
        data = await self.request("GET", f"/deployments/{ref}/status")
        states = self._states(data)
        if any(s in _XIQ_FAILED for s in states):
            st = JOB_FAILED
        elif states and all(s == "COMPLETED" for s in states):
            st = JOB_SUCCEEDED
        else:
            st = JOB_RUNNING
        job = ControllerJob(f"dep:{ref}", st, ",".join(states))
        if st == JOB_FAILED:
            job.message = f"deployment {ref}: {','.join(states)}"
        return job

    @staticmethod
    def _states(data: Any) -> list[str]:
        """schedule_status values: top level, per-device map, or a data list."""
        if isinstance(data, dict) and "schedule_status" in data:
            return [str(data["schedule_status"]).upper()]
        rows: list[Any] = []
        if isinstance(data, dict):
            inner = data.get("data")
            rows = inner if isinstance(inner, list) else list(data.values())
        elif isinstance(data, list):
            rows = data
        return [
            str(r["schedule_status"]).upper()
            for r in rows
            if isinstance(r, dict) and r.get("schedule_status")
        ]


# --- Cisco Meraki ----------------------------------------------------------------------------------------


class Meraki(HttpController):
    """Cisco Meraki Dashboard API v1.

    Auth: Authorization: Bearer <API key>. Options: org_id (devices), product (switch).
    Devices: GET /organizations/{org}/devices?perPage=1000, following the Link
    header's rel=next (or startingAfter=<last serial> when a page is full).
    Versions: GET /networks/{net}/firmwareUpgrades (products.<product>.availableVersions)
    Upgrade: PUT /networks/{net}/firmwareUpgrades with products.<product>.nextUpgrade
    {time, toVersion.id}: Meraki upgrades every device of that product in the network.
    Job: the device's reported firmware, polled.
    """

    vendor = "Cisco Meraki"
    platforms = ("meraki",)
    native_upgrade = True
    default_url = "https://api.meraki.com/api/v1"
    per_page = 1000
    max_pages = 100

    async def login(self, client: httpx.AsyncClient) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.cfg.token}"}

    async def list_devices(self) -> list[ControllerDevice]:
        org = self.opt("org_id")
        if not org:
            raise ControllerError(f"{self.name}: options.org_id is required")
        rows = await self._pages(f"/organizations/{org}/devices")
        return [
            ControllerDevice(
                id=str(d.get("serial")),
                name=str(d.get("name") or d.get("serial")),
                platform="meraki",
                version=str(d.get("firmware", "")),
                serial=str(d.get("serial", "")),
                model=str(d.get("model", "")),
                extra={"network_id": d.get("networkId"), "product": d.get("productType")},
            )
            for d in rows
        ]

    async def _pages(self, path: str) -> list[dict[str, Any]]:
        """Every page of a Meraki list (Link: <…>; rel=next, or startingAfter)."""
        url: str = f"{self.base}{path}"
        query: dict[str, Any] | None = {"perPage": self.per_page}
        out: list[dict[str, Any]] = []
        headers = {"Authorization": f"Bearer {self.cfg.token}"}
        async with httpx.AsyncClient(
            timeout=self.cfg.timeout_seconds, verify=self.cfg.verify_tls, transport=self._transport
        ) as client:
            for _ in range(self.max_pages):
                resp = await client.get(url, params=query, headers=headers)
                if resp.status_code >= 400:
                    raise ControllerError(
                        f"{self.name}: GET {path} -> HTTP {resp.status_code}: {resp.text[:300]}"
                    )
                rows = resp.json() if resp.content else []
                out.extend(rows)
                nxt = resp.links.get("next", {}).get("url")
                if nxt:
                    url, query = nxt, None
                elif len(rows) >= self.per_page and rows and rows[-1].get("serial"):
                    url = f"{self.base}{path}"
                    query = {"perPage": self.per_page, "startingAfter": rows[-1]["serial"]}
                else:
                    break
        return out

    async def get_device(self, name: str) -> ControllerDevice:
        for d in await self.list_devices():
            if d.name == name or d.serial == name:
                return d
        raise ControllerError(f"{self.name}: no device {name!r}")

    def _product(self, device: ControllerDevice | None = None) -> str:
        return str(
            (device.extra.get("product") if device else None) or self.opt("product", "switch")
        )

    async def list_images(
        self, platform: str = "", device: ControllerDevice | None = None
    ) -> list[ControllerImage]:
        net = (device.extra.get("network_id") if device else None) or self.opt("network_id")
        if not net:
            raise ControllerError(f"{self.name}: options.network_id is required to list versions")
        fw = await self.request("GET", f"/networks/{net}/firmwareUpgrades")
        prod = ((fw.get("products") or {}).get(self._product(device))) or {}
        return [
            ControllerImage(
                id=str(v.get("id")),
                name=str(v.get("shortName", v.get("firmware"))),
                version=str(v.get("firmware") or v.get("shortName")),
                platform="meraki",
                extra={"release": v.get("releaseType")},
            )
            for v in prod.get("availableVersions", [])
        ]

    async def distribute(self, device: ControllerDevice, version: str) -> ControllerJob:
        raise ControllerError(f"{self.name}: Meraki schedules the whole upgrade: use upgrade")

    async def upgrade(self, device: ControllerDevice, version: str) -> ControllerJob:
        img = find_image(await self.list_images(device=device), version)
        net = device.extra.get("network_id") or self.opt("network_id")
        when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 60))
        await self.request(
            "PUT",
            f"/networks/{net}/firmwareUpgrades",
            json={
                "products": {
                    self._product(device): {
                        "nextUpgrade": {"time": when, "toVersion": {"id": img.id}}
                    }
                }
            },
        )
        return ControllerJob(
            id=f"{device.serial}|{img.version}", status=JOB_RUNNING, message=f"scheduled for {when}"
        )

    activate = upgrade

    async def job(self, job_id: str) -> ControllerJob:
        serial, version = job_id.split("|", 1)
        d = await self.request("GET", f"/devices/{serial}")
        cur = str(d.get("firmware", ""))
        return ControllerJob(
            job_id, JOB_SUCCEEDED if version in cur else JOB_RUNNING, f"running {cur}"
        )


# --- HPE Aruba Networking Central ---------------------------------------------------------------------------


class ArubaCentral(HttpController):
    """HPE Aruba Networking Central (AOS-CX / AOS-S switches).

    Auth: OAuth access token, Authorization: Bearer <token>.
    Devices: GET /monitoring/v1/switches (switch_type AOS-CX -> device_type "CX",
    AOS-S -> "HP"; else options.device_type, default "CX").
    Versions: GET /firmware/v1/versions?device_type=… -> {data: [{firmware_version,
    release_status}]}.
    Upgrade: POST /firmware/v1/upgrade {serial, device_type, firmware_version, reboot}
    -> {success_list, failed_list, message} (no task id: the job id is serial|version).
    Job: GET /firmware/v1/status?serial=… -> {state, reason}; state is free text
    ("fail" -> failed; "success" / "complete" / "upgraded" -> succeeded), and the
    switch reporting firmware_version == target also counts as done.
    """

    vendor = "HPE Aruba Central"
    platforms = ("aos-cx", "aos-s")
    native_upgrade = True

    async def login(self, client: httpx.AsyncClient) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.cfg.token}"}

    def _device_type(self, kind: str) -> str:
        k = kind.upper().replace("_", "-")
        if "CX" in k:
            return "CX"
        if "AOS-S" in k or k in ("HP", "HPPC", "AOS-SWITCH", "ARUBAOS-SWITCH"):
            return "HP"
        return str(self.opt("device_type", "CX"))

    async def list_devices(self) -> list[ControllerDevice]:
        rows = (await self.request("GET", "/monitoring/v1/switches")).get("switches", [])
        out = []
        for d in rows:
            dtype = self._device_type(
                str(d.get("switch_type") or d.get("device_type") or d.get("type") or "")
                or str(d.get("model") or "")
            )
            out.append(
                ControllerDevice(
                    id=str(d.get("serial")),
                    name=str(d.get("name", "")),
                    platform="aos-s" if dtype == "HP" else "aos-cx",
                    version=str(d.get("firmware_version", "")),
                    serial=str(d.get("serial", "")),
                    model=str(d.get("model", "")),
                    reachable=str(d.get("status", "Up")).lower() == "up",
                    extra={"device_type": dtype},
                )
            )
        return out

    async def get_device(self, name: str) -> ControllerDevice:
        for d in await self.list_devices():
            if d.name == name or d.serial == name:
                return d
        raise ControllerError(f"{self.name}: no switch {name!r}")

    async def list_images(
        self, platform: str = "", device: ControllerDevice | None = None
    ) -> list[ControllerImage]:
        dtype = (
            device.extra.get("device_type")
            if device
            else self._device_type(platform)
            if platform
            else self.opt("device_type", "CX")
        )
        rows = await self.request("GET", "/firmware/v1/versions", query={"device_type": dtype})
        items = rows if isinstance(rows, list) else rows.get("data") or rows.get("versions") or []
        return [
            ControllerImage(
                id=str(v.get("firmware_version")),
                name=str(v.get("firmware_version")),
                version=str(v.get("firmware_version")),
                platform="aos-s" if dtype == "HP" else "aos-cx",
                extra={"release_status": v.get("release_status")},
            )
            for v in items
        ]

    async def _upgrade(self, device: ControllerDevice, version: str, reboot: bool) -> ControllerJob:
        r = await self.request(
            "POST",
            "/firmware/v1/upgrade",
            json={
                "serial": device.serial,
                "device_type": device.extra.get("device_type") or self.opt("device_type", "CX"),
                "firmware_version": version,
                "reboot": reboot,
            },
        )
        failed = (r.get("failed_list") or []) if isinstance(r, dict) else []
        succeeded = str((r.get("success_list") or []) if isinstance(r, dict) else [])
        for f in failed:
            sn = f.get("serial") if isinstance(f, dict) else f
            if str(sn) == device.serial or device.serial not in succeeded:
                raise ControllerError(
                    f"{self.name}: upgrade of {device.serial} rejected: {r.get('message') or f}"
                )
        return ControllerJob(
            id=f"{device.serial}|{version}",
            status=JOB_RUNNING,
            message=str(r.get("message", "")) if isinstance(r, dict) else "",
        )

    async def distribute(self, device: ControllerDevice, version: str) -> ControllerJob:
        return await self._upgrade(device, version, reboot=False)

    async def activate(self, device: ControllerDevice, version: str) -> ControllerJob:
        return await self._upgrade(device, version, reboot=True)

    upgrade = activate

    async def job(self, job_id: str) -> ControllerJob:
        serial, _, version = job_id.partition("|")
        r = await self.request("GET", "/firmware/v1/status", query={"serial": serial})
        state = str(r.get("state") or r.get("status") or "")
        st = state.lower()
        reason = str(r.get("reason", ""))
        if "fail" in st:
            return ControllerJob(job_id, JOB_FAILED, state, reason or state)
        if any(w in st for w in ("success", "complete", "upgraded")):
            return ControllerJob(job_id, JOB_SUCCEEDED, state, reason)
        if version:
            try:
                dev = await self.get_device(serial)
            except ControllerError:
                dev = None
            if dev is not None and dev.version == version:
                return ControllerJob(job_id, JOB_SUCCEEDED, state, f"running {version}")
        return ControllerJob(job_id, JOB_RUNNING, state, reason)


VENDORS: dict[str, type[HttpController]] = {
    "catalyst_center": CatalystCenter,
    "cloudvision": CloudVision,
    "mist": Mist,
    "apstra": Apstra,
    "fortimanager": FortiManager,
    "panorama": Panorama,
    "nsp": NokiaNsp,
    "xiq": ExtremeCloudIq,
    "meraki": Meraki,
    "aruba_central": ArubaCentral,
}
