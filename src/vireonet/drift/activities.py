"""Drift activities (D3).

Plain activity functions on the DriftActivities class, following the
temporal/activities.py pattern (@activity.defn methods, inputs as
dataclasses). Registered in worker.py alongside the change and ZTP
activities. Activities do the I/O; workflows only orchestrate.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio import activity

from vireonet.adapters.protocols import NetBoxProtocol
from vireonet.config_store.store import ConfigStore
from vireonet.drift.attribution import AttributionProvider, UnknownAttribution
from vireonet.drift.collector import DeviceLockedForCollection, DriftCollector
from vireonet.drift.detect import DetectionEngine, DriftFinding, dedup_key
from vireonet.drift.metrics import record_drift_record, record_drift_scan_failure
from vireonet.execution.engine import ExecutionEngine
from vireonet.reconciliation.drift import DriftStore
from vireonet.safety import DeviceLockService, LockHeldError
from vireonet.structlog_config import get_logger

log = get_logger(__name__)


@dataclass
class DriftListDevicesInput:
    site: str


@dataclass
class DriftLockInput:
    device: str
    ticket: str


@dataclass
class DriftCollectInput:
    device: str
    platform: str = ""
    mgmt_ip: str = ""
    ticket: str = ""


@dataclass
class DriftDetectInput:
    device: str
    running_config: str
    ticket: str = ""
    rendered_intent: str | None = None
    netbox_state: dict[str, Any] | None = None
    collected_facts: dict[str, Any] | None = None


@dataclass
class DriftStoreFindingsInput:
    device: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    ticket: str = ""
    scan_id: str = ""


@dataclass
class DriftStartScanInput:
    site: str


@dataclass
class DriftFinishScanInput:
    scan_id: str
    status: str = "complete"
    devices_scanned: int = 0
    devices_with_drift: int = 0
    devices_failed: int = 0


class DriftActivities:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        netbox: NetBoxProtocol | None = None,
        engine: ExecutionEngine | None = None,
        config_store: ConfigStore | None = None,
        drift_store: DriftStore | None = None,
        lock_ttl_seconds: int = 300,
        collector: DriftCollector | None = None,
        detector: DetectionEngine | None = None,
        attribution: AttributionProvider | None = None,
    ) -> None:
        self._sessions = session_factory
        self._netbox = netbox
        self._engine = engine
        self._config_store = config_store
        self._drift_store = drift_store if drift_store is not None else DriftStore(session_factory)
        self._locks = DeviceLockService(session_factory, ttl_seconds=lock_ttl_seconds)
        self._collector = collector
        self._detector = detector if detector is not None else DetectionEngine()
        self._attribution = attribution if attribution is not None else UnknownAttribution()

    def _get_collector(self) -> DriftCollector:
        if self._collector is not None:
            return self._collector
        if self._engine is None:
            raise ValueError("drift collection needs an execution engine")
        self._collector = DriftCollector(self._engine, self._locks)
        return self._collector

    @activity.defn(name="drift_start_scan")
    async def start_scan(self, inp: DriftStartScanInput) -> dict[str, Any]:
        scan_id = await self._drift_store.create_scan(inp.site)
        log.info("drift scan started", site=inp.site, scan_id=scan_id)
        return {"scan_id": scan_id}

    @activity.defn(name="drift_finish_scan")
    async def finish_scan(self, inp: DriftFinishScanInput) -> dict[str, Any]:
        await self._drift_store.finish_scan(
            inp.scan_id,
            status=inp.status,
            devices_scanned=inp.devices_scanned,
            devices_with_drift=inp.devices_with_drift,
            devices_failed=inp.devices_failed,
        )
        return {"scan_id": inp.scan_id, "status": inp.status}

    @activity.defn(name="drift_list_devices")
    async def list_devices(self, inp: DriftListDevicesInput) -> dict[str, Any]:
        if self._netbox is None:
            raise ValueError("drift scan needs a NetBox adapter")
        devices = await self._netbox.list_devices(site=inp.site)
        return {"devices": [d.name for d in devices]}

    @activity.defn(name="drift_acquire_lock")
    async def acquire_lock(self, inp: DriftLockInput) -> dict[str, Any]:
        try:
            await self._locks.acquire(inp.device, inp.ticket, holder="drift-scan")
            return {"ok": True}
        except LockHeldError as exc:
            return {"ok": False, "error": str(exc), "holder": exc.holder}

    @activity.defn(name="drift_release_lock")
    async def release_lock(self, inp: DriftLockInput) -> dict[str, Any]:
        await self._locks.release(inp.device, inp.ticket)
        return {"ok": True}

    @activity.defn(name="drift_collect_device")
    async def collect_device(self, inp: DriftCollectInput) -> dict[str, Any]:
        try:
            text = await self._get_collector().collect(
                inp.device, inp.platform, inp.mgmt_ip, inp.ticket
            )
            return {"ok": True, "running_config": text}
        except DeviceLockedForCollection as exc:
            # Locked by a change: skipped, not failed (D1 collector contract).
            log.info("drift collect skipped (locked)", device=inp.device, holder=exc.holder)
            return {"ok": True, "skipped": True, "reason": str(exc)}
        except Exception as exc:  # noqa: BLE001 — per-device failure, recorded not raised
            record_drift_scan_failure(inp.device, "collect_failed")
            log.warning("drift collect failed", device=inp.device, error=str(exc))
            return {"ok": False, "error": str(exc)}

    @activity.defn(name="drift_detect_device")
    async def detect_device(self, inp: DriftDetectInput) -> dict[str, Any]:
        baseline_config: str | None = None
        baseline_hash: str | None = None
        if self._config_store is not None:
            latest = await self._config_store.latest(inp.device)
            if latest is not None:
                baseline_config = latest.content
                baseline_hash = latest.content_hash
        findings = await self._detector.scan_device(
            device=inp.device,
            running_config=inp.running_config,
            baseline_config=baseline_config,
            baseline_hash=baseline_hash,
            rendered_intent=inp.rendered_intent,
            netbox_state=inp.netbox_state,
            collected_facts=inp.collected_facts,
        )
        return {"findings": [f.model_dump(mode="json") for f in findings]}

    @activity.defn(name="drift_store_findings")
    async def store_findings(self, inp: DriftStoreFindingsInput) -> dict[str, Any]:
        stored: list[str] = []
        linked: list[dict[str, Any]] = []
        updated: list[str] = []
        for raw in inp.findings:
            finding = DriftFinding(**raw)
            key = dedup_key(finding, inp.device)
            existing = await self._drift_store.find_existing(
                inp.device, finding.kind, str(finding.detail.get("running_hash"))
            )
            if existing is not None:
                await self._drift_store.touch(str(existing.id))
                updated.append(str(existing.id))
                continue
            # Change in progress on this device? Link the finding to
            # that ticket and do NOT create an unmanaged record yet —
            # the next scan after the change completes will judge it.
            active_ticket = await self._drift_store.find_active_change(inp.device)
            if active_ticket is not None:
                linked.append({"dedup_key": key, "ticket": active_ticket, "kind": finding.kind})
                continue
            attribution = await self._attribution.attribute(inp.device, "")
            actor = str(attribution.get("actor") or "unknown")
            record_id = await self._drift_store.create(
                inp.device,
                finding.kind,
                finding.severity,
                finding.diff,
                str(finding.detail.get("baseline_hash"))
                if finding.detail.get("baseline_hash") is not None
                else None,
                str(finding.detail.get("running_hash"))
                if finding.detail.get("running_hash") is not None
                else None,
                inp.ticket or None,
                status=finding.status,
                attributed_actor=actor if actor != "unknown" else None,
            )
            record_drift_record(finding.severity, finding.kind)
            stored.append(str(record_id))
        return {"stored": stored, "linked": linked, "updated": updated}


def all_drift_activities(activities: DriftActivities) -> list[Callable[..., Any]]:
    return [
        activities.start_scan,
        activities.finish_scan,
        activities.list_devices,
        activities.acquire_lock,
        activities.release_lock,
        activities.collect_device,
        activities.detect_device,
        activities.store_findings,
    ]
