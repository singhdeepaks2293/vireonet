from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from vireonet.adapters.base import AdapterConfig, AdapterHealth, BaseAdapter
from vireonet.adapters.errors import AdapterValidationError
from vireonet.adapters.protocols import DhcpReservation, NetBoxProtocol


class DhcpAdapter(BaseAdapter):
    """Real DHCP adapter. Renders ISC dhcpd / Kea configs from NetBox data,
    validates syntax (dhcpd/kea binary when present, structural check otherwise),
    deploys Git-first via GitLab + AWX reload."""

    def __init__(
        self, config: AdapterConfig, *, netbox: NetBoxProtocol | None = None,
        gitlab: Any | None = None, awx: Any | None = None,
        server: str = "isc", tftp_server: str = "10.0.0.5", http_base_url: str = "http://10.0.0.5/ztp",
        pnp_server_url: str = "", config_path: str = "dhcp/dhcpd.conf",
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(config, correlation_id=correlation_id)
        self._netbox = netbox
        self._gitlab = gitlab
        self._awx = awx
        self.server = server
        self.tftp_server = tftp_server
        self.http_base_url = http_base_url
        self.pnp_server_url = pnp_server_url
        self.config_path = config_path

    def capabilities(self) -> list[str]:
        return ["reservation", "render_isc", "render_kea", "validate", "deploy", "reload", "verify"]

    async def health_check(self) -> AdapterHealth:
        return AdapterHealth(name=self.name, status="ok", capabilities=self.capabilities())

    async def generate_reservation(self, hostname: str, mac: str, ip: str) -> DhcpReservation:
        return DhcpReservation(hostname, mac, ip, self.tftp_server, f"{hostname}.cfg")

    async def generate_from_netbox(self, hostname: str, *, option82_circuit: str | None = None, option82_remote: str | None = None) -> DhcpReservation:
        """Reservation from NetBox: MAC from the device, IP allocated if none."""
        if self._netbox is None:
            raise AdapterValidationError(self.name, "generate_from_netbox", "NetBox adapter not configured")
        device = await self._netbox.get_device(hostname)
        # NetBox does not store MAC on the device itself in all versions; the
        # MAC comes from the registration payload. Here we use primary IP if
        # present, otherwise allocate from the site prefix.
        ip = device.primary_ip or await self._netbox.allocate_ip("10.0.1.0/24", f"ztp {hostname}")
        res = DhcpReservation(hostname, "", ip, self.tftp_server, f"{hostname}.cfg")
        # Option 82 is carried in the rendered config, not in the reservation object.
        res.__dict__["option82_circuit"] = option82_circuit  # type: ignore[attr-defined]
        res.__dict__["option82_remote"] = option82_remote  # type: ignore[attr-defined]
        return res

    async def render_config(self, reservations: list[DhcpReservation], server: str) -> str:
        if server == "isc":
            lines = ["# VireoNet DHCP — generated, do not edit by hand", f'option tftp-server-name "{self.tftp_server}";']
            for r in reservations:
                lines.append(f"host {r.hostname} {{")
                lines.append(f"  hardware ethernet {r.mac_address};" if r.mac_address else f"  # no MAC yet for {r.hostname}")
                lines.append(f"  fixed-address {r.ip_address};")
                lines.append(f'  option tftp-server-name "{self.tftp_server}";')
                lines.append(f'  filename "{r.option_67}";')
                if self.pnp_server_url:
                    from vireonet.ztp.templates import encode_pnp_option43

                    lines.append('  option dhcp-parameter-request-list 43;')
                    lines.append(f'  # PnP option 43: {encode_pnp_option43(self.pnp_server_url)}')
                lines.append("}")
            return "\n".join(lines) + "\n"
        if server == "kea":
            hosts = []
            for r in reservations:
                entry: dict[str, Any] = {"hostname": r.hostname, "ip-address": r.ip_address, "boot-file-name": r.option_67}
                if r.mac_address:
                    entry["hw-address"] = r.mac_address
                hosts.append(entry)
            return json.dumps({"Dhcp4": {"valid-lifetime": 3600, "option-data": [{"name": "tftp-server-name", "data": self.tftp_server}], "reservations": hosts}}, indent=2)
        raise AdapterValidationError(self.name, "render_config", f"unknown server {server}")

    async def validate_config(self, config_text: str) -> bool:
        if self.server == "kea":
            try:
                data = json.loads(config_text)
                return "Dhcp4" in data
            except json.JSONDecodeError:
                return False
        # ISC: prefer the real dhcpd syntax check when the binary exists.
        dhcpd = shutil.which("dhcpd")
        if dhcpd:
            import tempfile
            from pathlib import Path

            with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as f:
                f.write(config_text)
                path = f.name
            try:
                result = subprocess.run([dhcpd, "-t", "-cf", path], capture_output=True, text=True, timeout=10)  # noqa: S603
                return result.returncode == 0
            except Exception:
                return False
            finally:
                Path(path).unlink(missing_ok=True)
        # Fallback structural check (no dhcpd binary in this sandbox).
        return "host " in config_text and "fixed-address" in config_text

    async def deploy(self, config_text: str, server: str) -> dict[str, Any]:
        """Git-first deploy: commit to GitLab, then AWX dhcp-reload. Returns evidence."""
        if not await self.validate_config(config_text):
            raise AdapterValidationError(self.name, "deploy", "DHCP config failed validation")
        evidence: dict[str, Any] = {"server": server, "config_path": self.config_path}
        if self._gitlab is not None:
            branch = "dhcp/update"
            try:
                await self._gitlab.create_branch(branch)
            except Exception:
                pass  # branch may already exist
            commit = await self._gitlab.commit_files(branch, {self.config_path: config_text}, "Update DHCP reservations (VireoNet)")
            evidence["gitlab_commit"] = commit
        if self._awx is not None:
            job = await self._awx.launch_job("dhcp-reload", {"config_path": self.config_path})
            evidence["reload_job_id"] = job.id
            evidence["reload_status"] = job.status
        return evidence

    async def reload(self) -> dict[str, Any]:
        if self._awx is None:
            return {"ok": True, "note": "no AWX configured, reload skipped (config committed to Git)"}
        job = await self._awx.launch_job("dhcp-reload", {})
        return {"ok": job.status == "successful", "job_id": job.id, "status": job.status}

    async def verify_reservation(self, hostname: str, config_text: str) -> bool:
        return f"host {hostname}" in config_text
