from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
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
from vireonet.config_store.device_repos import DeviceRepoManager
from vireonet.config_store.store import ConfigStore
from vireonet.drift.baseline import commit_baseline
from vireonet.execution.awx_engine import AwxEngine
from vireonet.execution.engine import ExecutionEngine
from vireonet.execution.mapping import ExecutionMapping
from vireonet.execution.types import DeviceRef, StepRequest
from vireonet.intent import parse_intent
from vireonet.policy import PolicyEngine
from vireonet.services.backup import BEGIN_MARKER, extract_running_config
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
    mac_address: str
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
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        netbox: NetBoxProtocol,
        dhcp: DhcpProtocol,
        awx: AwxProtocol,
        batfish: BatfishProtocol,
        servicenow: ServiceNowProtocol,
        ztp_service: ZtpService,
        tftp_server: str = "10.0.0.5",
        http_base_url: str = "http://10.0.0.5/ztp",
        pnp_server_url: str = "",
        dhcp_server: str = "isc",
        ip_pool: str = "10.0.1.0/24",
        management_interface: str = "GigabitEthernet0/0",
        policy: PolicyEngine | None = None,
        engine: ExecutionEngine | None = None,
        config_store: ConfigStore | None = None,
        device_repos: DeviceRepoManager | None = None,
    ) -> None:
        self._sessions = sessions
        self._netbox = netbox
        self._dhcp = dhcp
        self._awx = awx
        if engine is not None:
            self._engine = engine
        else:
            try:
                mapping = ExecutionMapping.from_yaml("policies/execution.yaml")
            except (OSError, ValueError):
                mapping = ExecutionMapping.default()
            self._engine = AwxEngine(awx, mapping)
        self._batfish = batfish
        self._snow = servicenow
        self._ztp = ztp_service
        self._tftp = tftp_server
        self._http_base = http_base_url
        self._pnp_url = pnp_server_url
        if dhcp_server not in {"isc", "kea"}:
            raise ValueError(f"unsupported DHCP server {dhcp_server!r}")
        if not ip_pool:
            raise ValueError("ZTP IP pool must be configured")
        if not management_interface:
            raise ValueError("ZTP management interface must be configured")
        self._dhcp_server = dhcp_server
        self._ip_pool = ip_pool
        self._management_interface = management_interface
        self._policy = policy
        # D1: optional baseline wiring. When both are set, a successful
        # day-1 commits the collected running config as the device's
        # baseline via the shared commit_baseline (best-effort: a
        # baseline failure is recorded in the result, never fails day-1).
        self._config_store = config_store
        self._device_repos = device_repos

    @activity.defn(name="ztp_create_reservation")
    async def create_reservation(self, inp: ReservationInput) -> dict[str, Any]:
        ip = await self._netbox.allocate_ip(self._ip_pool, f"ztp {inp.hostname}")
        reservation = await self._dhcp.generate_reservation(inp.hostname, inp.mac_address, ip)
        config = await self._dhcp.render_config([reservation], self._dhcp_server)
        if not await self._dhcp.validate_config(config):
            return {"ok": False, "error": "DHCP config failed validation"}
        deployed = await self._dhcp.deploy_and_verify(
            config, self._dhcp_server, hostname=inp.hostname
        )
        bootstrap = render_bootstrap(
            inp.platform,
            {
                "hostname": inp.hostname,
                "serial": inp.serial,
                "tftp_server": self._tftp,
                "http_base_url": self._http_base,
                "pnp_server_url": self._pnp_url,
                "config_file": f"{inp.hostname}.cfg",
                "mgmt_interface": self._management_interface,
            },
        )
        await self._ztp.set_stage(inp.serial, "reservation_created", ip_address=ip)
        return {"ok": True, "ip": ip, "bootstrap": bootstrap, "deployed": deployed}

    @activity.defn(name="ztp_verify_identity")
    async def verify_identity(self, inp: IdentityInput) -> dict[str, Any]:
        ok, errors = await self._ztp.verify_identity(
            serial=inp.serial, model=inp.model, platform=inp.platform, version=inp.version or None
        )
        if inp.ip_address:
            await self._ztp.set_stage(
                inp.serial,
                "identity_verified" if ok else "identity_failed",
                ip_address=inp.ip_address,
            )
        return {"ok": ok, "errors": errors}

    @activity.defn(name="ztp_day1_apply")
    async def day1_apply(self, inp: Day1Input) -> dict[str, Any]:
        intent = parse_intent(
            {
                "change_type": "ztp_device",
                "serial": inp.serial,
                "model": inp.model,
                "platform": inp.platform,
                "devices": [inp.hostname],
                "site": inp.site,
                "ticket_number": inp.ticket_number,
                "requested_by": "ztp",
                "mac_address": inp.mac_address,
            }
        )
        # Batfish gate on the day-1 candidate (current config is empty for a new
        # device, so there is no current snapshot and the differential falls back
        # to a single-snapshot analyse). Only a blocking gate failure stops day-1;
        # a non-blocking failure is recorded in the gate evidence as a warning.
        blocking = self._policy.batfish_blocking if self._policy is not None else False
        gate = await run_batfish_gate(intent, {}, self._batfish, blocking=blocking)
        if gate["blocks"]:
            return {"ok": False, "error": f"Batfish gate failed: {gate['failures']}", "gate": gate}
        job = await self._engine.apply(
            StepRequest(
                ticket_number=inp.ticket_number,
                device=DeviceRef(name=inp.hostname, platform=inp.platform),
                step="apply",
                change_type="ztp_device",
                job_type="playbook",
                extra_vars={"hostname": inp.hostname, "serial": inp.serial},
                dry_run=False,
                correlation_id=inp.ticket_number,
            )
        )
        if not job.ok:
            return {"ok": False, "error": f"AWX ztp-day1 job {job.job_id} {job.status.value}"}
        await self._ztp.set_stage(inp.serial, "day1_applied")
        result: dict[str, Any] = {"ok": True, "job_id": job.job_id, "gate": gate}
        # D1 baseline: collect the running config the day-1 job just
        # pushed and commit it as the baseline, best-effort.
        if self._config_store is not None and self._device_repos is not None:
            try:
                collected = await self._engine.collect_state(
                    StepRequest(
                        ticket_number=inp.ticket_number,
                        device=DeviceRef(name=inp.hostname, platform=inp.platform),
                        step="collect_state",
                        change_type="ztp_device",
                        dry_run=True,
                        correlation_id=inp.ticket_number,
                    )
                )
                raw = str(
                    collected.output.get("running_config")
                    or collected.output.get("stdout")
                    or collected.output.get("stdout_tail")
                    or ""
                )
                if BEGIN_MARKER in raw:
                    with suppress(ValueError):
                        raw = extract_running_config(raw)
                if raw.strip():
                    result["baseline_commit"] = await commit_baseline(
                        device=inp.hostname,
                        running_config=raw,
                        ticket=inp.ticket_number,
                        config_store=self._config_store,
                        device_repos=self._device_repos,
                    )
            except Exception as exc:  # noqa: BLE001 — baseline is best-effort, never fails day-1
                result["baseline_error"] = str(exc)
        return result

    @activity.defn(name="ztp_activate")
    async def activate(self, inp: ActivateInput) -> dict[str, Any]:
        try:
            await self._netbox.update_device_status(inp.hostname, "active")
        except Exception as exc:  # noqa: BLE001 — recorded, not fatal
            return {"ok": False, "error": str(exc)}
        # The device is live in NetBox now; mark it active locally before
        # removing the reservation so remove_reservation's stage check passes.
        await self._ztp.set_stage(inp.serial, "active")
        evidence: dict[str, Any] = {}
        try:
            evidence["dhcp_reservation_removed"] = await self._ztp.remove_reservation(inp.serial)
        except Exception as exc:  # noqa: BLE001 — removal failure must not un-activate the device
            evidence["dhcp_reservation_removal_failed"] = str(exc)
        with suppress(Exception):  # closing ServiceNow must not fail activation
            await self._snow.close(
                inp.ticket_number, f"ZTP complete for {inp.hostname} ({inp.serial})."
            )
        return {"ok": True, **evidence}


def all_ztp_activities(activities: ZtpActivities) -> list[Callable[..., Any]]:
    return [
        activities.create_reservation,
        activities.verify_identity,
        activities.day1_apply,
        activities.activate,
    ]
