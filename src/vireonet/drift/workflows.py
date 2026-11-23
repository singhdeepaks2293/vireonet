"""Drift scan workflows (D3).

DriftScanWorkflow (id drift-scan-<site>-<date>): lists the site's
devices via an activity (workflows never call NetBox directly), then
fans out one DriftDeviceWorkflow per device as a child workflow with
idempotent id drift-<device>-<date>.

DriftDeviceWorkflow: acquire a short device lock, collect, detect,
dedup/store, release the lock in a finally. Workflow code stays
deterministic: no I/O, no datetime.now(), no random — only activities.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from vireonet.drift.activities import (
        DriftCollectInput,
        DriftDetectInput,
        DriftFinishScanInput,
        DriftListDevicesInput,
        DriftLockInput,
        DriftStartScanInput,
        DriftStoreFindingsInput,
    )
    from vireonet.temporal.retry import retry_policy


@dataclass
class DriftScanInput:
    site: str
    date: str  # YYYY-MM-DD; the caller supplies the date (workflows have no clock)
    ticket: str = ""


@dataclass
class DriftDeviceInput:
    device: str
    platform: str = ""
    mgmt_ip: str = ""
    site: str = ""
    date: str = ""
    ticket: str = ""
    scan_id: str = ""


@workflow.defn
class DriftScanWorkflow:
    @workflow.run
    async def run(self, inp: DriftScanInput) -> dict[str, Any]:
        scan_id = await workflow.execute_activity(
            "drift_start_scan",
            DriftStartScanInput(site=inp.site),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=retry_policy(),
        )
        scan_id = str(scan_id["scan_id"])
        devices = await workflow.execute_activity(
            "drift_list_devices",
            DriftListDevicesInput(site=inp.site),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=retry_policy(),
        )
        names: list[str] = [str(d) for d in devices.get("devices", [])]
        results = await asyncio.gather(
            *[
                workflow.execute_child_workflow(
                    DriftDeviceWorkflow.run,
                    DriftDeviceInput(
                        device=name,
                        site=inp.site,
                        date=inp.date,
                        ticket=inp.ticket or f"drift-scan-{inp.site}-{inp.date}",
                        scan_id=scan_id,
                    ),
                    id=f"drift-{name}-{inp.date}",
                )
                for name in names
            ],
            return_exceptions=True,
        )
        scanned = with_drift = failed = 0
        for result in results:
            if isinstance(result, Exception):
                failed += 1
                continue
            scanned += 1
            if isinstance(result, dict) and result.get("drift_found"):
                with_drift += 1
            if isinstance(result, dict) and not result.get("ok", True):
                failed += 1
        await workflow.execute_activity(
            "drift_finish_scan",
            DriftFinishScanInput(
                scan_id=scan_id,
                status="complete" if failed == 0 else "failed",
                devices_scanned=scanned,
                devices_with_drift=with_drift,
                devices_failed=failed,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=retry_policy(),
        )
        return {
            "scan_id": scan_id,
            "site": inp.site,
            "devices_scanned": scanned,
            "devices_with_drift": with_drift,
            "devices_failed": failed,
        }


@workflow.defn
class DriftDeviceWorkflow:
    @workflow.run
    async def run(self, inp: DriftDeviceInput) -> dict[str, Any]:
        ticket = inp.ticket or f"drift-{inp.device}-{inp.date}"
        locked = await workflow.execute_activity(
            "drift_acquire_lock",
            DriftLockInput(device=inp.device, ticket=ticket),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=retry_policy(),
        )
        if not locked.get("ok"):
            return {
                "device": inp.device,
                "ok": False,
                "skipped": True,
                "reason": str(locked.get("error", "lock held")),
            }
        try:
            collected = await workflow.execute_activity(
                "drift_collect_device",
                DriftCollectInput(
                    device=inp.device,
                    platform=inp.platform,
                    mgmt_ip=inp.mgmt_ip,
                    ticket=ticket,
                ),
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=retry_policy(maximum_attempts=2),
            )
            if collected.get("skipped"):
                return {"device": inp.device, "ok": True, "skipped": True}
            if not collected.get("ok"):
                return {
                    "device": inp.device,
                    "ok": False,
                    "error": str(collected.get("error", "collect failed")),
                }
            detected = await workflow.execute_activity(
                "drift_detect_device",
                DriftDetectInput(
                    device=inp.device,
                    running_config=str(collected.get("running_config", "")),
                    ticket=ticket,
                ),
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry_policy(),
            )
            findings = detected.get("findings", [])
            stored = await workflow.execute_activity(
                "drift_store_findings",
                DriftStoreFindingsInput(
                    device=inp.device,
                    findings=[dict(f) for f in findings],
                    ticket=ticket,
                    scan_id=inp.scan_id,
                ),
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry_policy(),
            )
            return {
                "device": inp.device,
                "ok": True,
                "drift_found": bool(stored.get("stored")),
                "stored": stored.get("stored", []),
                "linked": stored.get("linked", []),
            }
        finally:
            await workflow.execute_activity(
                "drift_release_lock",
                DriftLockInput(device=inp.device, ticket=ticket),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry_policy(),
            )
