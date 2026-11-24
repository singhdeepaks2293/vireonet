from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
from vireonet.checks.templates import CheckTemplates, engine_command_runner
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
from vireonet.rendering import RenderedConfig, Renderer, UnsupportedChange, normalise_platform
from vireonet.safety import DeviceLockService, LockHeldError, run_waves
from vireonet.state_machine import ChangeState, record_transition
from vireonet.validation import validate_intent

# States worth a work note on the ticket.
CHANGE_EVENT_STATES = frozenset(
    {
        "closed",
        "rolled_back",
        "rollback_failed",
        "failed",
        "rejected",
        "pre_check_failed",
        "ci_failed",
    }
)
ITSM_NOTE_STATES = frozenset(
    {
        "planned",
        "ci_failed",
        "approved",
        "pre_checked",
        "pre_check_failed",
        "applied",
        "post_checked",
        "rolled_back",
        "rollback_failed",
        "rejected",
        "reconciliation_pending",
        "failed",
    }
)


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
    # Backup job ids per device, from pre_check_change's result. Carried by
    # the workflow (durable in Temporal history) so post-check and rollback
    # work on any worker, even after a restart.
    backups: dict[str, int] = field(default_factory=dict)


@dataclass
class ApplyInput:
    change_id: str
    intent: dict[str, Any]
    # Planned window end (ISO 8601): no wave starts after it.
    window_end: str = ""


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
    # See CheckInput.backups. Empty falls back to this worker's memory, for
    # workflows started before backups were carried in the input.
    backups: dict[str, int] = field(default_factory=dict)
    # device -> Git commit (main/master) recorded at pre-check: the device's
    # running config before this change. Rollback restores from it.
    git_refs: dict[str, str] = field(default_factory=dict)


@dataclass
class WindowCheckInput:
    change_id: str
    intent: dict[str, Any]
    emergency: bool = False


