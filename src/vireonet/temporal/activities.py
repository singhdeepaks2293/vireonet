from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from vireonet.adapters.protocols import (
    MonitoringProtocol,
    NotificationProtocol,
)
from vireonet.checks.base import CheckContext, CheckEngine
from vireonet.checks.plugins import post_checks_for, pre_checks_for
from vireonet.intent import parse_intent
from vireonet.models import Change
from vireonet.notifications import NotificationService
from vireonet.observability import record_change, record_check_failure
from vireonet.planner import plan as make_plan
from vireonet.safety import DeviceLockService, LockHeldError, run_waves
from vireonet.state_machine import ChangeState, record_transition
from vireonet.validation import validate_intent


@dataclass
class ValidateInput:
    change_id: str
    intent: dict


@dataclass
class PlanInput:
    change_id: str
    intent: dict


@dataclass
class TransitionInput:
    change_id: str
    to_state: str
    actor: str = "temporal"
    reason: str = ""
    evidence: dict = field(default_factory=dict)


@dataclass
class CheckInput:
    change_id: str
    intent: dict
    kind: str = "pre"


@dataclass
class ApplyInput:
    change_id: str
    intent: dict


@dataclass
class CloseInput:
    change_id: str
    intent: dict


@dataclass
class RollbackInput:
    change_id: str
    intent: dict
    evidence: dict = field(default_factory=dict)


