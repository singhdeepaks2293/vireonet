from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio import activity

from vireonet.adapters.protocols import (
    AwxProtocol,
    MonitoringProtocol,
    NetBoxProtocol,
    NotificationProtocol,
    ServiceNowProtocol,
)
from vireonet.checks.base import CheckContext, CheckEngine
from vireonet.checks.plugins import post_checks_for, pre_checks_for
from vireonet.execution.awx_engine import AwxEngine
from vireonet.execution.engine import ExecutionEngine
from vireonet.execution.mapping import ExecutionMapping
from vireonet.execution.types import DeviceRef, StepName, StepRequest
from vireonet.intent import parse_intent
from vireonet.models import Change
from vireonet.notifications import NotificationService
from vireonet.observability import record_change, record_check_failure
from vireonet.planner import plan as make_plan
from vireonet.policy import PolicyEngine
from vireonet.reconciliation.service import ReconciliationService
from vireonet.safety import DeviceLockService, LockHeldError, run_waves
from vireonet.state_machine import ChangeState, record_transition
from vireonet.validation import validate_intent


def _default_engine(awx: AwxProtocol) -> ExecutionEngine:
    """Engine over a raw AWX adapter, using the repo mapping when present."""
    try:
        mapping = ExecutionMapping.from_yaml("policies/execution.yaml")
    except (OSError, ValueError):
        mapping = ExecutionMapping.default()
    return AwxEngine(awx, mapping)


@dataclass
class ValidateInput:
    change_id: str
    intent: dict[str, Any]


@dataclass
class PlanInput:
    change_id: str
    intent: dict[str, Any]


@dataclass
class TransitionInput:
    change_id: str
    to_state: str
    actor: str = "temporal"
    reason: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class CheckInput:
    change_id: str
    intent: dict[str, Any]
    kind: str = "pre"


@dataclass
class ApplyInput:
    change_id: str
    intent: dict[str, Any]


@dataclass
class CloseInput:
    change_id: str
    intent: dict[str, Any]


@dataclass
class ReconcileInput:
    change_id: str
    intent: dict[str, Any]


@dataclass
class RollbackInput:
    change_id: str
    intent: dict[str, Any]
    evidence: dict[str, Any] = field(default_factory=dict)