@dataclass
class AbortInput:
    change_id: str
    intent: dict[str, Any]


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
        check_templates: CheckTemplates | None = None,
        renderer: Renderer | None = None,
        device_repos: Any | None = None,
        rollback_source: str = "git",
        excel_push: Any | None = None,
        itsm: Any | None = None,
        wave_health_checks: bool = True,
        emit: Any = None,
    ) -> None:
        self._emit = emit
        self._wave_health_checks = wave_health_checks
        # The ticket's ITSM tool (ServiceNow, Jira, Freshservice, BMC Helix):
        # a work note at every milestone, and closure. Without one, closure
        # falls back to the ServiceNow adapter.
        self._itsm = itsm
        # Excel config push: write pre-change running configs back to the
        # workbook and attach it to the ticket.
        self._excel_push = excel_push
        # Git device repos (config version control). With rollback_source
        # "git", rollback restores each device's config from the main/master
        # commit recorded at pre-check.
        self._device_repos = device_repos
        self._rollback_source = rollback_source
        self._sessions = session_factory
        # Per-intent pre/post check lists (policies/checks.yaml).
        self._check_templates = check_templates or CheckTemplates.default()
        # Vendor-neutral renderer (built-ins + policies/render_templates.yaml).
        self._renderer = renderer or Renderer()
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
            "stdout": str((result.output or {}).get("stdout", "")),
        }

    async def _platforms(self, intent: Any) -> dict[str, str]:
        """Device -> normalised platform. NetBox is the source of truth; a
        ZTP intent carries the platform of the device being provisioned."""
        out: dict[str, str] = {}
        for device in intent.devices:
            if getattr(intent, "platform", None):
                out[device] = normalise_platform(str(intent.platform))
                continue
            try:
                out[device] = normalise_platform((await self._netbox.get_device(device)).platform)
            except Exception:  # noqa: BLE001 — unknown platform is reported by render
                out[device] = ""
        return out

    async def _render(self, intent: Any) -> dict[str, RenderedConfig]:
        """Rendered config per device. Raises UnsupportedChange (fail closed)."""
        return self._renderer.render_all(intent, await self._platforms(intent))

    @staticmethod
    def _config_vars(rc: RenderedConfig, change_type: str) -> dict[str, str]:
        # The playbooks read these (ansible/playbooks/apply.yml & co).
        return {
            "change_type": change_type,
            "intended_config": rc.text,
            "intended_lines": json.dumps(rc.lines),
            "config_format": rc.format,
            "platform": rc.platform,
        }

    def _step_request(
        self,
        intent: Any,
        device: str,
        step: StepName,
        *,
        dry_run: bool = True,
        extra_vars: dict[str, str] | None = None,
        platform: str | None = None,
    ) -> StepRequest:
        platform = platform if platform is not None else str(getattr(intent, "platform", "") or "")
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
        errors = list(result.errors)
        evidence = dict(result.evidence)
        # Render now so an unsupported (change type, platform) is rejected
        # before CI and approval, not discovered at apply time.
        try:
            rendered = await self._render(intent)
            evidence["platforms"] = {d: rc.platform for d, rc in rendered.items()}
        except UnsupportedChange as exc:
            errors.append(f"render: {exc}")
        return {"ok": result.ok and not errors, "errors": errors, "evidence": evidence}

    @activity.defn(name="plan_change")
    async def plan_change(self, inp: PlanInput) -> dict[str, Any]:
        intent = parse_intent(inp.intent)
        plan = make_plan(intent).to_dict()
        try:
            plan["rendered"] = {d: rc.to_dict() for d, rc in (await self._render(intent)).items()}
        except UnsupportedChange as exc:
            plan["render_errors"] = [str(exc)]
        return plan

    @activity.defn(name="transition_change")
    async def transition_change(self, inp: TransitionInput) -> dict[str, Any]:
        async with self._sessions() as session:
            change = await self._load(session, inp.change_id)
            from_state = str(getattr(change.state, "value", change.state))
            await record_transition(
                session,
                change,
                ChangeState(inp.to_state),
                actor=inp.actor,
                reason=inp.reason,
                evidence=inp.evidence,
            )
            await session.commit()
            ticket = change.ticket_number
        if inp.to_state in ITSM_NOTE_STATES:
            await self._itsm_note(ticket, f"VireoNet: {from_state} -> {inp.to_state}: {inp.reason}")
        if self._emit is not None and inp.to_state in CHANGE_EVENT_STATES:
            # Flow trigger "VireoNet event: change.<state>" (never blocks the change).
            await self._emit(
                {
                    "source": "vireonet",
                    "event": f"change.{inp.to_state}",
                    "change": {
                        "id": inp.change_id,
                        "ticket_number": ticket,
                        "state": inp.to_state,
                        "from_state": from_state,
                        "reason": inp.reason,
                    },
                }
            )
        return {"state": inp.to_state}

    async def _itsm_note(self, ticket: str, text: str) -> None:
        """Best effort: an unreachable ITSM tool never blocks a change."""
        if self._itsm is None:
            return
        try:
            await self._itsm.add_comment(ticket, text)
        except Exception as exc:  # noqa: BLE001
            activity.logger.warning("itsm note failed", extra={"ticket": ticket, "error": str(exc)})

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
        running_configs: dict[str, str] = {}
        platforms = await self._platforms(intent)
        for device in intent.devices:
            res = await self._run_step(
                self._step_request(
                    intent,
                    device,
                    "backup",
                    extra_vars={"platform": platforms.get(device, "")},
                    platform=platforms.get(device, ""),
                )
            )
            if not res["ok"]:
                await self._locks.release_all(list(intent.devices), intent.ticket_number)
                return {
                    "ok": False,
                    "failures": [f"backup failed on {device}: {res}"],
                    "evidence": {"backups": backups},
                }
            backups[device] = int(res["job_id"])
            if intent.change_type == "config_push":
                running_configs[device] = res.get("stdout", "")
        self._backups[intent.ticket_number] = backups
        excel_writeback = await self._excel_writeback(intent, running_configs)
        # 2b. Record each device's current main/master commit: the version
        # rollback restores ("last commit" before this change).
        git_refs, git_notes = await self._record_git_refs(intent)
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
            run_commands=engine_command_runner(self._engine, intent),
        )
        # BackupTaken + NeighbourBaseline need backups/baseline; the other pre-checks
        # run regardless. If baseline failed, report it as a check failure.
        results = await CheckEngine().run(pre_checks_for(intent, self._check_templates), ctx)
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
        failures = [r.message for r in results if not r.ok]
        prepost = await self._prepost(intent, "pre")
        if prepost and not prepost["ok"]:
            ok = False
            failures += prepost["failures"]
        if not ok:
            await self._locks.release_all(list(intent.devices), intent.ticket_number)
        return {
            "ok": ok,
            "failures": failures,
            "evidence": {
                **({"prepost": prepost} if prepost else {}),
                "checks": [
                    {"name": r.name, "status": r.status, "message": r.message} for r in results
                ],
                "backups": backups,
                "git_refs": git_refs,
                "git_notes": git_notes,
                **({"excel_writeback": excel_writeback} if excel_writeback else {}),
            },
        }

    async def _prepost(self, intent: Any, label: str) -> dict[str, Any] | None:
        """The change's Pre/Post check profile (intent.check_profile): a pre
        snapshot at pre-check; at post-check a post snapshot compared with it.
        Any failed test fails that check (post: the change rolls back)."""
        name = getattr(intent, "check_profile", "") or ""
        if not name:
            return None
        from vireonet.prepost.service import PrePostService

        svc = PrePostService(self._sessions, engine=self._engine, netbox=self._netbox)
        pre_id = None
        if label == "post":
            snaps = [
                x
                for x in await svc.list_snapshots(profile=name, ticket=intent.ticket_number)
                if x["label"] == "pre"
            ]
            pre_id = snaps[0]["id"] if snaps else None
        try:
            snap = await svc.capture(
                name,
                list(intent.devices),
                label=label,
                ticket=intent.ticket_number,
                user="vireonet",
                pre_id=pre_id,
            )
        except KeyError:
            return {
                "ok": False,
                "failures": [f"check profile {name!r} does not exist"],
                "profile": name,
            }
        failed = [r for r in snap["results"] if not r["ok"]]
        out: dict[str, Any] = {
            "ok": not failed,
            "profile": name,
            "snapshot_id": snap["id"],
            "pre_snapshot_id": pre_id,
            "tests": len(snap["results"]),
            "failures": [f"{r['device']} {r['name']}: {r['message']}" for r in failed],
        }
        if pre_id:
            cmp = await svc.compare(pre_id, snap["id"])
            out["changed"] = [f"{d['device']}/{d['command']}" for d in cmp["diffs"] if d["changed"]]
        return out

    async def _excel_writeback(self, intent: Any, stdouts: dict[str, str]) -> dict[str, Any]:
        """Config push: put each device's pre-change running config in the
        workbook's backup sheet and attach it to the ticket. Best effort: a
        failure here is recorded, it does not stop the change (the AWX backup
        and Git commit are the restore sources)."""
        if (
            intent.change_type != "config_push"
            or self._excel_push is None
            or not intent.workbook_id
        ):
            return {}
        from vireonet.services.backup import extract_running_config

        running: dict[str, str] = {}
        missing: list[str] = []
        for device, stdout in stdouts.items():
            try:
                running[device] = extract_running_config(stdout)
            except Exception:  # noqa: BLE001 — no markers: note it, keep going
                missing.append(device)
        try:
            result = dict(
                await self._excel_push.write_back(intent.ticket_number, intent.workbook_id, running)
            )
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": str(exc)}
        if missing:
            result["no_running_config"] = missing
        return result

    async def _record_git_refs(self, intent: Any) -> tuple[dict[str, str], list[str]]:
        refs: dict[str, str] = {}
        notes: list[str] = []
        if self._device_repos is None or self._rollback_source != "git":
            return refs, notes
        for device in intent.devices:
            try:
                commit = await self._device_repos.resolve_target(device, "branch")
                refs[device] = commit.sha
            except Exception as exc:  # noqa: BLE001 — no ref: rollback uses the AWX backup
                notes.append(f"{device}: no Git commit recorded ({exc}); rollback uses the backup")
        return refs, notes

    @activity.defn(name="post_check_change")
    async def post_check_change(self, inp: CheckInput) -> dict[str, Any]:
        intent = parse_intent(inp.intent)
        backups = inp.backups or self._backups.get(intent.ticket_number, {})
        ctx = CheckContext(
            intent=intent,
            ticket_number=intent.ticket_number,
            netbox=self._netbox,
            awx=self._awx,
            backups=backups,
            baseline={"dry_run_job_id": 0},
            locks_held_by=intent.ticket_number,
            run_commands=engine_command_runner(self._engine, intent),
        )
        # AWX post-check playbook first (config match, neighbours, diff).
        # Per device, with that device's rendered config: the playbook re-runs
        # it in check mode and fails on any remaining diff (idempotency).
        rendered = await self._render(intent)
        post_jobs = []
        for device, rc in rendered.items():
            post_jobs.append(
                await self._run_step(
                    self._step_request(
                        intent,
                        device,
                        "post_check",
                        extra_vars=self._config_vars(rc, intent.change_type),
                        platform=rc.platform,
                    )
                )
            )
        post_res = {
            "ok": all(j["ok"] for j in post_jobs),
            "job_id": post_jobs[-1]["job_id"] if post_jobs else None,
            "jobs": post_jobs,
        }
        results = await CheckEngine().run(post_checks_for(intent, self._check_templates), ctx)
        ok = CheckEngine.all_pass(results) and bool(post_res["ok"])
        failures = [r.message for r in results if not r.ok]
        if not post_res["ok"]:
            failures.append(f"AWX post-check job failed: {post_res}")
        prepost = await self._prepost(intent, "post")
        if prepost and not prepost["ok"]:
            ok = False
            failures += prepost["failures"]
        return {
            "ok": ok,
            "failures": failures,
            "evidence": {
                **({"prepost": prepost} if prepost else {}),
                "checks": [
                    {"name": r.name, "status": r.status, "message": r.message} for r in results
                ],
                "post_check_job": post_res,
            },
        }

    @activity.defn(name="apply_change")
    async def apply_change(self, inp: ApplyInput) -> dict[str, Any]:
        intent = parse_intent(inp.intent)
        try:
            rendered = await self._render(intent)
        except UnsupportedChange as exc:
            return {"ok": False, "failures": [f"render: {exc}"], "evidence": {}}
        # Dry-run first, every device with its own platform's config. A
        # failed dry-run fails before any device is touched.
        dry_jobs: dict[str, Any] = {}
        for device, rc in rendered.items():
            dry_jobs[device] = await self._run_step(
                self._step_request(
                    intent,
                    device,
                    "dry_run",
                    dry_run=True,
                    extra_vars=self._config_vars(rc, intent.change_type),
                    platform=rc.platform,
                )
            )
        dry = {
            "ok": all(j["ok"] for j in dry_jobs.values()),
            "jobs": dry_jobs,
            "rendered": {d: rc.to_dict() for d, rc in rendered.items()},
        }
        if not dry["ok"]:
            return {
                "ok": False,
                "failures": [
                    f"dry-run failed on {[d for d, j in dry_jobs.items() if not j['ok']]}"
                ],
                "evidence": {"dry_run": dry},
            }

        # Apply in waves. Abort threshold stops the rollout; completed waves are rolled back by rollback_change.
        from vireonet.excel_push.window import parse_when

        window_end = parse_when(inp.window_end)
        window_closed: list[bool] = []

        await self._locks.renew_all(list(intent.devices), intent.ticket_number)
        wave_health: list[dict[str, Any]] = []

        async def apply_wave(wave: list[str]) -> list[str]:
            await self._locks.renew_all(list(intent.devices), intent.ticket_number)
            if window_end is not None and datetime.now(UTC) >= window_end:
                # Never push outside the planned window: stop here; the
                # workflow rolls back the waves already applied.
                window_closed.append(True)
                return list(wave)
            failed: list[str] = []
            for device in wave:
                rc = rendered[device]
                res = await self._run_step(
                    self._step_request(
                        intent,
                        device,
                        "apply",
                        dry_run=False,
                        extra_vars=self._config_vars(rc, intent.change_type),
                        platform=rc.platform,
                    )
                )
                if not res["ok"]:
                    failed.append(device)
            if not failed and self._wave_health_checks:
                # Health gate: this wave's post-checks pass before the next wave.
                health = await self._wave_health(intent, wave)
                wave_health.append(health)
                if not health["ok"]:
                    return list(wave)
            return failed

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
                "failures": [
                    f"planned window ended at {window_end.isoformat()}: no further waves started; rolling back"
                    if window_closed and window_end is not None
                    else (
                        f"wave health check failed on {wave_health[-1]['wave']}: {wave_health[-1]['failures']}"
                        if wave_health and not wave_health[-1]["ok"]
                        else f"apply failed on {waves.failed_devices}, aborted={waves.aborted}"
                    )
                ],
                "evidence": {
                    "waves": waves.completed_waves,
                    "failed": waves.failed_devices,
                    "wave_health": wave_health,
                    "dry_run": dry,
                },
            }
        return {
            "ok": True,
            "evidence": {
                "dry_run": dry,
                "waves": waves.completed_waves,
                "wave_health": wave_health,
            },
        }

    async def _wave_health(self, intent: Any, wave: list[str]) -> dict[str, Any]:
        wave_intent = intent.model_copy(update={"devices": list(wave)})
        ctx = CheckContext(
            intent=wave_intent,
            ticket_number=intent.ticket_number,
            netbox=self._netbox,
            awx=self._awx,
            backups=self._backups.get(intent.ticket_number, {}),
            baseline={"dry_run_job_id": 0},
            locks_held_by=intent.ticket_number,
            run_commands=engine_command_runner(self._engine, wave_intent),
        )
        from vireonet.checks.templates import TemplateCommandCheck

        # Only checks that read the devices themselves: built-ins such as
        # config_matches_intent compare against NetBox, which is updated after
        # the whole rollout, so they belong to the final post-check.
        checks: list[Any] = [
            c
            for c in post_checks_for(wave_intent, self._check_templates)
            if isinstance(c, TemplateCommandCheck)
        ]
        if not checks:
            return {"wave": list(wave), "ok": True, "failures": [], "checks": 0}
        results = await CheckEngine().run(checks, ctx)
        return {
            "checks": len(results),
            "wave": list(wave),
            "ok": CheckEngine.all_pass(results),
            "failures": [f"{r.name}: {r.message}" for r in results if not r.ok],
        }

    @activity.defn(name="policy_window_check")
    async def policy_window_check(self, inp: WindowCheckInput) -> dict[str, Any]:
        """Is it still inside the policy change window? Checked right before
        apply: approval can come long after intake."""
        if inp.emergency:
            return {"ok": True, "reason": "emergency: change window bypassed"}
        denial = self._policy.window_denial()
        await self._locks.renew_all(
            list(parse_intent(inp.intent).devices), parse_intent(inp.intent).ticket_number
        )
        return {"ok": denial is None, "reason": denial or "inside the change window"}

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
            except Exception as exc:  # noqa: BLE001 — recorded, not fatal to close
                evidence[f"{name}_netbox_error"] = str(exc)
        note = f"Change {intent.ticket_number} applied and post-checked."
        try:
            if self._itsm is not None:
                await self._itsm.close(intent.ticket_number, note)
                evidence["itsm_closed"] = getattr(self._itsm, "name", "itsm")
            else:
                await self._snow.close(intent.ticket_number, note)
        except Exception as exc:  # noqa: BLE001
            evidence["itsm_error" if self._itsm is not None else "servicenow_error"] = str(exc)
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
        backups = inp.backups or self._backups.get(intent.ticket_number, {})
        git_refs = dict(inp.git_refs)
        user_rollbacks = {
            d: t for d, t in (getattr(intent, "rollback_configs", None) or {}).items() if t.strip()
        }
        if not backups and not git_refs and not user_rollbacks:
            # Nothing to restore from. Report failure rather than launching a
            # rollback job with no backup and calling it a success.
            await self._unsilence_monitoring(intent.ticket_number)
            if self._notifications:
                await self._notifications.change_failed(
                    intent.ticket_number, "rollback impossible: no pre-change backups recorded"
                )
            record_change("rollback_failed")
            return {
                "ok": False,
                "job_id": None,
                "failures": ["no pre-change backups recorded for this change"],
                "evidence": inp.evidence,
            }
        # Rollback restores the pre-change backup per device, via the AWX rollback playbook.
        # The backup job id is passed as extra_vars so the playbook knows which file to restore.
        # One job per device: each restores with its own platform's module.
        # Source per device: the Git commit recorded at pre-check (config was
        # pushed from Git, so restore the last commit before this change),
        # else the AWX pre-change backup file.
        platforms = await self._platforms(intent)
        jobs = []
        sources: dict[str, dict[str, str]] = {}
        for device in intent.devices:
            extra = {"backups": str(backups), "platform": platforms.get(device, "")}
            ref = git_refs.get(device)
            sources[device] = {"source": "backup"}
            user_rollback = (getattr(intent, "rollback_configs", None) or {}).get(device, "")
            if user_rollback.strip():
                # Excel push with a Rollback config for this device: push
                # exactly that (the requester's own undo), not a full restore.
                extra["restore_lines"] = user_rollback
                extra["rollback_source"] = "excel"
                sources[device] = {"source": "excel"}
            elif ref and self._device_repos is not None:
                try:
                    extra["restore_config"] = await self._device_repos.running_config_at(
                        device, ref
                    )
                    extra["rollback_source"] = "git"
                    extra["git_ref"] = ref
                    sources[device] = {"source": "git", "ref": ref}
                except Exception as exc:  # noqa: BLE001 — fall back to the backup, say so
                    sources[device] = {"source": "backup", "git_error": str(exc)}
            if sources[device]["source"] == "backup" and device not in backups:
                jobs.append(
                    {"ok": False, "job_id": None, "error": f"{device}: no Git commit or backup"}
                )
                continue
            jobs.append(
                await self._run_step(
                    self._step_request(
                        intent,
                        device,
                        "rollback",
                        dry_run=False,
                        extra_vars=extra,
                        platform=platforms.get(device, ""),
                    )
                )
            )
        res = {
            "ok": all(j["ok"] for j in jobs),
            "job_id": jobs[-1]["job_id"] if jobs else None,
            "jobs": jobs,
            "sources": sources,
        }
        await self._unsilence_monitoring(intent.ticket_number)
        if not res["ok"]:
            # Devices may be half-changed. Keep the locks (they expire after
            # the TTL, or an operator releases them from /locks once the
            # devices are checked) so no other change lands on top.
            if self._notifications:
                await self._notifications.change_failed(
                    intent.ticket_number, f"ROLLBACK FAILED, manual action needed: {res}"
                )
            record_change("rollback_failed")
            return {
                "ok": False,
                "job_id": res["job_id"],
                "failures": [f"AWX rollback job failed: {res}"],
                "evidence": inp.evidence,
            }
        if self._notifications:
            await self._notifications.rolled_back(intent.ticket_number, str(inp.evidence))
        record_change("rolled_back")
        await self._locks.release_all(list(intent.devices), intent.ticket_number)
        self._backups.pop(intent.ticket_number, None)
        return {
            "ok": True,
            "job_id": res["job_id"],
            "sources": res["sources"],
            "evidence": inp.evidence,
        }

    @activity.defn(name="abort_change")
    async def abort_change(self, inp: AbortInput) -> dict[str, Any]:
        """Operator abort after pre-check, before apply: nothing was pushed,
        so just undo pre-check's side effects (locks, monitoring silence)."""
        intent = parse_intent(inp.intent)
        await self._unsilence_monitoring(intent.ticket_number)
        await self._locks.release_all(list(intent.devices), intent.ticket_number)
        self._backups.pop(intent.ticket_number, None)
        return {"ok": True}


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
        activities.abort_change,
        activities.policy_window_check,
    ]
