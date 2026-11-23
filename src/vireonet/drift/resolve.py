"""Drift resolution actions (D4).

Four actions, each proposed by an operator and decided by an approver or
admin (the API enforces the roles; this module enforces the rest):

- RollbackAction: restore the baseline config through a normal change
  (ChangeService.create_config_restore + the legal change states
  validated -> planned -> ci_passed -> approved -> pre_checked ->
  applied -> post_checked, driven through the ExecutionEngine steps
  backup / pre-check / dry-run / apply / post-check, with engine
  rollback on apply/post-check failure). Resolved only when a
  re-collect confirms the running config matches the baseline hash.
- MergeAction: the drifted config is the new intent — a per-device vars
  override on a drift/<device>/<record> branch plus a Git MR. NEVER a
  shared template (refused). merge_pending until the MR-merged webhook
  (or POST /drift/{id}/mr-merged) arrives, then the baseline is updated
  from a fresh collect and the record resolves. MR closed unmerged ->
  under_review.
- ExceptionAction: keep the config until an expiry (max
  drift_exception_max_days). Detection ignores that exact diff hash
  until expiry via is_excepted(); expire_exceptions() moves expired
  exceptions back to under_review.
- EmergencyAction: operators mark emergency_accepted with a mandatory
  retrospective deadline (drift_emergency_deadline_hours);
  check_emergency_deadlines() raises severity to critical and notifies
  when the deadline is missed.

Self-approval: whoever made the manual change (record.attributed_actor)
cannot approve their own exception or merge — enforced here by
check_self_approval() and by the API (403). Every action needs a reason
and a ServiceNow reference (validated by the API); every action takes
the device lock; every state move goes through transition_drift (audit)
and records a DriftDecision.

ServiceNow incidents: the ServiceNow adapter has NO incident-creation
method (no create_request / create_incident on ServiceNowProtocol or
ServiceNowAdapter), so ensure_incident() cannot invent one. Above the
severity threshold it adds a work note to the record's linked ticket
when one exists, and otherwise logs + notifies that no incident could
be opened. See docs/DRIFT.md.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vireonet.change_state import ChangeState
from vireonet.diff import config_hash, normalize_config
from vireonet.drift.models import DriftDecision
from vireonet.drift.state_machine import transition_drift
from vireonet.execution.types import DeviceRef, StepRequest
from vireonet.models import Change, DriftRecord
from vireonet.safety import LockHeldError
from vireonet.structlog_config import get_logger

log = get_logger(__name__)

SEVERITY_RANK: dict[str, int] = {"low": 1, "medium": 2, "high": 3, "critical": 4}

# Paths a merge must never touch: shared templates are owned by the
# template repo, not by a per-device drift fix. A diff touching one of
# these is refused, not silently narrowed.
SHARED_TEMPLATE_PREFIXES: tuple[str, ...] = (
    "templates/",
    "netops/templates/",
    "ansible/templates/",
)

# Per-device vars override written by a merge (D4). VarsStore only reads
# site/{site}.yaml, role/{role}.yaml and device-type/{type}.yaml — there
# is no per-device override path in VarsStore yet, so this path is the
# documented convention for drift merges; rendering does not pick it up
# until VarsStore learns it (see docs/DRIFT.md).
DEVICE_OVERRIDE_PATH = "netops/vars/device/{device}.yaml"


class SelfApprovalError(ValueError):
    """The attributed actor tried to decide their own drift."""


class ResolutionError(ValueError):
    """A resolution action could not run (missing baseline, engine, ...)."""


def check_self_approval(record: DriftRecord, username: str) -> None:
    """Raise when the user who made the manual change decides on it."""
    actor = (record.attributed_actor or "").strip()
    if actor and actor == username:
        raise SelfApprovalError(
            f"{username} made this change and cannot approve their own exception, merge or rollback"
        )


def touches_shared_template(diff: str) -> bool:
    """True when a unified diff touches a shared template path."""
    for line in diff.splitlines():
        stripped = line.strip()
        for marker in ("+++ b/", "--- a/", "+++ ", "--- "):
            if stripped.startswith(marker):
                path = stripped[len(marker) :].strip()
                if any(path.startswith(prefix) for prefix in SHARED_TEMPLATE_PREFIXES):
                    return True
        # Plain path mentions in a diff body (e.g. a rendered intent diff).
        if any(prefix in stripped for prefix in SHARED_TEMPLATE_PREFIXES) and stripped.startswith(
            ("+", "-")
        ):
            return True
    return False


def _coerce_id(record_id: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(record_id))
    except (ValueError, AttributeError, TypeError):
        return None


async def _load_record(session: AsyncSession, record_id: str) -> DriftRecord | None:
    pk = _coerce_id(record_id)
    if pk is None:
        return None
    return await session.get(DriftRecord, pk)


async def _record_decision(
    session: AsyncSession,
    *,
    record_id: uuid.UUID,
    action: str,
    actor: str,
    reason: str,
    ticket_ref: str,
    expiry: datetime | None = None,
) -> DriftDecision:
    row = DriftDecision(
        record_id=record_id,
        action=action,
        actor=actor,
        reason=reason,
        ticket_ref=ticket_ref,
        expiry=expiry,
    )
    session.add(row)
    return row


async def _to_under_review(
    session: AsyncSession, record: DriftRecord, actor: str, reason: str
) -> None:
    """detected -> under_review first: no action jumps straight from detected."""
    if str(record.status) == "detected":
        await transition_drift(session, record, "under_review", actor, reason)


async def _notify(
    notifier: Any | None,
    channel: str,
    title: str,
    body: str,
    ticket: str,
) -> None:
    if notifier is None:
        return
    send = getattr(notifier, "send", None)
    if not callable(send):
        return
    try:
        await send(channel, title, body, ticket)
    except Exception as exc:  # noqa: BLE001 - a failed notification must not fail the action
        log.warning("drift notification failed", error=str(exc), title=title)


# ---------------------------------------------------------------------------
# Exceptions / emergencies
# ---------------------------------------------------------------------------


async def is_excepted(
    sessions: async_sessionmaker[AsyncSession], device: str, running_hash: str | None
) -> bool:
    """True when an active exception covers this exact (device, running hash).

    A different running hash is a different diff, so changes beyond the
    approved diff are NOT excepted and create a new record.
    """
    if not running_hash:
        return False
    now = datetime.now(UTC)
    async with sessions() as session:
        rows = (
            await session.execute(
                select(DriftDecision, DriftRecord)
                .join(DriftRecord, DriftDecision.record_id == DriftRecord.id)
                .where(
                    DriftRecord.device == device,
                    DriftRecord.running_hash == running_hash,
                    DriftRecord.status == "exception_active",
                    DriftDecision.action == "exception",
                    DriftDecision.expiry.is_not(None),
                    DriftDecision.expiry > now,
                )
            )
        ).all()
        return len(rows) > 0


async def expire_exceptions(
    sessions: async_sessionmaker[AsyncSession],
    *,
    notifier: Any | None = None,
    channel: str = "slack",
    now: datetime | None = None,
) -> list[str]:
    """Move expired exceptions exception_active -> exception_expired -> under_review.

    Returns the record ids moved. Each move notifies (expiry is an
    escalation: the drift is live again and nobody has re-approved it).
    """
    moment = now or datetime.now(UTC)
    moved: list[str] = []
    async with sessions() as session:
        records = (
            (
                await session.execute(
                    select(DriftRecord).where(DriftRecord.status == "exception_active")
                )
            )
            .scalars()
            .all()
        )
        for record in records:
            decisions = (
                (
                    await session.execute(
                        select(DriftDecision)
                        .where(
                            DriftDecision.record_id == record.id,
                            DriftDecision.action == "exception",
                        )
                        .order_by(DriftDecision.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            latest = decisions[0] if decisions else None
            if latest is None or latest.expiry is None:
                continue
            expiry = latest.expiry
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=UTC)
            if expiry > moment:
                continue
            await transition_drift(
                session, record, "exception_expired", "system", "exception expired"
            )
            await transition_drift(
                session, record, "under_review", "system", "exception expired; drift live again"
            )
            moved.append(str(record.id))
            await session.commit()
            await _notify(
                notifier,
                channel,
                f"Drift exception expired on {record.device}",
                f"Exception for drift {record.id} ({record.kind}, {record.severity}) "
                "expired; the record is under review again.",
                str(record.linked_ticket or ""),
            )
    return moved


async def check_emergency_deadlines(
    sessions: async_sessionmaker[AsyncSession],
    *,
    notifier: Any | None = None,
    channel: str = "slack",
    now: datetime | None = None,
) -> list[str]:
    """Escalate emergency acceptances whose retrospective deadline passed.

    The deadline is the emergency decision's expiry. A missed deadline
    raises severity to "critical" (no new severity level is invented)
    and notifies; the record stays under review for a decision.
    Returns the record ids escalated.
    """
    moment = now or datetime.now(UTC)
    escalated: list[str] = []
    async with sessions() as session:
        records = (
            (
                await session.execute(
                    select(DriftRecord).where(DriftRecord.status.in_(["detected", "under_review"]))
                )
            )
            .scalars()
            .all()
        )
        for record in records:
            decisions = (
                (
                    await session.execute(
                        select(DriftDecision)
                        .where(
                            DriftDecision.record_id == record.id,
                            DriftDecision.action == "emergency_accepted",
                        )
                        .order_by(DriftDecision.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            latest = decisions[0] if decisions else None
            if latest is None or latest.expiry is None:
                continue
            deadline = latest.expiry
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=UTC)
            if deadline > moment or str(record.severity) == "critical":
                continue
            record.severity = "critical"
            session.add(record)
            await session.commit()
            escalated.append(str(record.id))
            await _notify(
                notifier,
                channel,
                f"Emergency drift deadline missed on {record.device}",
                f"Emergency acceptance for drift {record.id} missed its "
                f"retrospective deadline ({deadline.isoformat()}); severity raised to critical.",
                str(record.linked_ticket or ""),
            )
    return escalated


async def ensure_incident(
    record: DriftRecord,
    *,
    servicenow: Any | None = None,
    notifier: Any | None = None,
    channel: str = "slack",
    threshold: str = "high",
) -> str | None:
    """Open/update a ServiceNow incident above the severity threshold.

    Idempotent per record: when record.linked_ticket is set, the existing
    ticket gets a work note (no second incident); when it is empty there
    is no incident-creation method on the ServiceNow adapter, so no
    ticket is invented — the gap is logged and notified instead. Returns
    the ticket updated, or None when no incident could be opened.
    """
    if SEVERITY_RANK.get(str(record.severity), 0) < SEVERITY_RANK.get(threshold, 3):
        return None
    ticket = str(record.linked_ticket or "")
    if not ticket:
        log.warning(
            "drift incident not opened: no ServiceNow incident-creation method exists "
            "and the record has no linked ticket",
            device=str(record.device),
            record_id=str(record.id),
            severity=str(record.severity),
        )
        await _notify(
            notifier,
            channel,
            f"Critical drift on {record.device} needs a ServiceNow incident",
            f"Drift {record.id} ({record.kind}, {record.severity}) is above the "
            "incident threshold but has no linked ServiceNow ticket, and the "
            "ServiceNow adapter has no incident-creation method. Open one manually.",
            "",
        )
        return None
    add_note = getattr(servicenow, "add_work_note", None) if servicenow is not None else None
    if callable(add_note):
        try:
            await add_note(
                ticket,
                f"Drift {record.id} on {record.device} ({record.kind}, {record.severity}) "
                "is above the incident threshold; see the drift record for the diff.",
                f"drift-{record.id}-incident",
            )
        except Exception as exc:  # noqa: BLE001 - incident note failure must not fail the action
            log.warning("drift incident work note failed", error=str(exc), ticket=ticket)
    return ticket


# ---------------------------------------------------------------------------
# Baseline fetch / rollback confirmation
# ---------------------------------------------------------------------------


async def fetch_baseline_content(
    device: str,
    *,
    config_store: Any | None = None,
    device_repos: Any | None = None,
) -> str:
    """Baseline config text for a device.

    ConfigStore.latest first; then the device repo's rendered config
    (get_rendered, getattr — not every repo manager exposes it). Raises
    ResolutionError when neither yields content.
    """
    if config_store is not None:
        latest = getattr(config_store, "latest", None)
        if callable(latest):
            version = await latest(device)
            content = getattr(version, "content", None) if version is not None else None
            if isinstance(content, str) and content.strip():
                return content
    if device_repos is not None:
        get_rendered = getattr(device_repos, "get_rendered", None)
        if callable(get_rendered):
            try:
                content = await get_rendered(device)
            except Exception as exc:
                raise ResolutionError(f"device repo read failed for {device}: {exc}") from exc
            if isinstance(content, str) and content.strip():
                return content
    raise ResolutionError(f"no baseline config found for {device} (config store or device repo)")


async def confirm_rollback(
    device: str,
    baseline_hash: str | None,
    *,
    collector: Callable[[str], Awaitable[str]],
) -> bool:
    """Re-collect the running config and compare its hash to the baseline.

    True only when the hashes match. A collection failure is False (not
    confirmed), never an exception that would mark the record resolved.
    """
    if baseline_hash is None:
        return False
    try:
        running = await collector(device)
    except Exception as exc:  # noqa: BLE001 - unconfirmed is not resolved
        log.warning("rollback confirm collect failed", device=device, error=str(exc))
        return False
    return config_hash(normalize_config(running)) == baseline_hash


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


class RollbackAction:
    """Restore the baseline config through a normal change (D4).

    Idempotency: a record already in "reverting" returns the in-flight
    rollback (same change ticket) instead of starting a second change.
    A retry after a failed rollback gets a new change ticket
    (DRIFT-RB-<short>-2, -3, ...) because the failed change row is
    terminal and cannot be re-driven.
    """

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        change_service: Any | None = None,
        engine: Any | None = None,
        config_store: Any | None = None,
        device_repos: Any | None = None,
        locks: Any | None = None,
        collector: Callable[[str], Awaitable[str]] | None = None,
        notifier: Any | None = None,
        channel: str = "slack",
        servicenow: Any | None = None,
    ) -> None:
        self._sessions = sessions
        self._change_service = change_service
        self._engine = engine
        self._config_store = config_store
        self._device_repos = device_repos
        self._locks = locks
        self._collector = collector
        self._notifier = notifier
        self._channel = channel
        self._servicenow = servicenow

    async def _rollback_ticket(self, record: DriftRecord) -> str:
        base = f"DRIFT-RB-{str(record.id).replace('-', '')[:8]}"
        async with self._sessions() as session:
            rows = (
                (await session.execute(select(Change).where(Change.ticket_number.like(f"{base}%"))))
                .scalars()
                .all()
            )
            count = len(rows)
        return base if count == 0 else f"{base}-{count + 1}"

    async def execute(
        self, record_id: str, *, actor: str, reason: str, ticket_ref: str
    ) -> dict[str, Any]:
        if self._engine is None:
            raise ResolutionError("rollback needs an execution engine")
        async with self._sessions() as session:
            record = await _load_record(session, record_id)
            if record is None:
                raise ResolutionError(f"drift record {record_id} not found")
            check_self_approval(record, actor)
            if str(record.status) == "reverting":
                return {
                    "status": "already_reverting",
                    "record_id": str(record.id),
                    "change_ticket": str(record.linked_ticket or ""),
                }
            if str(record.status) not in {"detected", "under_review"}:
                raise ResolutionError(
                    f"rollback is not legal from {record.status} "
                    "(record must be detected or under_review)"
                )
            device = str(record.device)
            baseline_hash = str(record.baseline_hash) if record.baseline_hash else None
        baseline = await fetch_baseline_content(
            device, config_store=self._config_store, device_repos=self._device_repos
        )
        ticket = await self._rollback_ticket(record)
        if self._locks is not None:
            try:
                await self._locks.acquire(device, ticket, holder=actor)
            except LockHeldError:
                raise
        change_id: str | None = None
        try:
            async with self._sessions() as session:
                record = await _load_record(session, record_id)
                if record is None:  # pragma: no cover - deleted mid-flight
                    raise ResolutionError(f"drift record {record_id} not found")
                await _to_under_review(session, record, actor, "rollback proposed")
                await _record_decision(
                    session,
                    record_id=record.id,
                    action="rollback",
                    actor=actor,
                    reason=reason,
                    ticket_ref=ticket_ref,
                )
                record.linked_ticket = ticket
                await transition_drift(session, record, "reverting", actor, reason)
                await session.commit()
            if self._change_service is not None:
                change = await self._change_service.create_config_restore(
                    device=device,
                    ticket=ticket,
                    actor=actor,
                    baseline_ref=str(record.baseline_ref or ""),
                )
                change_id = str(change.id)
                await self._change_service.transition_change(
                    change_id,
                    ChangeState.PLANNED,
                    actor=actor,
                    reason="rollback planned: restore baseline",
                )
                await self._change_service.transition_change(
                    change_id,
                    ChangeState.CI_PASSED,
                    actor=actor,
                    reason="CI not applicable: restoring already-approved baseline",
                    evidence={"ci": "not_applicable_baseline_restore"},
                )
                await self._change_service.transition_change(
                    change_id,
                    ChangeState.APPROVED,
                    actor=actor,
                    reason=f"rollback approved: {reason}",
                )
            outcome = await self._run_engine_steps(device, ticket, baseline, actor, change_id)
            if not outcome["ok"]:
                await self._fail(record_id, actor, outcome, change_id)
                return {
                    "status": "failed",
                    "record_id": str(record.id),
                    "stage": outcome["stage"],
                    "change_ticket": ticket,
                    "change_id": change_id,
                }
            confirmed = False
            if self._collector is not None:
                confirmed = await confirm_rollback(device, baseline_hash, collector=self._collector)
            async with self._sessions() as session:
                record = await _load_record(session, record_id)
                if record is None:  # pragma: no cover - deleted mid-flight
                    raise ResolutionError(f"drift record {record_id} not found")
                if confirmed:
                    await transition_drift(
                        session,
                        record,
                        "resolved",
                        actor,
                        "rollback confirmed: running == baseline",
                    )
                    await session.commit()
                    await _notify(
                        self._notifier,
                        self._channel,
                        f"Drift resolved on {device}",
                        f"Rollback of drift {record.id} confirmed: running config matches baseline.",
                        ticket,
                    )
                    if record.linked_ticket:
                        await self._close_incident_note(record)
                    return {
                        "status": "resolved",
                        "record_id": str(record.id),
                        "change_ticket": ticket,
                        "change_id": change_id,
                    }
                await transition_drift(
                    session,
                    record,
                    "under_review",
                    actor,
                    "rollback applied but re-collect did not match baseline",
                )
                await session.commit()
                return {
                    "status": "unconfirmed",
                    "record_id": str(record.id),
                    "change_ticket": ticket,
                    "change_id": change_id,
                }
        finally:
            if self._locks is not None:
                with suppress(Exception):  # noqa: BLE001 - lock release is best-effort here
                    await self._locks.release(device, ticket)

    async def _run_engine_steps(
        self, device: str, ticket: str, baseline: str, actor: str, change_id: str | None
    ) -> dict[str, Any]:
        """backup -> pre-check -> dry-run -> apply -> post-check.

        Returns {"ok": True} or {"ok": False, "stage": <step>}. A failed
        apply/post-check triggers engine.rollback() (auto-rollback); a
        failed earlier step does not touch the device, so no rollback.
        """
        assert self._engine is not None
        steps: list[tuple[str, bool]] = [
            ("backup", True),
            ("pre_check", True),
            ("dry_run", True),
            ("apply", False),
            ("post_check", True),
        ]
        for step, dry in steps:
            req = StepRequest(
                ticket_number=ticket,
                device=DeviceRef(name=device),
                step=step,  # type: ignore[arg-type]
                change_type="config_restore",
                rendered_config=baseline,
                dry_run=dry,
                correlation_id=ticket,
            )
            try:
                result = await getattr(self._engine, step)(req)
            except Exception as exc:  # noqa: BLE001 - engine failure is a step failure
                log.warning("rollback step raised", step=step, error=str(exc))
                return {"ok": False, "stage": step}
            if not result.ok:
                if step in {"apply", "post_check"}:
                    try:
                        await self._engine.rollback(req.model_copy(update={"step": "rollback"}))
                    except Exception as exc:  # noqa: BLE001 - rollback attempt is best-effort
                        log.warning("rollback auto-rollback failed", error=str(exc))
                return {"ok": False, "stage": step}
            if self._change_service is not None and change_id is not None:
                target = {
                    "pre_check": ChangeState.PRE_CHECKED,
                    "apply": ChangeState.APPLIED,
                    "post_check": ChangeState.POST_CHECKED,
                }.get(step)
                if target is not None:
                    await self._change_service.transition_change(
                        change_id,
                        target,
                        actor=actor,
                        reason=f"rollback {step} passed",
                    )
        return {"ok": True, "stage": ""}

    async def _fail(
        self,
        record_id: str,
        actor: str,
        outcome: dict[str, Any],
        change_id: str | None,
    ) -> None:
        """Post-check/apply failure: auto-rollback ran; record stays open."""
        if self._change_service is not None and change_id is not None:
            stage = str(outcome["stage"])
            target = (
                ChangeState.ROLLED_BACK if stage in {"apply", "post_check"} else ChangeState.FAILED
            )
            try:
                await self._change_service.transition_change(
                    change_id,
                    target,
                    actor=actor,
                    reason=f"rollback {stage} failed; auto-rollback applied"
                    if target is ChangeState.ROLLED_BACK
                    else f"rollback {stage} failed",
                )
            except ValueError:
                log.warning("rollback change transition failed", change_id=change_id)
        async with self._sessions() as session:
            record = await _load_record(session, record_id)
            if record is None:
                return
            await transition_drift(
                session,
                record,
                "under_review",
                actor,
                f"rollback failed at {outcome['stage']}; auto-rollback applied, record stays open",
            )
            await session.commit()

    async def _close_incident_note(self, record: DriftRecord) -> None:
        add_note = getattr(self._servicenow, "add_work_note", None) if self._servicenow else None
        if callable(add_note) and record.linked_ticket:
            try:
                await add_note(
                    str(record.linked_ticket),
                    f"Drift {record.id} on {record.device} resolved by rollback.",
                    f"drift-{record.id}-resolved",
                )
            except Exception as exc:  # noqa: BLE001 - note failure must not fail resolve
                log.warning("drift resolve work note failed", error=str(exc))


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


class MergeAction:
    """Merge the drifted config into Git as a per-device vars override (D4)."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        git: Any | None = None,
        config_store: Any | None = None,
        device_repos: Any | None = None,
        notifier: Any | None = None,
        channel: str = "slack",
        target_branch: str = "main",
    ) -> None:
        self._sessions = sessions
        self._git = git
        self._config_store = config_store
        self._device_repos = device_repos
        self._notifier = notifier
        self._channel = channel
        self._target_branch = target_branch

    async def execute(
        self, record_id: str, *, actor: str, reason: str, ticket_ref: str
    ) -> dict[str, Any]:
        if self._git is None:
            raise ResolutionError("merge needs a Git provider")
        async with self._sessions() as session:
            record = await _load_record(session, record_id)
            if record is None:
                raise ResolutionError(f"drift record {record_id} not found")
            check_self_approval(record, actor)
            if str(record.status) not in {"detected", "under_review"}:
                raise ResolutionError(
                    f"merge is not legal from {record.status} "
                    "(record must be detected or under_review)"
                )
            if touches_shared_template(str(record.diff or "")):
                raise ResolutionError(
                    "merge refused: the diff touches a shared template path "
                    "(templates are owned by the template repo, not by a drift fix)"
                )
            device = str(record.device)
            short = str(record.id).replace("-", "")[:8]
        branch = f"drift/{device}/{short}"
        await self._git.create_branch(branch, self._target_branch)
        path = DEVICE_OVERRIDE_PATH.format(device=device)
        content = (
            f"# Drift override for {device} (record {record_id})\n"
            f"# Approved by {actor}; ServiceNow ref: {ticket_ref}\n"
            f"# Reason: {reason}\n"
            "drift_override:\n"
            f"  record_id: {record_id}\n"
            f"  device: {device}\n"
            "  diff: |\n" + "".join(f"    {line}\n" for line in str(record.diff or "").splitlines())
        )
        await self._git.commit_file(
            branch, path, content, f"drift: per-device override for {device}"
        )
        description = (
            f"Drift merge for {device} (record {record_id}).\n\n"
            f"Kind: {record.kind}; severity: {record.severity}.\n"
            f"ServiceNow ref: {ticket_ref}.\n"
            f"Changed by: {record.attributed_actor or 'unknown'}"
            + (f" at {record.attributed_at.isoformat()}" if record.attributed_at else "")
            + ".\n\nDiff:\n```\n"
            f"{record.diff or ''}\n```\n\n"
            f"Drift record: {record_id}."
        )
        mr = await self._git.open_mr(
            source_branch=branch,
            target_branch=self._target_branch,
            title=f"Drift merge {device} {short}",
            description=description,
        )
        async with self._sessions() as session:
            record = await _load_record(session, record_id)
            if record is None:  # pragma: no cover - deleted mid-flight
                raise ResolutionError(f"drift record {record_id} not found")
            await _to_under_review(session, record, actor, "merge proposed")
            await _record_decision(
                session,
                record_id=record.id,
                action="merge",
                actor=actor,
                reason=reason,
                ticket_ref=ticket_ref,
            )
            await transition_drift(session, record, "merge_pending", actor, reason)
            await session.commit()
        return {
            "status": "merge_pending",
            "record_id": str(record.id),
            "branch": branch,
            "mr_id": getattr(mr, "id", None),
            "mr_url": getattr(mr, "url", ""),
        }


