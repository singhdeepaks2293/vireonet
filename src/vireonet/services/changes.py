from __future__ import annotations

import builtins
import uuid
from datetime import datetime
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vireonet.adapters.protocols import AwxProtocol, NetBoxProtocol, ServiceNowProtocol
from vireonet.config_store.store import ConfigStore
from vireonet.execution.engine import ExecutionEngine
from vireonet.intent import ChangeIntent, parse_intent
from vireonet.models import AuditLog, Change, DriftRecord
from vireonet.policy import PolicyEngine
from vireonet.services.backup import BackupService
from vireonet.safety import DeviceLockService
from vireonet.state_machine import ChangeState, record_transition
from vireonet.validation import validate_intent


def _coerce_change_id(change_id: str) -> uuid.UUID | None:
    """API/Temporal pass ids as strings; the PK is PGUUID, whose bind
    processor needs a UUID object (a str raises AttributeError: 'str' has
    no attribute 'hex' on SQLite, and fails on Postgres too)."""
    try:
        return uuid.UUID(str(change_id))
    except (ValueError, AttributeError, TypeError):
        return None


def _find_in_evidence(evidence: Any, key: str) -> Any:
    """First value stored under ``key`` anywhere nested in audit evidence."""
    if isinstance(evidence, dict):
        if key in evidence:
            return evidence[key]
        for value in evidence.values():
            found = _find_in_evidence(value, key)
            if found is not None:
                return found
    elif isinstance(evidence, list):
        for value in evidence:
            found = _find_in_evidence(value, key)
            if found is not None:
                return found
    return None


