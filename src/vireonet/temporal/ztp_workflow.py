from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from vireonet.temporal.retry import retry_policy
    from vireonet.temporal.ztp_activities import (
        ActivateInput,
        Day1Input,
        IdentityInput,
        ReservationInput,
    )


@dataclass
class ZtpWorkflowInput:
    serial: str
    hostname: str
    mac_address: str
    model: str
    platform: str
    site: str
    ticket_number: str
    callback_timeout_seconds: int = 3600


@dataclass
class CallbackPayload:
    serial: str
    model: str
    platform: str
    version: str = ""
    ip_address: str = ""


@workflow.defn
class ZtpWorkflow:
    def __init__(self) -> None:
        self._callback: CallbackPayload | None = None

    @workflow.run
    async def run(self, inp: ZtpWorkflowInput) -> str:
        # 1. DHCP reservation (NetBox IP + rendered config, Git-deployed).
        res = await workflow.execute_activity(
            "ztp_create_reservation", ReservationInput(serial=inp.serial, hostname=inp.hostname, mac_address=inp.mac_address, model=inp.model, platform=inp.platform, site=inp.site, ticket_number=inp.ticket_number),
            start_to_close_timeout=timedelta(minutes=5), retry_policy=retry_policy(),
        )
        if not res["ok"]:
            return "failed"
        # 2. Wait for the device callback (one-time token verified in the API, not here).
        try:
            await workflow.wait_condition(lambda: self._callback is not None, timeout=timedelta(seconds=inp.callback_timeout_seconds))
        except TimeoutError:
            return "callback_timeout"
        assert self._callback is not None
        # 3. Identity check — serial / model / platform vs NetBox. No day-1 before this passes.
        ident = await workflow.execute_activity(
            "ztp_verify_identity",
            IdentityInput(serial=inp.serial, model=self._callback.model, platform=self._callback.platform, version=self._callback.version, ip_address=self._callback.ip_address),
            start_to_close_timeout=timedelta(minutes=5), retry_policy=retry_policy(),
        )
        if not ident["ok"]:
            return "identity_failed"
        # 4. Day-1: render + Batfish gate + AWX push.
        day1 = await workflow.execute_activity(
            "ztp_day1_apply", Day1Input(serial=inp.serial, hostname=inp.hostname, model=inp.model, platform=inp.platform, site=inp.site, ticket_number=inp.ticket_number),
            start_to_close_timeout=timedelta(minutes=30), retry_policy=retry_policy(maximum_attempts=3),
        )
        if not day1["ok"]:
            return "day1_failed"
        # 5. Activate in NetBox + close in ServiceNow.
        await workflow.execute_activity(
            "ztp_activate", ActivateInput(serial=inp.serial, hostname=inp.hostname, ticket_number=inp.ticket_number),
            start_to_close_timeout=timedelta(minutes=5), retry_policy=retry_policy(),
        )
        return "active"

    @workflow.signal
    def device_callback(self, payload: CallbackPayload) -> None:
        # Only the first callback wins; a duplicate callback is ignored.
        if self._callback is None:
            self._callback = payload

    @workflow.query
    def get_state(self) -> dict:
        return {"callback_received": self._callback is not None, "callback": self._callback.__dict__ if self._callback else None}