class ChangeActivities:
    def __init__(
        self, session_factory, *, servicenow, netbox, awx, policy,
        lock_ttl_seconds: int = 1800, wave_size: int = 2, abort_threshold: int = 1,
        notification: NotificationProtocol | None = None,
        monitoring: MonitoringProtocol | None = None,
        monitoring_silence_minutes: int = 60,
    ) -> None:
        self._sessions = session_factory
        self._snow = servicenow
        self._netbox = netbox
        self._awx = awx
        self._policy = policy
        self._locks = DeviceLockService(session_factory, ttl_seconds=lock_ttl_seconds)
        self._wave_size = wave_size
        self._abort_threshold = abort_threshold
        self._backups: dict[str, dict[str, int]] = {}
        self._notifications = NotificationService(notification, channel="slack") if notification is not None else None
        self._monitoring = monitoring
        self._silence_minutes = monitoring_silence_minutes
        self._silences: dict[str, dict[str, str]] = {}  # ticket -> {device: silence_id}

    async def _silence_monitoring(self, ticket: str, devices: list[str]) -> None:
        if self._monitoring is None:
            return
        silences: dict[str, str] = {}
        for device in devices:
            try:
                silences[device] = await self._monitoring.silence(device, self._silence_minutes)
            except Exception:
                pass  # monitoring silence must not fail a change; it is best-effort
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

    async def _launch_and_poll(self, template: str, extra_vars: dict[str, str], limit: str | None = None) -> dict[str, Any]:
        job = await self._awx.launch_job(template, extra_vars, limit=limit)
        # Fake AWX returns successful immediately; Real AWX polls via poll_job if present.
        poll = getattr(self._awx, "poll_job", None)
        if poll is not None and job.status not in {"successful", "failed", "canceled"}:
            job = await poll(job.id, timeout_seconds=600)
        return {"job_id": job.id, "status": job.status, "ok": job.status == "successful"}

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
            await record_transition(session, change, ChangeState(inp.to_state), actor=inp.actor, reason=inp.reason, evidence=inp.evidence)
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
            res = await self._launch_and_poll("backup", {"ticket": intent.ticket_number, "device": device}, limit=device)
            if not res["ok"]:
                await self._locks.release_all(list(intent.devices), intent.ticket_number)
                return {"ok": False, "failures": [f"backup failed on {device}: {res}"], "evidence": {"backups": backups}}
            backups[device] = int(res["job_id"])
        self._backups[intent.ticket_number] = backups
        # 3. Baseline via AWX pre-check playbook (neighbours, interfaces, config hash).
        baseline_res = await self._launch_and_poll("pre-check", {"ticket": intent.ticket_number}, limit=",".join(intent.devices))
        baseline: dict[str, Any] = {"pre_check_job_id": baseline_res["job_id"]} if baseline_res["ok"] else {}
        # 4. Plugin checks, with backup + baseline evidence.
        ctx = CheckContext(intent=intent, ticket_number=intent.ticket_number, netbox=self._netbox, awx=self._awx, backups=backups, baseline=baseline, locks_held_by=intent.ticket_number)
        # BackupTaken + NeighbourBaseline need backups/baseline; the other pre-checks
        # run regardless. If baseline failed, report it as a check failure.
        results = await CheckEngine().run(pre_checks_for(intent), ctx)
        if not baseline_res["ok"]:
            results.append(type(results[0])(name="neighbour_baseline", status="fail", message="AWX pre-check job failed", evidence=baseline))
        ok = CheckEngine.all_pass(results)
        if not ok:
            await self._locks.release_all(list(intent.devices), intent.ticket_number)
        return {"ok": ok, "failures": [r.message for r in results if not r.ok], "evidence": {"checks": [{ "name": r.name, "status": r.status, "message": r.message } for r in results], "backups": backups}}

    @activity.defn(name="post_check_change")
    async def post_check_change(self, inp: CheckInput) -> dict[str, Any]:
        intent = parse_intent(inp.intent)
        backups = self._backups.get(intent.ticket_number, {})
        ctx = CheckContext(intent=intent, ticket_number=intent.ticket_number, netbox=self._netbox, awx=self._awx, backups=backups, baseline={"dry_run_job_id": 0}, locks_held_by=intent.ticket_number)
        # AWX post-check playbook first (config match, neighbours, diff).
        post_res = await self._launch_and_poll("post-check", {"ticket": intent.ticket_number}, limit=",".join(intent.devices))
        results = await CheckEngine().run(post_checks_for(intent), ctx)
        ok = CheckEngine.all_pass(results) and bool(post_res["ok"])
        failures = [r.message for r in results if not r.ok]
        if not post_res["ok"]:
            failures.append(f"AWX post-check job failed: {post_res}")
        return {"ok": ok, "failures": failures, "evidence": {"checks": [{"name": r.name, "status": r.status, "message": r.message} for r in results], "post_check_job": post_res}}

    @activity.defn(name="apply_change")
    async def apply_change(self, inp: ApplyInput) -> dict[str, Any]:
        intent = parse_intent(inp.intent)
        # Dry-run first, for the whole change. A non-empty unexpected diff fails before any device is touched.
        dry = await self._launch_and_poll("dry-run", {"ticket": intent.ticket_number, "change_type": intent.change_type}, limit=",".join(intent.devices))
        if not dry["ok"]:
            return {"ok": False, "failures": [f"dry-run failed: {dry}"], "evidence": {"dry_run": dry}}
        # Apply in waves. Abort threshold stops the rollout; completed waves are rolled back by rollback_change.
        async def apply_wave(wave: list[str]) -> list[str]:
            res = await self._launch_and_poll("apply", {"ticket": intent.ticket_number, "change_type": intent.change_type}, limit=",".join(wave))
            return [] if res["ok"] else list(wave)

        waves = await run_waves(list(intent.devices), apply_wave, wave_size=self._wave_size, abort_threshold=self._abort_threshold)
        if waves.aborted or waves.failed_devices:
            if self._notifications:
                await self._notifications.change_failed(intent.ticket_number, f"apply failed on {waves.failed_devices}")
            for device in waves.failed_devices:
                record_check_failure("apply")
            return {"ok": False, "failures": [f"apply failed on {waves.failed_devices}, aborted={waves.aborted}"], "evidence": {"waves": waves.completed_waves, "failed": waves.failed_devices, "dry_run": dry}}
        return {"ok": True, "evidence": {"dry_run": dry, "waves": waves.completed_waves}}

    @activity.defn(name="close_change")
    async def close_change(self, inp: CloseInput) -> dict[str, Any]:
        intent = parse_intent(inp.intent)
        evidence: dict[str, Any] = {}
        for name in intent.devices:
            try:
                await self._netbox.update_device_status(name, "active")
                await self._netbox.add_journal_entry(name, "change", f"Change {intent.ticket_number} applied.")
            except Exception as exc:  # noqa: BLE001 — recorded, not fatal to close
                evidence[f"{name}_netbox_error"] = str(exc)
        try:
            await self._snow.close(intent.ticket_number, f"Change {intent.ticket_number} applied and post-checked.")
        except Exception as exc:  # noqa: BLE001
            evidence["servicenow_error"] = str(exc)
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
        backups = self._backups.get(intent.ticket_number, {})
        # Rollback restores the pre-change backup per device, via the AWX rollback playbook.
        # The backup job id is passed as extra_vars so the playbook knows which file to restore.
        res = await self._launch_and_poll("rollback", {"ticket": intent.ticket_number, "backups": str(backups)}, limit=",".join(intent.devices))
        await self._unsilence_monitoring(intent.ticket_number)
        if self._notifications:
            await self._notifications.rolled_back(intent.ticket_number, str(inp.evidence))
        record_change("rolled_back")
        await self._locks.release_all(list(intent.devices), intent.ticket_number)
        self._backups.pop(intent.ticket_number, None)
        return {"ok": bool(res["ok"]), "job_id": res["job_id"], "evidence": inp.evidence}


def all_activities(activities: ChangeActivities) -> list:
    return [
        activities.validate_change, activities.plan_change, activities.transition_change,
        activities.pre_check_change, activities.post_check_change, activities.apply_change,
        activities.close_change, activities.rollback_change,
    ]