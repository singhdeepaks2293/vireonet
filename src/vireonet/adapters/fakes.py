from __future__ import annotations

from vireonet.adapters.base import AdapterConfig, AdapterHealth, BaseAdapter
from vireonet.adapters.errors import AdapterNotFound, AdapterValidationError
from vireonet.adapters.protocols import (
    AwxJob,
    BatfishResult,
    DhcpReservation,
    EveLab,
    GitLabMR,
    NetBoxDevice,
    NetBoxVlan,
    SNRequest,
)


class _FakeBase(BaseAdapter):
    """Deterministic in-memory fake. Records calls; `fail_next` injects one error."""

    def __init__(self, name: str, caps: list[str]) -> None:
        super().__init__(AdapterConfig(name=name))
        self._caps = caps
        self.calls: list[dict[str, object]] = []
        self._fail_next: Exception | None = None

    def capabilities(self) -> list[str]:
        return list(self._caps)

    async def health_check(self) -> AdapterHealth:
        return AdapterHealth(name=self.name, status="ok", capabilities=self.capabilities())

    def fail_next(self, exc: Exception) -> None:
        self._fail_next = exc

    def _record(self, operation: str, **kw: object) -> None:
        self.calls.append({"operation": operation, **kw})
        if self._fail_next is not None:
            exc, self._fail_next = self._fail_next, None
            raise exc


class FakeServiceNowAdapter(_FakeBase):
    def __init__(self) -> None:
        super().__init__("servicenow", ["read_request", "update_state", "work_notes", "close"])
        self.requests: dict[str, SNRequest] = {
            "RITM0001234": SNRequest("RITM0001234", "Add VLAN 100", "open", "alice", {"vlan_id": "100"})
        }
        self._notes_seen: set[str] = set()

    async def get_request(self, ticket: str) -> SNRequest:
        self._record("get_request", ticket=ticket)
        try:
            return self.requests[ticket]
        except KeyError:
            raise AdapterNotFound("servicenow", "get_request", ticket) from None

    async def update_state(self, ticket: str, state: str, work_notes: str | None = None) -> SNRequest:
        self._record("update_state", ticket=ticket, state=state)
        req = await self.get_request(ticket)
        req.state = state
        return req

    async def add_work_note(self, ticket: str, note: str, idempotency_key: str) -> bool:
        self._record("add_work_note", ticket=ticket, key=idempotency_key)
        await self.get_request(ticket)
        if idempotency_key in self._notes_seen:
            return False  # duplicate on retry: not written twice
        self._notes_seen.add(idempotency_key)
        return True

    async def close(self, ticket: str, close_notes: str) -> SNRequest:
        self._record("close", ticket=ticket)
        return await self.update_state(ticket, "closed", close_notes)

    async def get_request_by_sys_id(self, sys_id: str) -> SNRequest:
        self._record("get_request_by_sys_id", sys_id=sys_id)
        # Fake has one seeded request; any sys_id maps to it (deterministic).
        return self.requests["RITM0001234"]

    async def attach_file(self, ticket: str, filename: str, content: bytes) -> str:
        self._record("attach_file", ticket=ticket, filename=filename)
        await self.get_request(ticket)
        return f"attach-{ticket}-{filename}"

    async def get_approval_state(self, ticket: str) -> str:
        self._record("get_approval_state", ticket=ticket)
        await self.get_request(ticket)
        return "approved"