async def handle_mr_merged(
    sessions: async_sessionmaker[AsyncSession],
    *,
    branch: str,
    merged: bool,
    config_store: Any | None = None,
    device_repos: Any | None = None,
    collector: Callable[[str], Awaitable[str]] | None = None,
    notifier: Any | None = None,
    channel: str = "slack",
    actor: str = "git-webhook",
) -> dict[str, Any] | None:
    """MR signal for a drift/<device>/<short> branch.

    merged=True: re-collect the running config and commit it as the new
    baseline (commit_baseline), then resolve. Without a collector the
    baseline cannot be updated from the merged MR alone, so the record
    resolves with a warning recorded in the audit reason and the next
    scan re-judges the baseline. merged=False (closed unmerged): back
    to under_review. Returns None when the branch is not a drift branch
    or no merge_pending record matches.
    """
    parts = branch.split("/")
    if len(parts) != 3 or parts[0] != "drift":
        return None
    short = parts[2]
    async with sessions() as session:
        records = (
            (
                await session.execute(
                    select(DriftRecord).where(DriftRecord.status == "merge_pending")
                )
            )
            .scalars()
            .all()
        )
        record = next((r for r in records if str(r.id).replace("-", "").startswith(short)), None)
        if record is None:
            return None
        record_id = str(record.id)
        device = str(record.device)
        if not merged:
            await transition_drift(
                session, record, "under_review", actor, "merge request closed unmerged"
            )
            await session.commit()
            await _notify(
                notifier,
                channel,
                f"Drift MR closed unmerged on {device}",
                f"Merge request for drift {record_id} was closed without merging; "
                "the record is under review again.",
                str(record.linked_ticket or ""),
            )
            return {"status": "under_review", "record_id": record_id}
        baseline_note = ""
        if collector is not None and config_store is not None and device_repos is not None:
            try:
                from vireonet.drift.baseline import commit_baseline

                running = await collector(device)
                sha = await commit_baseline(
                    device=device,
                    running_config=running,
                    ticket=str(record.linked_ticket or f"drift-{short}"),
                    config_store=config_store,
                    device_repos=device_repos,
                    message=f"drift merge {short}: baseline for {device}",
                )
                baseline_note = f" baseline updated ({sha})."
            except Exception as exc:  # noqa: BLE001 - baseline failure is recorded, not fatal
                baseline_note = f" baseline update failed: {exc}."
                log.warning("drift merge baseline update failed", error=str(exc))
        else:
            baseline_note = (
                " baseline update skipped: no collector/config store wired; "
                "the next scan re-judges the baseline."
            )
        await transition_drift(
            session, record, "resolved", actor, f"merge request merged.{baseline_note}"
        )
        await session.commit()
        await _notify(
            notifier,
            channel,
            f"Drift resolved on {device}",
            f"Merge request for drift {record_id} merged.{baseline_note}",
            str(record.linked_ticket or ""),
        )
        return {"status": "resolved", "record_id": record_id}


