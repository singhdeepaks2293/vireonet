from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vireonet.adapters.protocols import NetBoxProtocol, ServiceNowProtocol
from vireonet.intent import ChangeIntent, parse_intent
from vireonet.models import AuditLog, Change
from vireonet.policy import PolicyEngine
from vireonet.render import render_intent
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


class DuplicateTicketError(ValueError):
    pass


def intent_from_servicenow_variables(ticket: str, variables: dict[str, str], requested_by: str) -> dict[str, Any]:
    """Catalog-variable -> intent mapping. ONE place, per-catalog.

    Expected variables (catalog admin defines them): change_type, site,
    devices (comma-separated), plus per-type fields e.g. vlan_id, vlan_name.
    Unknown catalogs must add their mapping here, not in the API.
    """
    devices = [d.strip() for d in (variables.get("devices") or "").split(",") if d.strip()]
    base: dict[str, Any] = {
        "ticket_number": ticket, "requested_by": requested_by,
        "site": variables.get("site", ""), "devices": devices,
        "change_type": variables.get("change_type", "vlan_add"),
    }
    for key in ("vlan_id", "vlan_name", "acl_name", "action", "protocol", "source", "destination",
                "destination_port", "sequence", "prefix", "next_hop", "vrf", "interface",
                "description", "enabled", "mtu", "serial", "model", "platform", "mac_address"):
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
    ) -> None:
        self._sessions = session_factory
        self._snow = servicenow
        self._netbox = netbox
        self._policy = policy

    async def _existing(self, session: AsyncSession, ticket: str) -> Change | None:
        return (await session.execute(select(Change).where(Change.ticket_number == ticket))).scalar_one_or_none()

    async def create_from_intent(
        self, data: dict[str, Any], *, actor: str, emergency: bool = False, now: datetime | None = None
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
                ticket_number=intent.ticket_number, change_type=intent.change_type,
                state=ChangeState.RECEIVED, requester=intent.requested_by,
                intent=intent.model_dump(mode="json"), devices=list(intent.devices),
            )
            session.add(change)
            await session.flush()
            result = await validate_intent(intent, self._netbox, self._policy, now=now, emergency=emergency)
            if result.ok:
                await record_transition(session, change, ChangeState.VALIDATED, actor=actor, reason="NetBox + policy passed", evidence=result.evidence)
            else:
                await record_transition(session, change, ChangeState.REJECTED, actor=actor, reason="; ".join(result.errors), evidence=result.evidence)
            await session.commit()
            await session.refresh(change)
            return change, True

    async def create_from_servicenow(self, ticket: str, *, actor: str, sys_id: str | None = None) -> tuple[Change, bool]:
        req = await self._snow.get_request_by_sys_id(sys_id) if sys_id else await self._snow.get_request(ticket)
        try:
            data = intent_from_servicenow_variables(req.ticket, req.variables, req.requested_by)
            return await self.create_from_intent(data, actor=actor)
        except (PydanticValidationError, ValueError, KeyError) as exc:
            # Unparseable catalog payload: still persist it as rejected, with evidence.
            async with self._sessions() as session:
                existing = await self._existing(session, req.ticket)
                if existing is not None:
                    return existing, False
                change = Change(ticket_number=req.ticket, change_type="unknown", state=ChangeState.RECEIVED, requester=req.requested_by, intent={"variables": req.variables}, devices=[])
                session.add(change)
                await session.flush()
                await record_transition(session, change, ChangeState.REJECTED, actor=actor, reason=f"unparseable ServiceNow variables: {exc}", evidence={"variables": req.variables})
                await session.commit()
                await session.refresh(change)
                return change, True

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
            column = {"created_at": Change.created_at, "ticket_number": Change.ticket_number, "change_type": Change.change_type, "state": Change.state}.get(sort_by, Change.created_at)
            query = query.order_by(column.desc() if sort_dir == "desc" else column.asc())
            query = query.offset((page - 1) * page_size).limit(page_size)
            items = list((await session.execute(query)).scalars().all())
            return items, total

    async def get_detail(self, change_id: str) -> dict | None:
        pk = _coerce_change_id(change_id)
        if pk is None:
            return None
        async with self._sessions() as session:
            change = await session.get(Change, pk)
            if change is None:
                return None
            rows = (await session.execute(
                select(AuditLog).where(AuditLog.change_id == change.id).order_by(AuditLog.created_at)
            )).scalars().all()
            transitions = [
                {"from_state": r.from_state, "to_state": r.to_state, "actor": r.actor, "reason": r.reason, "evidence": r.evidence, "created_at": r.created_at.isoformat() if r.created_at else None}
                for r in rows
            ]
            # Diff: rendered intent lines as additions, grouped by device.
            diff: list[dict] = []
            new_no = 0
            try:
                intent = parse_intent(change.intent)
                rendered = render_intent(intent)
                for device, lines in rendered.items():
                    diff.append({"old_line": None, "new_line": None, "type": "context", "text": f"# {device}"})
                    for line in lines:
                        new_no += 1
                        diff.append({"old_line": None, "new_line": new_no, "type": "add", "text": line})
            except Exception:
                diff = []
            # Checks / Batfish / AWX are read from audit evidence.
            checks: list[dict] = []
            batfish: dict = {}
            awx_jobs: list[dict] = []
            for r in rows:
                ev = r.evidence or {}
                if isinstance(ev.get("checks"), list):
                    checks.extend(ev["checks"])
                if isinstance(ev.get("gate"), dict):
                    batfish = ev["gate"]
                if ev.get("job_id") is not None:
                    awx_jobs.append({"job_id": ev["job_id"], "status": ev.get("status", ""), "to_state": r.to_state})
                if isinstance(ev.get("dry_run"), dict):
                    awx_jobs.append({"job_id": ev["dry_run"].get("job_id"), "status": ev["dry_run"].get("status", ""), "to_state": r.to_state})
            return {
                "change": change, "transitions": transitions, "intent": change.intent,
                "diff": diff, "checks": checks, "batfish": batfish, "awx_jobs": awx_jobs,
            }

    async def rollback(self, change_id: str, *, actor: str, reason: str = "manual rollback") -> Change | None:
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
            await record_transition(session, change, ChangeState.ROLLED_BACK, actor=actor, reason=reason, evidence={"manual": True})
            await session.commit()
            await session.refresh(change)
            return change