class ChangeActivities:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        servicenow: ServiceNowProtocol,
        netbox: NetBoxProtocol,
        awx: AwxProtocol,
        policy: PolicyEngine,
        lock_ttl_seconds: int = 1800,
        wave_size: int = 2,
        abort_threshold: int = 1,
        notification: NotificationProtocol | None = None,
        monitoring: MonitoringProtocol | None = None,
        monitoring_silence_minutes: int = 60,
        engine: ExecutionEngine | None = None,
        reconciliation: ReconciliationService | None = None,
    ) -> None:
        self._sessions = session_factory
        self._snow = servicenow
        self._netbox = netbox
        self._awx = awx
        self._engine = engine if engine is not None else _default_engine(awx)
        self._reconciliation = reconciliation
        self._policy = policy
        self._locks = DeviceLockService(session_factory, ttl_seconds=lock_ttl_seconds)
        self._wave_size = wave_size
        self._abort_threshold = abort_threshold
        self._backups: dict[str, dict[str, int]] = {}
        self._notifications = (
            NotificationService(notification, channel="slack") if notification is not None else None
        )
        self._monitoring = monitoring
        self._silence_minutes = monitoring_silence_minutes
        self._silences: dict[str, dict[str, str]] = {}  # ticket -> {device: silence_id}

    async def _silence_monitoring(self, ticket: str, devices: list[str]) -> None:
        if self._monitoring is None:
            return
        silences: dict[str, str] = {}
        for device in devices:
            # monitoring silence must not fail a change; it is best-effort
            with suppress(Exception):
                silences[device] = await self._monitoring.silence(device, self._silence_minutes)
        self._silences[ticket] = silences

    async def _unsilence_monitoring(self, ticket: str) -> None:
        if self._monitoring is None:
            return
        for device, silence_id in self._silences.pop(ticket, {}).items():
            try:
                unsilence = getattr(self._monitoring, "unsilence", None)
                if unsilence is not None:
                    await unsilence(silence_id)
                await self._monitoring.enable_monitoring(device)
            except Exception:
                pass

    async def _load(self, session: AsyncSession, change_id: str) -> Change:
        import uuid as _uuid

        try:
            pk: object = _uuid.UUID(str(change_id))
        except (ValueError, AttributeError, TypeError):
            pk = change_id
        change = await session.get(Change, pk)
        if change is None:
            raise ValueError(f"change {change_id} not found")
        return change

    async def _persist_backup_manifest(
        self, change_id: str, ticket: str, backups: dict[str, int]
    ) -> None:
        """Persist AWX backup job references in the Change JSON before apply."""
        async with self._sessions() as session:
            change = await self._load(session, change_id)
            intent = dict(change.intent or {})
            intent["_vireonet_backup_manifest"] = {
                "ticket_number": ticket,
                "backups": {device: int(job_id) for device, job_id in backups.items()},
            }
            change.intent = intent
            await session.commit()

    async def _load_backup_manifest(self, change_id: str) -> dict[str, int]:
        """Read rollback references from PostgreSQL, never worker memory."""
        async with self._sessions() as session:
            change = await self._load(session, change_id)
            manifest = (change.intent or {}).get("_vireonet_backup_manifest", {})
            raw = manifest.get("backups", {}) if isinstance(manifest, dict) else {}
            if not isinstance(raw, dict):
                raise ValueError(f"invalid persisted backup manifest for change {change_id}")
            return {str(device): int(job_id) for device, job_id in raw.items()}

    async def _renew_change_locks(self, intent: Any) -> bool:
        return await self._locks.renew_all(list(intent.devices), intent.ticket_number)

    async def _run_step(self, req: StepRequest) -> dict[str, Any]:
        """Run one engine step. Return shape matches the old _launch_and_poll:
        {job_id, status, ok} plus duration_seconds and evidence."""
        method = {
            "backup": self._engine.backup,
            "pre_check": self._engine.pre_check,
            "dry_run": self._engine.dry_run,
            "apply": self._engine.apply,
            "post_check": self._engine.post_check,
            "rollback": self._engine.rollback,
            "collect_state": self._engine.collect_state,
        }[req.step]
        result = await method(req)
        return {
            "job_id": result.job_id,
            "status": result.status.value,
            "ok": result.ok,
            "duration_seconds": result.duration_seconds,
            "evidence": result.evidence,
        }

    def _step_request(
        self,
        intent: Any,
        device: str,
        step: StepName,
        *,
        dry_run: bool = True,
        extra_vars: dict[str, str] | None = None,
    ) -> StepRequest:
        platform = str(getattr(intent, "platform", "") or "")
        return StepRequest(
            ticket_number=intent.ticket_number,
            device=DeviceRef(name=device, platform=platform),
            step=step,
            change_type=intent.change_type,
            intent=dict(intent.model_dump()) if hasattr(intent, "model_dump") else {},
            dry_run=dry_run,
            extra_vars=extra_vars or {},
            correlation_id=intent.ticket_number,
        )

    @activity.defn(name="validate_change")
    async def validate_change(self, inp: ValidateInput) -> dict[str, Any]:
        intent = parse_intent(inp.intent)
        result = await validate_intent(intent, self._netbox, self._policy)
        return {"ok": result.ok, "errors": result.errors, "evidence": result.evidence}

    @activity.defn(name="plan_change")
    async def plan_change(self, inp: PlanInput) -> dict[str, Any]:
        return make_plan(parse_intent(inp.intent)).to_dict()

    @activity.defn(name="transition_change")
    async def transition_change(self, inp: TransitionInput) -> dict[str, Any]:
        async with self._sessions() as session:
            change = await self._load(session, inp.change_id)
            await record_transition(
                session,
                change,
                ChangeState(inp.to_state),
                actor=inp.actor,
                reason=inp.reason,
                evidence=inp.evidence,
            )
            await session.commit()
            return {"state": inp.to_state}

    @activity.defn(name="pre_check_change")
    async def pre_check_change(self, inp: CheckInput) -> dict[str, Any]:
        intent = parse_intent(inp.intent)
        # 1. Lock first — a failed pre-check must not leave a lock held by a dead workflow.
        try:
            await self._locks.acquire_all(list(intent.devices), intent.ticket_number)
        except LockHeldError as exc:
            return {"ok": False, "failures": [str(exc)], "evidence": {"lock_holder": exc.holder}}
        await self._silence_monitoring(intent.ticket_number, list(intent.devices))
        # 2. Backup every device; the backup job id is the rollback source.
        backups: dict[str, int] = {}
        for device in intent.devices:
            res = await self._run_step(self._step_request(intent, device, "backup"))
            if not res["ok"]:
                await self._locks.release_all(list(intent.devices), intent.ticket_number)
                return {
                    "ok": False,
                    "failures": [f"backup failed on {device}: {res}"],
                    "evidence": {"backups": backups},
                }
            backups[device] = int(res["job_id"])
        try:
            await self._persist_backup_manifest(inp.change_id, intent.ticket_number, backups)
        except Exception as exc:  # noqa: BLE001 — rollback evidence must survive worker restart
            await self._locks.release_all(list(intent.devices), intent.ticket_number)
            return {
                "ok": False,
                "failures": [f"could not persist backup manifest: {exc}"],
                "evidence": {"backups": backups},
            }
        self._backups[intent.ticket_number] = backups  # compatibility cache only
        # 3. Baseline via AWX pre-check playbook (neighbours, interfaces, config hash).
        baseline_res = await self._run_step(
            self._step_request(intent, ",".join(intent.devices), "pre_check")
        )
        baseline: dict[str, Any] = (
            {"pre_check_job_id": baseline_res["job_id"]} if baseline_res["ok"] else {}
        )
        # 4. Plugin checks, with backup + baseline evidence.
        ctx = CheckContext(
            intent=intent,
            ticket_number=intent.ticket_number,
            netbox=self._netbox,
            awx=self._awx,
            backups=backups,
            baseline=baseline,
            locks_held_by=intent.ticket_number,
        )
        # BackupTaken + NeighbourBaseline need backups/baseline; the other pre-checks
        # run regardless. If baseline failed, report it as a check failure.
        results = await CheckEngine().run(pre_checks_for(intent), ctx)
        if not baseline_res["ok"]:
            results.append(
                type(results[0])(
                    name="neighbour_baseline",
                    status="fail",
                    message="AWX pre-check job failed",
                    evidence=baseline,
                )
            )
        ok = CheckEngine.all_pass(results)
        if not ok:
            await self._locks.release_all(list(intent.devices), intent.ticket_number)
        return {
            "ok": ok,
            "failures": [r.message for r in results if not r.ok],
            "evidence": {
                "checks": [
                    {"name": r.name, "status": r.status, "message": r.message} for r in results
                ],
                "backups": backups,
            },
        }

    @activity.defn(name="post_check_change")
    async def post_check_change(self, inp: CheckInput) -> dict[str, Any]:
        intent = parse_intent(inp.intent)
        backups = await self._load_backup_manifest(inp.change_id)
        if not await self._renew_change_locks(intent):
            return {
                "ok": False,
                "failures": ["device lock lease was lost before post-check"],
                "evidence": {"backup_devices": sorted(backups)},
            }
        ctx = CheckContext(
            intent=intent,
            ticket_number=intent.ticket_number,
            netbox=self._netbox,
            awx=self._awx,
            backups=backups,
            baseline={"dry_run_job_id": 0},
            locks_held_by=intent.ticket_number,
        )
        # AWX post-check playbook first (config match, neighbours, diff).
        post_res = await self._run_step(
            self._step_request(intent, ",".join(intent.devices), "post_check")
        )
        results = await CheckEngine().run(post_checks_for(intent), ctx)
        ok = CheckEngine.all_pass(results) and bool(post_res["ok"])
        failures = [r.message for r in results if not r.ok]
        if not post_res["ok"]:
            failures.append(f"AWX post-check job failed: {post_res}")
        return {
            "ok": ok,
            "failures": failures,
            "evidence": {
                "checks": [
                    {"name": r.name, "status": r.status, "message": r.message} for r in results
                ],
                "post_check_job": post_res,
            },
        }

    @activity.defn(name="apply_change")
    async def apply_change(self, inp: ApplyInput) -> dict[str, Any]:
        intent = parse_intent(inp.intent)
        backups = await self._load_backup_manifest(inp.change_id)
        if set(backups) != set(intent.devices):
            return {
                "ok": False,
                "failures": ["durable backup manifest is missing or incomplete; refusing apply"],
                "evidence": {"backup_devices": sorted(backups), "requested_devices": sorted(intent.devices)},
            }
        if not await self._renew_change_locks(intent):
            return {
                "ok": False,
                "failures": ["device lock lease was lost before apply"],
                "evidence": {"backup_devices": sorted(backups)},
            }
        # Dry-run first, for the whole change. A non-empty unexpected diff fails before any device is touched.
        dry = await self._run_step(
            self._step_request(
                intent,
                ",".join(intent.devices),
                "dry_run",
                dry_run=True,
                extra_vars={"change_type": intent.change_type},
            )
        )
        if not dry["ok"]:
            return {
                "ok": False,
                "failures": [f"dry-run failed: {dry}"],
                "evidence": {"dry_run": dry},
            }

        # Apply in waves. Abort threshold stops the rollout; completed waves are rolled back by rollback_change.
        async def apply_wave(wave: list[str]) -> list[str]:
            if not await self._renew_change_locks(intent):
                return list(wave)
            res = await self._run_step(
                self._step_request(
                    intent,
                    ",".join(wave),
                    "apply",
                    dry_run=False,
                    extra_vars={"change_type": intent.change_type},
                )
            )
            return [] if res["ok"] else list(wave)

        waves = await run_waves(
            list(intent.devices),
            apply_wave,
            wave_size=self._wave_size,
            abort_threshold=self._abort_threshold,
        )
        if waves.aborted or waves.failed_devices:
            if self._notifications:
                await self._notifications.change_failed(
                    intent.ticket_number, f"apply failed on {waves.failed_devices}"
                )
            for _ in waves.failed_devices:
                record_check_failure("apply")
            return {
                "ok": False,
                "failures": [f"apply failed on {waves.failed_devices}, aborted={waves.aborted}"],
                "evidence": {
                    "waves": waves.completed_waves,
                    "failed": waves.failed_devices,
                    "dry_run": dry,
                },
            }
        return {"ok": True, "evidence": {"dry_run": dry, "waves": waves.completed_waves}}

    @activity.defn(name="reconcile_change")
    async def reconcile_change(self, inp: ReconcileInput) -> dict[str, Any]:
        """Reconcile Git/NetBox with the post-checked device state (E3).

        Runs only after post_check succeeds (the workflow enforces this);
        a failed post_check rolls back instead and never reaches here, so
        no Git/NetBox write happens for a failed change.
        """
        intent = parse_intent(inp.intent)
        platform = str(getattr(intent, "platform", "") or "")
        if self._reconciliation is None:
            # No reconciliation service wired (e.g. activities constructed
            # directly in tests): nothing was written anywhere, so there is
            # nothing to reconcile. The worker always wires the service.
            return {
                "ok": True,
                "status": "reconciled",
                "devices": list(intent.devices),
                "evidence": {"reconciliation": "not-configured"},
            }
        outcomes: list[dict[str, Any]] = []
        for device in intent.devices:
            collect = await self._engine.collect_state(
                StepRequest(
                    ticket_number=intent.ticket_number,
                    device=DeviceRef(name=device, platform=platform),
                    step="collect_state",
                    change_type=intent.change_type,
                    correlation_id=intent.ticket_number,
                )
            )
            if not collect.ok:
                outcomes.append({
                    "device": device,
                    "status": "reconciliation_pending",
                    "failures": [f"state collection failed: {collect.status.value}"],
                })
                continue
            outcome = await self._reconciliation.reconcile(
                ticket=intent.ticket_number,
                device=device,
                job_type="playbook",
                script_name=None,
                script_sha=None,
                awx_job_id=collect.job_id or 0,
                declared=list(collect.declared_changes),
                collect_result=collect,
                manifest_writes=[],
            )
            outcomes.append(
                {
                    "device": device,
                    "status": outcome.status,
                    "git_commit": outcome.git_commit,
                    "netbox_applied": outcome.netbox_applied,
                    "drift_records": outcome.drift_records,
                    "failures": outcome.failures,
                }
            )
        pending = any(o["status"] == "reconciliation_pending" for o in outcomes)
        return {
            "ok": not pending,
            "status": "reconciliation_pending" if pending else "reconciled",
            "devices": outcomes,
            "evidence": {"outcomes": outcomes},
        }

    @activity.defn(name="close_change")
    async def close_change(self, inp: CloseInput) -> dict[str, Any]:
        intent = parse_intent(inp.intent)
        # Close refuses a change stuck in reconciliation_pending: only an
        # approver override (POST /changes/{id}/reconcile-override, which
        # moves the record to reconciled) may let it close.
        try:
            async with self._sessions() as session:
                change = await self._load(session, inp.change_id)
                state = ChangeState(change.state) if isinstance(change.state, str) else change.state
        except Exception:  # noqa: BLE001 — an unloadable change keeps the old behaviour
            state = None
        if state == ChangeState.RECONCILIATION_PENDING:
            return {
                "ok": False,
                "failures": [
                    "change is in reconciliation_pending; an approver override is required"
                ],
                "evidence": {"state": state.value},
            }
        evidence: dict[str, Any] = {}
        for name in intent.devices:
            try:
                await self._netbox.update_device_status(name, "active")
                await self._netbox.add_journal_entry(
                    name, "change", f"Change {intent.ticket_number} applied."
                )
            except Exception as exc:  # noqa: BLE001 — external state must be confirmed
                evidence[f"{name}_netbox_error"] = str(exc)
        try:
            await self._snow.close(
                intent.ticket_number, f"Change {intent.ticket_number} applied and post-checked."
            )
        except Exception as exc:  # noqa: BLE001 — do not claim closure while ITSM is open
            evidence["servicenow_error"] = str(exc)
        if evidence:
            # Retain the lock and backup evidence for an operator/retry path.
            # A partial external close is an uncertain outcome, not success.
            return {
                "ok": False,
                "status": "closure_pending",
                "failures": ["NetBox and/or ServiceNow closure could not be confirmed"],
                "evidence": evidence,
            }
        await self._unsilence_monitoring(intent.ticket_number)
        if self._notifications:
            await self._notifications.change_completed(intent.ticket_number)
        record_change("closed")
        await self._locks.release_all(list(intent.devices), intent.ticket_number)
        self._backups.pop(intent.ticket_number, None)
        return {"ok": True, "evidence": evidence}

    @activity.defn(name="rollback_change")
    async def rollback_change(self, inp: RollbackInput) -> dict[str, Any]:
        intent = parse_intent(inp.intent)
        try:
            backups = await self._load_backup_manifest(inp.change_id)
        except Exception as exc:  # noqa: BLE001 — missing rollback source is an operator incident
            return {
                "ok": False,
                "failures": [f"durable backup manifest unavailable: {exc}"],
                "evidence": {"trigger": inp.evidence},
            }
        if set(backups) != set(intent.devices):
            return {
                "ok": False,
                "failures": ["durable backup manifest is incomplete; rollback cannot be verified"],
                "evidence": {"backup_devices": sorted(backups), "requested_devices": sorted(intent.devices)},
            }
        # Rollback restores the pre-change backup per device, via the AWX rollback playbook.
        # The backup job id is the rollback source. Keep the lock and references
        # until AWX confirms success; a failed/unknown rollback needs intervention.
        res = await self._run_step(
            self._step_request(
                intent,
                ",".join(intent.devices),
                "rollback",
                dry_run=False,
                extra_vars={"backups": str(backups)},
            )
        )
        if not res["ok"]:
            return {
                "ok": False,
                "job_id": res["job_id"],
                "failures": ["AWX did not confirm rollback success"],
                "evidence": {"rollback": res, "trigger": inp.evidence},
            }
        await self._unsilence_monitoring(intent.ticket_number)
        if self._notifications:
            await self._notifications.rolled_back(intent.ticket_number, str(inp.evidence))
        record_change("rolled_back")
        await self._locks.release_all(list(intent.devices), intent.ticket_number)
        self._backups.pop(intent.ticket_number, None)
        return {"ok": True, "job_id": res["job_id"], "evidence": inp.evidence}


def all_activities(activities: ChangeActivities) -> list[Callable[..., Any]]:
    return [
        activities.validate_change,
        activities.plan_change,
        activities.transition_change,
        activities.pre_check_change,
        activities.post_check_change,
        activities.apply_change,
        activities.reconcile_change,
        activities.close_change,
        activities.rollback_change,
    ]