# ---------------------------------------------------------------------------
# Exception / emergency actions
# ---------------------------------------------------------------------------


class ExceptionAction:
    """Keep the running config until an expiry (D4)."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        notifier: Any | None = None,
        channel: str = "slack",
        max_days: int = 30,
    ) -> None:
        self._sessions = sessions
        self._notifier = notifier
        self._channel = channel
        self._max_days = max_days

    async def execute(
        self,
        record_id: str,
        *,
        actor: str,
        reason: str,
        ticket_ref: str,
        owner: str,
        expiry: datetime,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        moment = now or datetime.now(UTC)
        if not owner.strip():
            raise ResolutionError("an exception needs an owner")
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        if expiry <= moment:
            raise ResolutionError("exception expiry must be in the future")
        if expiry > moment + timedelta(days=self._max_days):
            raise ResolutionError(
                f"exception expiry exceeds the policy maximum of {self._max_days} days"
            )
        async with self._sessions() as session:
            record = await _load_record(session, record_id)
            if record is None:
                raise ResolutionError(f"drift record {record_id} not found")
            check_self_approval(record, actor)
            if str(record.status) not in {"detected", "under_review"}:
                raise ResolutionError(
                    f"exception is not legal from {record.status} "
                    "(record must be detected or under_review)"
                )
            await _to_under_review(session, record, actor, "exception proposed")
            await _record_decision(
                session,
                record_id=record.id,
                action="exception",
                actor=actor,
                reason=f"{reason} (owner: {owner})",
                ticket_ref=ticket_ref,
                expiry=expiry,
            )
            await transition_drift(
                session,
                record,
                "exception_active",
                actor,
                f"exception granted until {expiry.isoformat()} (owner: {owner})",
            )
            await session.commit()
            return {
                "status": "exception_active",
                "record_id": str(record.id),
                "expiry": expiry.isoformat(),
                "owner": owner,
            }


class EmergencyAction:
    """Mark a drift emergency-accepted with a retrospective deadline (D4)."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        notifier: Any | None = None,
        channel: str = "slack",
        deadline_hours: int = 24,
    ) -> None:
        self._sessions = sessions
        self._notifier = notifier
        self._channel = channel
        self._deadline_hours = deadline_hours

    async def execute(
        self,
        record_id: str,
        *,
        actor: str,
        reason: str,
        ticket_ref: str,
        deadline_hours: int | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        moment = now or datetime.now(UTC)
        hours = deadline_hours if deadline_hours is not None else self._deadline_hours
        if hours < 1 or hours > 168:
            raise ResolutionError("emergency deadline must be 1-168 hours")
        deadline = moment + timedelta(hours=hours)
        async with self._sessions() as session:
            record = await _load_record(session, record_id)
            if record is None:
                raise ResolutionError(f"drift record {record_id} not found")
            if str(record.status) not in {"detected", "under_review"}:
                raise ResolutionError(
                    f"emergency accept is not legal from {record.status} "
                    "(record must be detected or under_review)"
                )
            await _to_under_review(session, record, actor, "emergency accepted")
            await _record_decision(
                session,
                record_id=record.id,
                action="emergency_accepted",
                actor=actor,
                reason=reason,
                ticket_ref=ticket_ref,
                expiry=deadline,
            )
            await session.commit()
            return {
                "status": str(record.status),
                "record_id": str(record.id),
                "deadline": deadline.isoformat(),
            }


def record_short_id(record_id: str) -> str:
    """First 8 hex chars of a record id (used in branch and ticket names)."""
    return str(record_id).replace("-", "")[:8]