def _normalise_declared(raw: Any) -> list[dict[str, Any]]:
    """Declared changes as stored in evidence, coerced to the documented
    shape; anything malformed is dropped, never invented."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "object_type": str(item.get("object_type", "")),
                "identifier": str(item.get("identifier", "")),
                "field": str(item.get("field", "")),
                "old_value": str(item.get("old_value", "")),
                "new_value": str(item.get("new_value", "")),
            }
        )
    return out


class DuplicateTicketError(ValueError):
    pass


def intent_from_servicenow_variables(
    ticket: str, variables: dict[str, str], requested_by: str
) -> dict[str, Any]:
    """Catalog-variable -> intent mapping. ONE place, per-catalog.

    Expected variables (catalog admin defines them): change_type, site,
    devices (comma-separated), plus per-type fields e.g. vlan_id, vlan_name.
    Unknown catalogs must add their mapping here, not in the API.
    """
    devices = [d.strip() for d in (variables.get("devices") or "").split(",") if d.strip()]
    base: dict[str, Any] = {
        "ticket_number": ticket,
        "requested_by": requested_by,
        "site": variables.get("site", ""),
        "devices": devices,
        "change_type": variables.get("change_type", "vlan_add"),
    }
    for key in (
        "vlan_id",
        "vlan_name",
        "acl_name",
        "action",
        "protocol",
        "source",
        "destination",
        "destination_port",
        "sequence",
        "prefix",
        "next_hop",
        "vrf",
        "interface",
        "description",
        "enabled",
        "mtu",
        "serial",
        "model",
        "platform",
        "mac_address",
    ):
        if key in variables and variables[key] != "":
            base[key] = variables[key]
    if "vlan_id" in base:
        base["vlan_id"] = int(base["vlan_id"])
    if "destination_port" in base and base["destination_port"] is not None:
        base["destination_port"] = int(base["destination_port"])
    if "sequence" in base:
        base["sequence"] = int(base["sequence"])
    if "enabled" in base:
        base["enabled"] = str(base["enabled"]).lower() in {"true", "yes", "1"}
    if "mtu" in base:
        base["mtu"] = int(base["mtu"])
    return base


class ChangeService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        servicenow: ServiceNowProtocol,
        netbox: NetBoxProtocol,
        policy: PolicyEngine,
        config_store: ConfigStore | None = None,
        awx: AwxProtocol | None = None,
        engine: ExecutionEngine | None = None,
    ) -> None:
        self._sessions = session_factory
        self._snow = servicenow
        self._netbox = netbox
        self._policy = policy
        self._config_store = config_store
        self._awx = awx
        self._engine = engine

    async def _existing(self, session: AsyncSession, ticket: str) -> Change | None:
        return (
            await session.execute(select(Change).where(Change.ticket_number == ticket))
        ).scalar_one_or_none()

    async def create_from_intent(
        self,
        data: dict[str, Any],
        *,
        actor: str,
        emergency: bool = False,
        now: datetime | None = None,
    ) -> tuple[Change, bool]:
        """Parse, persist as received, validate+policy, move to validated/rejected.

        Returns (change, created). Duplicate ticket returns the existing row
        with created=False — duplicate webhooks are harmless.
        """
        intent: ChangeIntent = parse_intent(data)
        async with self._sessions() as session:
            existing = await self._existing(session, intent.ticket_number)
            if existing is not None:
                return existing, False
            change = Change(
                ticket_number=intent.ticket_number,
                change_type=intent.change_type,
                state=ChangeState.RECEIVED,
                requester=intent.requested_by,
                intent=intent.model_dump(mode="json"),
                devices=list(intent.devices),
            )
            session.add(change)
            await session.flush()
            result = await validate_intent(
                intent, self._netbox, self._policy, now=now, emergency=emergency
            )
            if result.ok:
                await record_transition(
                    session,
                    change,
                    ChangeState.VALIDATED,
                    actor=actor,
                    reason="NetBox + policy passed",
                    evidence=result.evidence,
                )
            else:
                await record_transition(
                    session,
                    change,
                    ChangeState.REJECTED,
                    actor=actor,
                    reason="; ".join(result.errors),
                    evidence=result.evidence,
                )
            await session.commit()
            await session.refresh(change)
            return change, True

    async def create_from_servicenow(
        self, ticket: str, *, actor: str, sys_id: str | None = None
    ) -> tuple[Change, bool]:
        req = (
            await self._snow.get_request_by_sys_id(sys_id)
            if sys_id
            else await self._snow.get_request(ticket)
        )
        try:
            data = intent_from_servicenow_variables(req.ticket, req.variables, req.requested_by)
            return await self.create_from_intent(data, actor=actor)
        except (PydanticValidationError, ValueError, KeyError) as exc:
            # Unparseable catalog payload: still persist it as rejected, with evidence.
            async with self._sessions() as session:
                existing = await self._existing(session, req.ticket)
                if existing is not None:
                    return existing, False
                change = Change(
                    ticket_number=req.ticket,
                    change_type="unknown",
                    state=ChangeState.RECEIVED,
                    requester=req.requested_by,
                    intent={"variables": req.variables},
                    devices=[],
                )
                session.add(change)
                await session.flush()
                await record_transition(
                    session,
                    change,
                    ChangeState.REJECTED,
                    actor=actor,
                    reason=f"unparseable ServiceNow variables: {exc}",
                    evidence={"variables": req.variables},
                )
                await session.commit()
                await session.refresh(change)
                return change, True

    async def create_config_restore(
        self,
        *,
        device: str,
        ticket: str,
        actor: str,
        baseline_ref: str | None = None,
    ) -> Change:
        """Create the Change row for a drift rollback (D4).

        A rollback restores already-approved baseline config, so it is not
        a catalog intent (intent.py only knows vlan/acl/route/interface/ztp)
        and cannot go through create_from_intent. This row carries
        change_type "config_restore" with the baseline reference in the
        intent, and is validated on creation: the only validation a restore
        needs is that a baseline exists, which the caller (drift.resolve)
        has already established by fetching the baseline content. The row
        then moves through the normal states (planned -> ci_passed ->
        approved -> pre_checked -> applied -> post_checked) via
        transition_change; CI is recorded as not-applicable because there
        is no Git MR for restoring already-approved config.
        """
        async with self._sessions() as session:
            existing = await self._existing(session, ticket)
            if existing is not None:
                return existing
            change = Change(
                ticket_number=ticket,
                change_type="config_restore",
                state=ChangeState.RECEIVED,
                requester=actor,
                intent={
                    "change_type": "config_restore",
                    "devices": [device],
                    "site": "",
                    "ticket_number": ticket,
                    "requested_by": actor,
                    "baseline_ref": baseline_ref or "",
                },
                devices=[device],
            )
            session.add(change)
            await session.flush()
            await record_transition(
                session,
                change,
                ChangeState.VALIDATED,
                actor=actor,
                reason="drift rollback: baseline restore, baseline exists",
                evidence={"baseline_ref": baseline_ref or ""},
            )
            await session.commit()
            await session.refresh(change)
            return change

    async def transition_change(
        self,
        change_id: str,
        to_state: ChangeState,
        *,
        actor: str,
        reason: str,
        evidence: dict[str, Any] | None = None,
    ) -> Change | None:
        """Move a change through one legal transition (D4 rollback driver).

        Raises ValueError on an illegal transition; returns None when the
        change does not exist.
        """
        pk = _coerce_change_id(change_id)
        if pk is None:
            return None
        async with self._sessions() as session:
            change = await session.get(Change, pk)
            if change is None:
                return None
            await record_transition(
                session,
                change,
                to_state,
                actor=actor,
                reason=reason,
                evidence=evidence or {},
            )
            await session.commit()
            await session.refresh(change)
            return change

    async def get(self, change_id: str) -> Change | None:
        pk = _coerce_change_id(change_id)
        if pk is None:
            return None
        async with self._sessions() as session:
            return await session.get(Change, pk)

    async def list(
        self,
        *,
        state: str | None = None,
        change_type: str | None = None,
        device: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "created_at",
        sort_dir: str = "desc",
    ) -> tuple[list[Change], int]:
        """Server-side filtered, sorted, paginated list. Device uses JSON containment."""
        async with self._sessions() as session:
            query = select(Change)
            count_query = select(func.count()).select_from(Change)
            if state:
                query = query.where(Change.state == state)
                count_query = count_query.where(Change.state == state)
            if change_type:
                query = query.where(Change.change_type == change_type)
                count_query = count_query.where(Change.change_type == change_type)
            if device:
                # JSON containment: devices is a JSON array. Works on Postgres
                # JSONB and on SQLite JSON1. Do not use LIKE on the JSON text.
                query = query.where(Change.devices.contains([device]))
                count_query = count_query.where(Change.devices.contains([device]))
            if date_from:
                query = query.where(Change.created_at >= date_from)
                count_query = count_query.where(Change.created_at >= date_from)
            if date_to:
                query = query.where(Change.created_at <= date_to)
                count_query = count_query.where(Change.created_at <= date_to)
            total = int((await session.execute(count_query)).scalar_one())
            column = {
                "created_at": Change.created_at,
                "ticket_number": Change.ticket_number,
                "change_type": Change.change_type,
                "state": Change.state,
            }.get(sort_by, Change.created_at)
            query = query.order_by(column.desc() if sort_dir == "desc" else column.asc())
            query = query.offset((page - 1) * page_size).limit(page_size)
            items = list((await session.execute(query)).scalars().all())
            return items, total

    async def get_detail(self, change_id: str) -> dict[str, Any] | None:
        pk = _coerce_change_id(change_id)
        if pk is None:
            return None
        async with self._sessions() as session:
            change = await session.get(Change, pk)
            if change is None:
                return None
            rows = (
                (
                    await session.execute(
                        select(AuditLog)
                        .where(AuditLog.change_id == change.id)
                        .order_by(AuditLog.created_at)
                    )
                )
                .scalars()
                .all()
            )
            transitions = [
                {
                    "from_state": r.from_state,
                    "to_state": r.to_state,
                    "actor": r.actor,
                    "reason": r.reason,
                    "evidence": r.evidence,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]
            # Diff: stored running-vs-candidate diffs (F1), grouped by device.
            # When no config store is wired in, or no backup was collected for
            # this change, there is no diff to show — never fall back to
            # rendering the intent as additions.
            diff: list[dict[str, Any]] = []
            diff_summary: list[dict[str, Any]] = []
            if self._config_store is not None:
                for row in await self._config_store.diffs_for_change(change.id):
                    diff.append(
                        {
                            "old_line": None,
                            "new_line": None,
                            "type": "context",
                            "text": f"# {row.device}  (+{row.added} -{row.removed})",
                        }
                    )
                    diff.extend(list(row.diff_lines or []))
                    diff_summary.append(
                        {
                            "device": row.device,
                            "added": row.added,
                            "removed": row.removed,
                            "is_empty": row.is_empty,
                            "running_version_id": str(row.running_version_id)
                            if row.running_version_id
                            else None,
                        }
                    )
            # Checks / Batfish / AWX are read from audit evidence.
            checks: list[dict[str, Any]] = []
            batfish: dict[str, Any] = {}
            awx_jobs: list[dict[str, Any]] = []
            declared_changes: list[dict[str, Any]] = []
            script_name: str | None = None
            script_sha: str | None = None
            # The change record stores no job_type column; the intent may
            # carry one, otherwise the default is "playbook" (see below).
            job_type = "playbook"
            intent_job_type = (change.intent or {}).get("job_type")
            if intent_job_type in {"playbook", "python"}:
                job_type = str(intent_job_type)
            for r in rows:
                ev = r.evidence or {}
                if isinstance(ev.get("checks"), list):
                    checks.extend(ev["checks"])
                if isinstance(ev.get("gate"), dict):
                    batfish = ev["gate"]
                if ev.get("job_id") is not None:
                    awx_jobs.append(
                        {
                            "job_id": ev["job_id"],
                            "status": ev.get("status", ""),
                            "to_state": r.to_state,
                        }
                    )
                if isinstance(ev.get("dry_run"), dict):
                    awx_jobs.append(
                        {
                            "job_id": ev["dry_run"].get("job_id"),
                            "status": ev["dry_run"].get("status", ""),
                            "to_state": r.to_state,
                        }
                    )
                # E5 execution provenance, derived from stored evidence only:
                # a run-python template (or a script_name in evidence) means
                # the job was a python script job; otherwise the default is
                # "playbook" — the change record stores no job_type column,
                # and no value is invented when the evidence is silent.
                if _find_in_evidence(ev, "template") == "run-python":
                    job_type = "python"
                found_script = _find_in_evidence(ev, "script_name")
                if isinstance(found_script, str) and found_script:
                    job_type = "python"
                    script_name = found_script
                    found_sha = _find_in_evidence(ev, "script_sha")
                    if isinstance(found_sha, str) and found_sha:
                        script_sha = found_sha
                if not declared_changes:
                    declared_changes = _normalise_declared(
                        _find_in_evidence(ev, "declared_changes")
                    )
            # Reconciliation status comes from the change state; the drift
            # count from drift records linked to this ticket. A state other
            # than reconciled / reconciliation_pending has no reconciliation
            # status (None), and a change with no drift records reports 0.
            state_value = str(
                change.state.value if hasattr(change.state, "value") else change.state
            )
            reconciliation_status: str | None = None
            if state_value == "reconciled":
                reconciliation_status = "reconciled"
            elif state_value == "reconciliation_pending":
                reconciliation_status = "reconciliation_pending"
            drift_count = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(DriftRecord)
                        .where(DriftRecord.linked_ticket == change.ticket_number)
                    )
                ).scalar_one()
            )
            return {
                "change": change,
                "transitions": transitions,
                "intent": change.intent,
                "diff": diff,
                "diff_summary": diff_summary,
                "checks": checks,
                "batfish": batfish,
                "awx_jobs": awx_jobs,
                "job_type": job_type,
                "script_name": script_name,
                "script_sha": script_sha,
                "declared_changes": declared_changes,
                "reconciliation_status": reconciliation_status,
                "drift_count": drift_count,
            }

    async def collect_before_change(
        self, change_id: str, actor: str
    ) -> builtins.list[dict[str, Any]]:
        """Collect the running config for every device on the change and store
        the real running-vs-candidate diff for each one (F1).

        Raises ValueError when no config store / AWX adapter is wired in, or
        the change does not exist. `actor` is reserved for the audit trail;
        collection itself records no state transition.
        """
        if self._config_store is None or (self._awx is None and self._engine is None):
            raise ValueError(
                "config store and AWX adapter are required to collect a running config"
            )
        change = await self.get(change_id)
        if change is None:
            raise ValueError(f"change {change_id} not found")
        state = ChangeState(change.state) if isinstance(change.state, str) else change.state
        if state not in {ChangeState.VALIDATED, ChangeState.PLANNED, ChangeState.CI_PASSED}:
            raise ValueError(f"pre-change diff capture is not legal from {state.value}")
        backup = BackupService(
            self._engine if self._engine is not None else self._awx,  # type: ignore[arg-type]
            self._config_store,
        )
        results: list[dict[str, Any]] = []
        for device in list(change.devices or []):
            results.append(
                await backup.collect_and_diff(
                    change.id, device, change.ticket_number, dict(change.intent or {})
                )
            )
        return results

    async def reconcile_override(self, change_id: str, *, actor: str, reason: str) -> Change | None:
        """Approver override for a change stuck in reconciliation_pending (E3).

        Choice (see POST /changes/{id}/reconcile-override): the override is
        recorded in the audit trail and the change is moved to reconciled,
        which makes it close-eligible. It is two legal transitions —
        reconciliation_pending -> reconciling -> reconciled — both carrying
        the override evidence, because there is no direct
        reconciliation_pending -> reconciled transition. The workflow itself
        has already returned "reconciliation_pending"; this does not re-run
        the workflow close activity, it only records the override and
        unblocks a manual close.
        """
        if not reason.strip():
            raise ValueError("reconcile override requires a reason")
        pk = _coerce_change_id(change_id)
        if pk is None:
            return None
        async with self._sessions() as session:
            change = await session.get(Change, pk)
            if change is None:
                return None
            state = ChangeState(change.state) if isinstance(change.state, str) else change.state
            if state is not ChangeState.RECONCILIATION_PENDING:
                raise ValueError(
                    f"reconcile override is only legal from reconciliation_pending, "
                    f"not {state.value}"
                )
            evidence = {"override": True, "reason": reason}
            await record_transition(
                session,
                change,
                ChangeState.RECONCILING,
                actor=actor,
                reason=f"reconcile override: {reason}",
                evidence=evidence,
            )
            await record_transition(
                session,
                change,
                ChangeState.RECONCILED,
                actor=actor,
                reason=f"reconcile override: {reason}",
                evidence=evidence,
            )
            await session.commit()
            await session.refresh(change)
            return change

    async def retry_close(
        self, change_id: str, *, actor: str, reason: str = "operator retry"
    ) -> Change | None:
        """Retry external CMDB/ITSM closure for a recoverable closure_pending change."""
        pk = _coerce_change_id(change_id)
        if pk is None:
            return None
        async with self._sessions() as session:
            change = await session.get(Change, pk)
            if change is None:
                return None
            state = ChangeState(change.state) if isinstance(change.state, str) else change.state
            if state is not ChangeState.CLOSURE_PENDING:
                raise ValueError(f"retry close is only legal from closure_pending, not {state.value}")
            ticket = change.ticket_number
            devices = list(change.devices or [])
        # External operations are deliberately idempotent and happen before
        # the state transition. If any fails, the record remains closure_pending.
        for device in devices:
            await self._netbox.update_device_status(device, "active")
            await self._netbox.add_journal_entry(
                device, "change", f"Change {ticket} applied; closure retry confirmed."
            )
        await self._snow.close(ticket, f"Change {ticket} applied and post-checked.")
        async with self._sessions() as session:
            change = await session.get(Change, pk)
            if change is None:
                return None
            state = ChangeState(change.state) if isinstance(change.state, str) else change.state
            if state is not ChangeState.CLOSURE_PENDING:
                raise ValueError(f"change state changed during close retry: {state.value}")
            await record_transition(
                session,
                change,
                ChangeState.CLOSED,
                actor=actor,
                reason=reason,
                evidence={"servicenow": "closed", "netbox": "active"},
            )
            await session.commit()
            await session.refresh(change)
        await DeviceLockService(self._sessions).release_all(devices, ticket)
        return change

    async def rollback(
        self, change_id: str, *, actor: str, reason: str = "manual rollback"
    ) -> Change | None:
        """Manual rollback. Legal only from applied / post_checked. Caller (API) checks role."""
        pk = _coerce_change_id(change_id)
        if pk is None:
            return None
        async with self._sessions() as session:
            change = await session.get(Change, pk)
            if change is None:
                return None
            state = ChangeState(change.state) if isinstance(change.state, str) else change.state
            if state not in {ChangeState.APPLIED, ChangeState.POST_CHECKED}:
                raise ValueError(f"rollback is not legal from {state.value}")
            await record_transition(
                session,
                change,
                ChangeState.ROLLED_BACK,
                actor=actor,
                reason=reason,
                evidence={"manual": True},
            )
            await session.commit()
            await session.refresh(change)
            return change
