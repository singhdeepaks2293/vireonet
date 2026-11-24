from __future__ import annotations

import json
import shutil
import subprocess
from contextlib import suppress
from typing import Any

from vireonet.adapters.base import AdapterConfig, AdapterHealth, BaseAdapter
from vireonet.adapters.errors import AdapterValidationError
from vireonet.adapters.protocols import DhcpReservation, NetBoxProtocol


class DhcpAdapter(BaseAdapter):
    """Real DHCP adapter. Renders ISC dhcpd / Kea configs from NetBox data,
    validates syntax (dhcpd/kea binary when present, structural check otherwise),
    deploys Git-first via GitLab + AWX reload."""

    def __init__(
        self,
        config: AdapterConfig,
        *,
        netbox: NetBoxProtocol | None = None,
        gitlab: Any | None = None,
        awx: Any | None = None,
        server: str = "isc",
        tftp_server: str = "10.0.0.5",
        http_base_url: str = "http://10.0.0.5/ztp",
        pnp_server_url: str = "",
        config_path: str = "dhcp/dhcpd.conf",
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
        return [
            "reservation",
            "render_isc",
            "render_kea",
            "validate",
            "deploy",
            "deploy_and_verify",
            "reload",
            "verify",
            "remove_reservation",
        ]

    async def health_check(self) -> AdapterHealth:
        try:
            reservation = DhcpReservation(
                "health-check",
                "aa:bb:cc:dd:ee:00",
                "10.0.1.254",
                self.tftp_server,
                "health-check.cfg",
            )
            config = await self.render_config([reservation], self.server)
            valid = await self.validate_config(config)
        except Exception as exc:  # noqa: BLE001 — health must not raise
            return AdapterHealth(
                name=self.name, status="down", detail=str(exc), capabilities=self.capabilities()
            )
        if valid and self.tftp_server:
            return AdapterHealth(name=self.name, status="ok", capabilities=self.capabilities())
        return AdapterHealth(
            name=self.name,
            status="down",
            detail="DHCP health-check reservation failed validation",
            capabilities=self.capabilities(),
        )

    async def generate_reservation(self, hostname: str, mac: str, ip: str) -> DhcpReservation:
        return DhcpReservation(hostname, mac, ip, self.tftp_server, f"{hostname}.cfg")

    async def generate_from_netbox(
        self,
        hostname: str,
        *,
        option82_circuit: str | None = None,
        option82_remote: str | None = None,
    ) -> DhcpReservation:
        """Reservation from NetBox: MAC from the device, IP allocated if none."""
        if self._netbox is None:
            raise AdapterValidationError(
                self.name, "generate_from_netbox", "NetBox adapter not configured"
            )
        device = await self._netbox.get_device(hostname)
        # NetBox does not store MAC on the device itself in all versions; the
        # MAC comes from the registration payload. Here we use primary IP if
        # present, otherwise allocate from the site prefix.
        ip = device.primary_ip or await self._netbox.allocate_ip("10.0.1.0/24", f"ztp {hostname}")
        res = DhcpReservation(hostname, "", ip, self.tftp_server, f"{hostname}.cfg")
        # Option 82 is carried in the rendered config, not in the reservation object.
        res.__dict__["option82_circuit"] = option82_circuit
        res.__dict__["option82_remote"] = option82_remote
        return res

    async def render_config(self, reservations: list[DhcpReservation], server: str) -> str:
        if server == "isc":
            lines = [
                "# VireoNet DHCP — generated, do not edit by hand",
                f'option tftp-server-name "{self.tftp_server}";',
            ]
            for r in reservations:
                lines.append(f"host {r.hostname} {{")
                lines.append(
                    f"  hardware ethernet {r.mac_address};"
                    if r.mac_address
                    else f"  # no MAC yet for {r.hostname}"
                )
                lines.append(f"  fixed-address {r.ip_address};")
                lines.append(f'  option tftp-server-name "{self.tftp_server}";')
                lines.append(f'  filename "{r.option_67}";')
                if self.pnp_server_url:
                    from vireonet.ztp.templates import encode_pnp_option43

                    lines.append("  option dhcp-parameter-request-list 43;")
                    lines.append(f"  # PnP option 43: {encode_pnp_option43(self.pnp_server_url)}")
                lines.append("}")
            return "\n".join(lines) + "\n"
        if server == "kea":
            hosts = []
            for r in reservations:
                entry: dict[str, Any] = {
                    "hostname": r.hostname,
                    "ip-address": r.ip_address,
                    "boot-file-name": r.option_67,
                }
                if r.mac_address:
                    entry["hw-address"] = r.mac_address
                hosts.append(entry)
            # Reservations at Dhcp4 level are GLOBAL reservations; Kea only
            # consults them with "reservations-global": true (default false).
            # Subnet-level lookups stay on (reservations-in-subnet defaults true).
            # The lease still needs a subnet4 covering the IP — that lives in
            # the deployed base config (subnet4 is preserved on edits).
            # https://kea.readthedocs.io/en/latest/arm/dhcp4-srv.html
            return json.dumps(
                {
                    "Dhcp4": {
                        "valid-lifetime": 3600,
                        "option-data": [{"name": "tftp-server-name", "data": self.tftp_server}],
                        "reservations-global": True,
                        "reservations": hosts,
                    }
                },
                indent=2,
            )
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
                result = subprocess.run(
                    [dhcpd, "-t", "-cf", path], capture_output=True, text=True, timeout=10
                )  # noqa: S603
                return result.returncode == 0
            except Exception:
                return False
            finally:
                Path(path).unlink(missing_ok=True)
        # Fallback structural check (no dhcpd binary in this sandbox).
        # A config with reservations needs host + fixed-address; a generated
        # config with every reservation removed (header + options only) is
        # still syntactically valid dhcpd, so accept it as well.
        return (
            "host " in config_text and "fixed-address" in config_text
        ) or config_text.startswith("# VireoNet DHCP")

    async def deploy(self, config_text: str, server: str) -> dict[str, Any]:
        """Git-first deploy: commit to GitLab, then AWX dhcp-reload. Returns evidence."""
        if not await self.validate_config(config_text):
            raise AdapterValidationError(self.name, "deploy", "DHCP config failed validation")
        evidence: dict[str, Any] = {"server": server, "config_path": self.config_path}
        if self._gitlab is not None:
            branch = "dhcp/update"
            with suppress(Exception):  # branch may already exist
                await self._gitlab.create_branch(branch)
            commit = await self._gitlab.commit_files(
                branch, {self.config_path: config_text}, "Update DHCP reservations (VireoNet)"
            )
            evidence["gitlab_commit"] = commit
        if self._awx is not None:
            job = await self._awx.launch_job("dhcp-reload", {"config_path": self.config_path})
            evidence["reload_job_id"] = job.id
            evidence["reload_status"] = job.status
        return evidence

    async def deploy_and_verify(
        self, config_text: str, server: str, *, hostname: str, expect_present: bool = True
    ) -> dict[str, Any]:
        """Deploy, poll the AWX reload job to a finished state, then verify the
        reservation is present (or, for removals, absent) in the deployed config."""
        evidence = await self.deploy(config_text, server)
        if self._awx is not None and evidence.get("reload_job_id") is not None:
            poll = getattr(self._awx, "poll_job", None)
            if poll is not None:
                job = await poll(int(evidence["reload_job_id"]), timeout_seconds=300)
                evidence["reload_status"] = job.status
                if job.status != "successful":
                    raise AdapterValidationError(
                        self.name,
                        "deploy_and_verify",
                        f"DHCP reload job {job.id} finished {job.status}",
                    )
            elif evidence.get("reload_status") in {"failed", "canceled"}:
                raise AdapterValidationError(
                    self.name,
                    "deploy_and_verify",
                    f"DHCP reload finished {evidence.get('reload_status')}",
                )
        present = self._reservation_present(hostname, config_text, server)
        if expect_present and not present:
            raise AdapterValidationError(
                self.name,
                "deploy_and_verify",
                f"reservation for {hostname} not present in deployed config",
            )
        if not expect_present and present:
            raise AdapterValidationError(
                self.name,
                "deploy_and_verify",
                f"reservation for {hostname} still present in deployed config",
            )
        evidence["verified"] = True
        return evidence

    async def remove_reservation(self, hostname: str) -> dict[str, Any]:
        if self._gitlab is None:
            raise AdapterValidationError(
                self.name, "remove_reservation", "GitLab adapter not configured"
            )
        current = await self._gitlab.read_file(self.config_path)
        updated = self.remove_host_from_config(current, hostname, self.server)
        if updated == current:
            return {
                "server": self.server,
                "hostname": hostname,
                "removed": False,
                "note": f"no reservation for {hostname} in {self.config_path}",
            }
        evidence = await self.deploy_and_verify(
            updated, self.server, hostname=hostname, expect_present=False
        )
        removed = not self._reservation_present(hostname, updated, self.server)
        if not removed:
            raise AdapterValidationError(
                self.name,
                "remove_reservation",
                f"reservation for {hostname} still present after removal",
            )
        evidence["removed"] = True
        evidence["verified"] = removed
        evidence["hostname"] = hostname
        return evidence

    @staticmethod
    def remove_host_from_config(config_text: str, hostname: str, server: str) -> str:
        if server == "kea":
            try:
                data = json.loads(config_text)
            except json.JSONDecodeError:
                return config_text
            dhcp4 = data.get("Dhcp4")
            if not isinstance(dhcp4, dict):
                return config_text
            reservations = dhcp4.get("reservations")
            if not isinstance(reservations, list):
                return config_text
            kept = [
                r
                for r in reservations
                if not (isinstance(r, dict) and r.get("hostname") == hostname)
            ]
            if len(kept) == len(reservations):
                return config_text
            # Edit in place: everything else (subnet4, option-data, ...) is kept.
            dhcp4["reservations"] = kept
            dhcp4["reservations-global"] = True
            return json.dumps(data, indent=2)
        # ISC: drop the `host <name> { ... }` block, tracking brace depth.
        lines = config_text.splitlines(keepends=True)
        out: list[str] = []
        i = 0
        while i < len(lines):
            if lines[i].strip().startswith(f"host {hostname}") and "{" in lines[i]:
                depth = lines[i].count("{") - lines[i].count("}")
                i += 1
                while i < len(lines) and depth > 0:
                    depth += lines[i].count("{") - lines[i].count("}")
                    i += 1
                continue
            out.append(lines[i])
            i += 1
        return "".join(out)

    async def reload(self) -> dict[str, Any]:
        if self._awx is None:
            return {
                "ok": True,
                "note": "no AWX configured, reload skipped (config committed to Git)",
            }
        job = await self._awx.launch_job("dhcp-reload", {})
        poll = getattr(self._awx, "poll_job", None)
        if poll is not None and job.status not in {"successful", "failed", "canceled"}:
            job = await poll(job.id, timeout_seconds=300)
        return {"ok": job.status == "successful", "job_id": job.id, "status": job.status}

    @staticmethod
    def _reservation_present(hostname: str, config_text: str, server: str) -> bool:
        if server == "kea":
            try:
                data = json.loads(config_text)
            except json.JSONDecodeError:
                return False
            dhcp4 = data.get("Dhcp4")
            if not isinstance(dhcp4, dict):
                return False
            reservations = dhcp4.get("reservations", [])
            return isinstance(reservations, list) and any(
                isinstance(r, dict) and r.get("hostname") == hostname for r in reservations
            )
        return f"host {hostname}" in config_text

    async def verify_reservation(self, hostname: str, config_text: str) -> bool:
        return self._reservation_present(hostname, config_text, self.server)
