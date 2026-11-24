"""Fake controllers for fake mode and tests: every vendor, backed by the
simulated network, so an upgrade really changes `show version`."""

from __future__ import annotations

import itertools
from typing import Any

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
from vireonet.execution.fake_network import AVAILABLE_VERSIONS, FakeNetwork

# Which controller manages which demo platforms in fake mode.
FAKE_PLATFORMS = {
    "catalyst_center": ("ios", "ios-xe", "nxos"),
    "cloudvision": ("eos",),
    "mist": ("junos",),
    "apstra": ("junos", "eos", "nxos"),
    "fortimanager": ("fortios",),
    "panorama": ("panos",),
    "nsp": ("sros", "srlinux"),
    "xiq": ("exos",),
    "meraki": ("meraki",),
    "aruba_central": ("aos-cx",),
}
VENDOR_NAMES = {
    "catalyst_center": "Cisco Catalyst Center",
    "cloudvision": "Arista CloudVision",
    "mist": "Juniper Mist",
    "apstra": "Juniper Apstra",
    "fortimanager": "Fortinet FortiManager",
    "panorama": "Palo Alto Panorama",
    "nsp": "Nokia NSP",
    "xiq": "ExtremeCloud IQ",
    "meraki": "Cisco Meraki",
    "aruba_central": "HPE Aruba Central",
}
# Controllers whose distribute is a separate step in the real product.
SEPARATE_DISTRIBUTE = {"catalyst_center", "panorama", "mist", "nsp", "xiq", "aruba_central"}


class FakeController:
    def __init__(
        self,
        kind: str,
        network: FakeNetwork,
        inventory: list[dict[str, Any]],
        *,
        polls_to_finish: int = 2,
        sessions: Any = None,
    ) -> None:
        # Shares the simulated software state (version, boot image, images on
        # flash) between the API and the worker through demo_state.
        self._sessions = sessions
        self.name = kind
        self.kind = kind
        self.vendor = VENDOR_NAMES.get(kind, kind)
        self.platforms = FAKE_PLATFORMS.get(kind, ())
        self.native_upgrade = kind != "panorama"
        self._net = network
        self._inventory = [n for n in inventory if n.get("platform") in self.platforms]
        self._polls = polls_to_finish
        self._jobs: dict[str, dict[str, Any]] = {}
        self._ids = itertools.count(1)
        self.calls: list[tuple[str, str, str]] = []

    async def health(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "vendor": self.vendor,
            "status": "ok",
            "detail": f"fake: {len(self._inventory)} device{'' if len(self._inventory) == 1 else 's'} (demo network)",
        }

    def _dev(self, node: dict[str, Any]) -> ControllerDevice:
        st = self._net.device(node["name"], node["platform"])
        return ControllerDevice(
            id=f"{self.kind}-{node['name']}",
            name=node["name"],
            platform=node["platform"],
            version=st.version,
            serial=f"FAKE{abs(hash(node['name'])) % 10**8:08d}",
            model=f"{node['platform']}-{node.get('role', 'device')}",
            extra={"images_on_flash": list(st.images), "boot": st.boot_image},
        )

    async def _load(self, names: list[str]) -> None:
        if self._sessions is not None and names:
            from vireonet.execution.fake_network import NetworkStateStore

            await NetworkStateStore(self._sessions).load_into(self._net, names)
            for n in self._inventory:  # keep platforms known for image names
                if n["name"] in names:
                    self._net.platforms.setdefault(n["name"], n["platform"])

    async def _save(self, name: str) -> None:
        if self._sessions is not None:
            from vireonet.execution.fake_network import NetworkStateStore

            await NetworkStateStore(self._sessions).save_from(self._net, [name])

    async def list_devices(self) -> list[ControllerDevice]:
        await self._load([n["name"] for n in self._inventory])
        return [self._dev(n) for n in self._inventory]

    async def get_device(self, name: str) -> ControllerDevice:
        for n in self._inventory:
            if n["name"] == name:
                await self._load([name])
                return self._dev(n)
        raise ControllerError(
            f"{self.name}: no device {name!r} (manages {', '.join(self.platforms) or 'nothing'} in fake mode)"
        )

    async def list_images(self, platform: str = "") -> list[ControllerImage]:
        out = []
        for p in self.platforms if not platform else [platform]:
            for v in AVAILABLE_VERSIONS.get(p, []):
                out.append(
                    ControllerImage(
                        id=f"img-{p}-{v}", name=FakeNetwork.image_name(v, p), version=v, platform=p
                    )
                )
        return out

    def _new_job(self, kind: str, device: ControllerDevice, version: str) -> ControllerJob:
        find_image([i for i in self._images_sync(device.platform)], version, device.platform)
        jid = f"{self.kind}-job-{next(self._ids)}"
        self._jobs[jid] = {"kind": kind, "device": device.name, "version": version, "polls": 0}
        self.calls.append((kind, device.name, version))
        return ControllerJob(jid, JOB_RUNNING, "0%", f"{kind} {version} on {device.name}")

    def _images_sync(self, platform: str) -> list[ControllerImage]:
        return [
            ControllerImage(id=f"img-{platform}-{v}", name=v, version=v, platform=platform)
            for v in AVAILABLE_VERSIONS.get(platform, [])
        ]

    async def distribute(self, device: ControllerDevice, version: str) -> ControllerJob:
        return self._new_job("distribute", device, version)

    async def activate(self, device: ControllerDevice, version: str) -> ControllerJob:
        return self._new_job("activate", device, version)

    async def upgrade(self, device: ControllerDevice, version: str) -> ControllerJob:
        if not self.native_upgrade:
            raise ControllerError(
                f"{self.name}: use distribute then activate (no one-step upgrade)"
            )
        return self._new_job("upgrade", device, version)

    async def job(self, job_id: str) -> ControllerJob:
        j = self._jobs.get(job_id)
        if j is None:
            raise ControllerError(f"{self.name}: no job {job_id}")
        j["polls"] += 1
        if j["polls"] < self._polls:
            return ControllerJob(job_id, JOB_RUNNING, f"{int(100 * j['polls'] / self._polls)}%")
        if "result" not in j:
            await self._load([j["device"]])
            try:
                if j["kind"] in ("distribute", "upgrade"):
                    self._net.stage_image(j["device"], j["version"])
                if j["kind"] in ("activate", "upgrade"):
                    self._net.activate_image(j["device"], j["version"])
                j["result"] = (JOB_SUCCEEDED, "")
                await self._save(j["device"])
            except ValueError as exc:
                j["result"] = (JOB_FAILED, str(exc))
        status, msg = j["result"]
        return ControllerJob(job_id, status, "100%", msg)

    async def api(
        self, method: str, path: str, *, query: dict[str, Any] | None = None, body: Any = None
    ) -> Any:
        return {
            "fake": True,
            "controller": self.name,
            "method": method.upper(),
            "path": path,
            "query": query or {},
            "body": body,
        }
