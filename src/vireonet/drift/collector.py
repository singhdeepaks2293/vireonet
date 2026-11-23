"""Drift collection (D1): read-only running-config collection via AWX.

The collector refuses to run while a change holds the device lock — a
scan must never race an apply — and it refuses to ask for a write
credential. Note the limit, stated plainly: the "read-only credential"
is a request-side check. The collector only ever asks AWX for the
read-only credential; it cannot prove the AWX credential itself is
actually read-only. That is an AWX-side control, not something this
code can verify.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from vireonet.adapters.errors import AdapterUnavailable
from vireonet.execution.engine import ExecutionEngine
from vireonet.execution.types import DeviceRef, StepRequest, StepResult
from vireonet.safety import DeviceLockService, LockHeldError
from vireonet.services.backup import BEGIN_MARKER, extract_running_config
from vireonet.structlog_config import get_logger

log = get_logger(__name__)


class DeviceLockedForCollection(LockHeldError):
    """Raised when a change holds the device lock.

    D3 treats this as "skipped, not failed": the device is recorded as
    skipped and the scan continues with the other devices.
    """


class DriftCollector:
    """Collect a device's running config through the execution engine."""

    def __init__(
        self,
        engine: ExecutionEngine,
        locks: DeviceLockService,
        timeout_seconds: int = 60,
        max_concurrent: int = 5,
    ) -> None:
        self._engine = engine
        self._locks = locks
        self.timeout_seconds = timeout_seconds
        self.max_concurrent = max_concurrent

    async def collect(self, device: str, platform: str, mgmt_ip: str, ticket: str) -> str:
        """Return the device's running config text.

        Raises DeviceLockedForCollection if a change holds the lock, and
        AdapterUnavailable if the engine step fails or returns no config.
        """
        holder = await self._locks.is_locked(device)
        if holder is not None:
            raise DeviceLockedForCollection(device, holder)
        # Read-only by request: the collector asks AWX for the read-only
        # credential only. It cannot prove the AWX credential is actually
        # read-only — see the module docstring.
        req = StepRequest(
            ticket_number=ticket,
            device=DeviceRef(name=device, platform=platform, mgmt_ip=mgmt_ip or None),
            step="collect_state",
            job_type="playbook",
            dry_run=True,
            extra_vars={"credential": "read-only"},
            timeout_seconds=self.timeout_seconds,
            correlation_id=ticket,
        )
        result: StepResult = await self._engine.collect_state(req)
        if not result.ok:
            raise AdapterUnavailable(
                "awx", "collect_state", f"collect failed on {device}: {result.status.value}"
            )
        raw = (
            result.output.get("running_config")
            or result.output.get("stdout")
            or result.output.get("stdout_tail")
            or ""
        )
        raw = str(raw)
        if BEGIN_MARKER in raw:
            with suppress(ValueError):
                raw = extract_running_config(raw)
        if not raw.strip():
            raise AdapterUnavailable("awx", "collect_state", f"no running config for {device}")
        log.info("drift collected", device=device, ticket=ticket)
        return raw

    async def collect_many(
        self, devices: list[dict[str, Any]], ticket: str
    ) -> dict[str, str | Exception]:
        """Collect several devices concurrently (bounded by max_concurrent).

        Returns {device: config} on success or {device: exception} on
        failure, per device — one device's failure never aborts the rest.
        """
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def _one(spec: dict[str, Any]) -> tuple[str, str | Exception]:
            device = str(spec["device"])
            async with semaphore:
                try:
                    text = await self.collect(
                        device,
                        str(spec.get("platform", "")),
                        str(spec.get("mgmt_ip", "")),
                        ticket,
                    )
                    return device, text
                except Exception as exc:  # noqa: BLE001 — collected per device, not raised
                    return device, exc

        results = await asyncio.gather(*[_one(spec) for spec in devices])
        return dict(results)