class FakeNetBoxAdapter(_FakeBase):
    def __init__(self) -> None:
        super().__init__("netbox", ["devices", "vlans", "ips", "journal"])
        self.devices: dict[str, NetBoxDevice] = {
            "leaf-01": NetBoxDevice("leaf-01", "FOC1234A", "C9300-48P", "dc1", "active", "ios-xe", "10.0.0.11"),
            "leaf-02": NetBoxDevice("leaf-02", "FOC1234B", "C9300-48P", "dc1", "staged", "ios-xe"),
        }
        self.vlans: dict[int, NetBoxVlan] = {100: NetBoxVlan(100, "web", "dc1")}
        self._next_ip = 100

    async def get_device(self, name: str) -> NetBoxDevice:
        self._record("get_device", name=name)
        try:
            return self.devices[name]
        except KeyError:
            raise AdapterNotFound("netbox", "get_device", name) from None

    async def list_devices(self, site: str | None = None) -> list[NetBoxDevice]:
        self._record("list_devices", site=site or "")
        return [d for d in self.devices.values() if site is None or d.site == site]

    async def update_device_status(self, name: str, status: str) -> NetBoxDevice:
        self._record("update_device_status", name=name, status=status)
        dev = await self.get_device(name)
        dev.status = status
        return dev

    async def create_device(self, name: str, serial: str, site: str, status: str = "staged", device_type_id: int | None = None, role_id: int | None = None) -> NetBoxDevice:
        self._record("create_device", name=name)
        dev = NetBoxDevice(name, serial, "C9300-48P", site, status, "ios-xe")
        self.devices[name] = dev
        return dev

    async def list_interfaces(self, device: str) -> list[str]:
        self._record("list_interfaces", device=device)
        await self.get_device(device)
        return ["GigabitEthernet0/0", "GigabitEthernet0/1", "Ethernet1/1", "mgmt0"]

    async def list_vlans(self, site: str | None = None) -> list[NetBoxVlan]:
        self._record("list_vlans", site=site or "")
        return [v for v in self.vlans.values() if site is None or v.site == site]

    async def check_ip_free(self, ip: str) -> bool:
        self._record("check_ip_free", ip=ip)
        return ip not in {d.primary_ip for d in self.devices.values() if d.primary_ip}

    async def export_inventory_for_awx(self, site: str | None = None) -> dict[str, object]:
        self._record("export_inventory_for_awx", site=site or "")
        hosts = {d.name: {"ansible_host": d.primary_ip} for d in await self.list_devices(site)}
        return {"all": {"hosts": hosts}}

    async def get_vlan(self, vlan_id: int, site: str | None = None) -> NetBoxVlan | None:
        self._record("get_vlan", vlan_id=vlan_id)
        v = self.vlans.get(vlan_id)
        return v if v is None or site is None or v.site == site else None

    async def allocate_ip(self, prefix: str, description: str) -> str:
        self._record("allocate_ip", prefix=prefix)
        self._next_ip += 1
        return f"10.0.1.{self._next_ip}"

    async def add_journal_entry(self, device: str, kind: str, comments: str) -> str:
        self._record("add_journal_entry", device=device, kind=kind)
        await self.get_device(device)
        return f"journal-{device}-{len(self.calls)}"


class FakeAwxAdapter(_FakeBase):
    def __init__(self) -> None:
        super().__init__("awx", ["launch_job", "poll_job", "cancel", "templates"])
        self.jobs: dict[int, AwxJob] = {}
        self._next_id = 1
        self.templates = ["backup", "pre-check", "dry-run", "apply", "post-check", "rollback"]

    async def launch_job(self, template: str, extra_vars: dict[str, str], limit: str | None = None) -> AwxJob:
        self._record("launch_job", template=template)
        if template not in self.templates:
            raise AdapterNotFound("awx", "launch_job", template)
        job = AwxJob(self._next_id, template, "successful", dict(extra_vars))
        self.jobs[job.id] = job
        self._next_id += 1
        return job

    async def get_job(self, job_id: int) -> AwxJob:
        self._record("get_job", job_id=job_id)
        try:
            return self.jobs[job_id]
        except KeyError:
            raise AdapterNotFound("awx", "get_job", str(job_id)) from None

    async def cancel_job(self, job_id: int) -> AwxJob:
        job = await self.get_job(job_id)
        job.status = "canceled"
        return job

    async def list_templates(self) -> list[str]:
        self._record("list_templates")
        return list(self.templates)


class FakeGitLabAdapter(_FakeBase):
    def __init__(self) -> None:
        super().__init__("gitlab", ["mr", "pipeline", "merge"])
        self.mrs: dict[int, GitLabMR] = {}
        self._next_iid = 1

    async def create_mr(self, branch: str, title: str, files: dict[str, str]) -> GitLabMR:
        self._record("create_mr", branch=branch)
        mr = GitLabMR(self._next_iid, title, "opened", "success", f"https://gitlab.example/mr/{self._next_iid}")
        self.mrs[mr.iid] = mr
        self._next_iid += 1
        return mr

    async def get_mr(self, iid: int) -> GitLabMR:
        self._record("get_mr", iid=iid)
        try:
            return self.mrs[iid]
        except KeyError:
            raise AdapterNotFound("gitlab", "get_mr", str(iid)) from None

    async def get_pipeline_status(self, iid: int) -> str:
        return (await self.get_mr(iid)).pipeline_status

    async def merge_mr(self, iid: int) -> GitLabMR:
        mr = await self.get_mr(iid)
        mr.state = "merged"
        return mr


class FakeBatfishAdapter(_FakeBase):
    def __init__(self) -> None:
        super().__init__("batfish", ["reachability", "acl", "routing", "undefined_refs"])

    async def analyse(self, files: dict[str, str]) -> BatfishResult:
        self._record("analyse", files=list(files))
        failures = [f"undefined reference in {k}" for k, v in files.items() if "UNDEFINED" in v]
        return BatfishResult(passed=not failures, failures=failures, evidence={"files": len(files)})


