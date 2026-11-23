from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio import activity

from vireonet.adapters.protocols import (
    AwxProtocol,
    BatfishProtocol,
    DhcpProtocol,
    NetBoxProtocol,
    ServiceNowProtocol,
)
from vireonet.batfish_gate import run_batfish_gate
from vireonet.intent import parse_intent
from vireonet.ztp.service import ZtpService
from vireonet.ztp.templates import render_bootstrap


@dataclass
class ReservationInput:
    serial: str
    hostname: str
    mac_address: str
    model: str
    platform: str
    site: str
    ticket_number: str


@dataclass
class IdentityInput:
    serial: str
    model: str
    platform: str
    version: str = ""
    ip_address: str = ""


@dataclass
class Day1Input:
    serial: str
    hostname: str
    model: str
    platform: str
    site: str
    ticket_number: str


@dataclass
class ActivateInput:
    serial: str
    hostname: str
    ticket_number: str


class ZtpActivities:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], *, netbox: NetBoxProtocol,
        dhcp: DhcpProtocol, awx: AwxProtocol, batfish: BatfishProtocol,
        servicenow: ServiceNowProtocol, ztp_service: ZtpService,
        tftp_server: str = "10.0.0.5", http_base_url: str = "http://10.0.0.5/ztp", pnp_server_url: str = "",
    ) -> None:
        self._sessions = sessions
        self._netbox = netbox
        self._dhcp = dhcp
        self._awx = awx
        self._batfish = batfish
        self._snow = servicenow
        self._ztp = ztp_service
        self._tftp = tftp_server
        self._http_base = http_base_url
        self._pnp_url = pnp_server_url

    @activity.defn(name="ztp_create_reservation")
    async def create_reservation(self, inp: ReservationInput) -> dict[str, Any]:
        ip = await self._netbox.allocate_ip("10.0.1.0/24", f"ztp {inp.hostname}")
        reservation = await self._dhcp.generate_reservation(inp.hostname, inp.mac_address, ip)
        config = await self._dhcp.render_config([reservation], "isc")
        if not await self._dhcp.validate_config(config):
            return {"ok": False, "error": "DHCP config failed validation"}
        deployed = await self._dhcp.deploy(config, "isc")
        bootstrap = render_bootstrap(inp.platform, {
            "hostname": inp.hostname, "serial": inp.serial, "tftp_server": self._tftp,
            "http_base_url": self._http_base, "pnp_server_url": self._pnp_url,
            "config_file": f"{inp.hostname}.cfg", "mgmt_interface": "GigabitEthernet0/0",
        })
        await self._ztp.set_stage(inp.serial, "reservation_created", ip_address=ip)
        return {"ok": True, "ip": ip, "bootstrap": bootstrap, "deployed": deployed}

    @activity.defn(name="ztp_verify_identity")
    async def verify_identity(self, inp: IdentityInput) -> dict[str, Any]:
        ok, errors = await self._ztp.verify_identity(serial=inp.serial, model=inp.model, platform=inp.platform, version=inp.version or None)
        if inp.ip_address:
            await self._ztp.set_stage(inp.serial, "identity_verified" if ok else "identity_failed", ip_address=inp.ip_address)
        return {"ok": ok, "errors": errors}

    @activity.defn(name="ztp_day1_apply")
    async def day1_apply(self, inp: Day1Input) -> dict[str, Any]:
        intent = parse_intent({
            "change_type": "ztp_device", "serial": inp.serial, "model": inp.model, "platform": inp.platform,
            "devices": [inp.hostname], "site": inp.site, "ticket_number": inp.ticket_number,
            "requested_by": "ztp", "mac_address": "aa:bb:cc:dd:ee:ff",
        })
        # Batfish gate on the day-1 candidate (current config is empty for a new device).
        gate = await run_batfish_gate(intent, {}, self._batfish)
        if not gate["passed"]:
            return {"ok": False, "error": f"Batfish gate failed: {gate['failures']}", "gate": gate}
        job = await self._awx.launch_job("ztp-day1", {"ticket": inp.ticket_number, "hostname": inp.hostname, "serial": inp.serial}, limit=inp.hostname)
        poll = getattr(self._awx, "poll_job", None)
        if poll is not None and job.status not in {"successful", "failed", "canceled"}:
            job = await poll(job.id, timeout_seconds=600)
        if job.status != "successful":
            return {"ok": False, "error": f"AWX ztp-day1 job {job.id} {job.status}"}
        await self._ztp.set_stage(inp.serial, "day1_applied")
        return {"ok": True, "job_id": job.id, "gate": gate}

    @activity.defn(name="ztp_activate")
    async def activate(self, inp: ActivateInput) -> dict[str, Any]:
        try:
            await self._netbox.update_device_status(inp.hostname, "active")
        except Exception as exc:  # noqa: BLE001 — recorded, not fatal
            return {"ok": False, "error": str(exc)}
        try:
            await self._snow.close(inp.ticket_number, f"ZTP complete for {inp.hostname} ({inp.serial}).")
        except Exception:
            pass
        await self._ztp.set_stage(inp.serial, "active")
        return {"ok": True}


def all_ztp_activities(activities: ZtpActivities) -> list:
    return [activities.create_reservation, activities.verify_identity, activities.day1_apply, activities.activate]