class FakeVaultAdapter(_FakeBase):
    def __init__(self) -> None:
        super().__init__("vault", ["kv_read", "bootstrap_token"])
        self.secrets = {"secret/vireonet/awx": {"token": "fake-awx-token"}}

    async def read_secret(self, path: str) -> dict[str, str]:
        self._record("read_secret", path=path)
        try:
            return dict(self.secrets[path])
        except KeyError:
            raise AdapterNotFound("vault", "read_secret", path) from None

    async def issue_bootstrap_token(self, device: str) -> str:
        self._record("issue_bootstrap_token", device=device)
        return f"bootstrap-{device}-one-time"


class FakeEveNgAdapter(_FakeBase):
    def __init__(self) -> None:
        super().__init__("eveng", ["start_lab", "stop_lab"])
        self.labs: dict[str, EveLab] = {}

    async def start_lab(self, name: str, topology: str) -> EveLab:
        self._record("start_lab", name=name)
        lab = EveLab(f"lab-{name}", name, "running", ["leaf-01", "leaf-02"])
        self.labs[lab.id] = lab
        return lab

    async def get_lab(self, lab_id: str) -> EveLab:
        self._record("get_lab", lab_id=lab_id)
        try:
            return self.labs[lab_id]
        except KeyError:
            raise AdapterNotFound("eveng", "get_lab", lab_id) from None

    async def stop_lab(self, lab_id: str) -> None:
        self._record("stop_lab", lab_id=lab_id)
        self.labs.pop(lab_id, None)


class FakeDhcpAdapter(_FakeBase):
    def __init__(self) -> None:
        super().__init__("dhcp", ["reservation", "render_isc", "render_kea", "validate", "deploy", "reload", "verify"])

    async def generate_reservation(self, hostname: str, mac: str, ip: str) -> DhcpReservation:
        self._record("generate_reservation", hostname=hostname)
        return DhcpReservation(hostname, mac, ip, "10.0.0.5", "bootstrap.cfg")

    async def generate_from_netbox(self, hostname: str, *, option82_circuit: str | None = None, option82_remote: str | None = None) -> DhcpReservation:
        self._record("generate_from_netbox", hostname=hostname)
        return DhcpReservation(hostname, "aa:bb:cc:dd:ee:ff", "10.0.1.101", "10.0.0.5", "bootstrap.cfg")

    async def render_config(self, reservations: list[DhcpReservation], server: str) -> str:
        self._record("render_config", server=server)
        if server not in {"isc", "kea"}:
            raise AdapterValidationError("dhcp", "render_config", f"unknown server {server}")
        lines = [f"# {server}"]
        for r in reservations:
            lines.append(f"host {r.hostname} {{ hardware ethernet {r.mac_address}; fixed-address {r.ip_address}; }}")
        return "\n".join(lines)

    async def validate_config(self, config_text: str) -> bool:
        self._record("validate_config")
        return "host " in config_text

    async def deploy(self, config_text: str, server: str) -> dict[str, object]:
        self._record("deploy", server=server)
        if not await self.validate_config(config_text):
            raise AdapterValidationError("dhcp", "deploy", "config failed validation")
        return {"server": server, "gitlab_commit": "fake-commit", "reload_job_id": 1, "reload_status": "successful"}

    async def reload(self) -> dict[str, object]:
        self._record("reload")
        return {"ok": True, "job_id": 1, "status": "successful"}

    async def verify_reservation(self, hostname: str, config_text: str) -> bool:
        return f"host {hostname}" in config_text


class FakeNotificationAdapter(_FakeBase):
    def __init__(self) -> None:
        super().__init__("notification", ["email", "teams", "slack"])

    async def send(self, channel: str, title: str, body: str, ticket: str) -> bool:
        self._record("send", channel=channel, ticket=ticket)
        if channel not in {"email", "teams", "slack"}:
            raise AdapterValidationError("notification", "send", f"unknown channel {channel}")
        return True


class FakeMonitoringAdapter(_FakeBase):
    def __init__(self) -> None:
        super().__init__("monitoring", ["silence", "enable"])

    async def silence(self, device: str, duration_minutes: int) -> str:
        self._record("silence", device=device)
        return f"silence-{device}-{duration_minutes}m"

    async def enable_monitoring(self, device: str) -> bool:
        self._record("enable_monitoring", device=device)
        return True


ALL_FAKE_ADAPTERS: dict[str, type[_FakeBase]] = {
    "servicenow": FakeServiceNowAdapter,
    "netbox": FakeNetBoxAdapter,
    "awx": FakeAwxAdapter,
    "gitlab": FakeGitLabAdapter,
    "batfish": FakeBatfishAdapter,
    "vault": FakeVaultAdapter,
    "eveng": FakeEveNgAdapter,
    "dhcp": FakeDhcpAdapter,
    "notification": FakeNotificationAdapter,
    "monitoring": FakeMonitoringAdapter,
}
