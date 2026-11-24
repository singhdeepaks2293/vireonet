"""Flows, versions and runs in the database."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vireonet.flows.schema import FlowValidationError, ensure_valid
from vireonet.models import Flow, FlowRun, FlowVersion

MAX_LOG_ENTRIES = 2000
EXPORT_FORMAT = "vireonet-flow/1"
TERMINAL = {"succeeded", "failed", "cancelled"}


class FlowNotFound(LookupError):
    pass


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except ValueError as exc:
        raise FlowNotFound(str(value)) from exc


def flow_dict(flow: Flow, version: FlowVersion | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(flow.id),
        "name": flow.name,
        "description": flow.description,
        "enabled": flow.enabled,
        "trigger_type": flow.trigger_type,
        "latest_version": flow.latest_version,
        "published_version": flow.published_version,
        "created_by": flow.created_by,
        "created_at": flow.created_at.isoformat() if flow.created_at else None,
        "updated_at": flow.updated_at.isoformat() if flow.updated_at else None,
    }
    if version is not None:
        out.update(
            version=version.version,
            trigger=version.trigger,
            definition=version.definition,
            comment=version.comment,
        )
    return out


def run_dict(run: FlowRun, flow_name: str = "", *, full: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(run.id),
        "flow_id": str(run.flow_id),
        "flow_name": flow_name,
        "version": run.version,
        "parent_run_id": str(run.parent_run_id) if run.parent_run_id else None,
        "trigger_type": run.trigger_type,
        "status": run.status,
        "error": run.error,
        "started_by": run.started_by,
        "waiting": run.waiting or {},
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "steps_logged": len(run.log or []),
    }
    if full:
        out.update(
            trigger_event=run.trigger_event,
            inputs=run.inputs,
            log=run.log or [],
            variables=run.variables or {},
            outputs=run.outputs or {},
        )
    return out


class FlowStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    # --- flows -----------------------------------------------------------------------------

    async def create(
        self,
        *,
        name: str,
        description: str = "",
        trigger: dict[str, Any] | None = None,
        definition: dict[str, Any],
        actor: str,
        allow_scripts: bool,
        enabled: bool = False,
        publish: bool = False,
        comment: str = "created",
    ) -> tuple[dict[str, Any], str]:
        """Returns (flow, webhook token). The token is shown only once."""
        name = name.strip()
        if not name:
            raise FlowValidationError(["name is required"])
        trig = dict(trigger or {"type": "manual"})
        d = ensure_valid(definition, trig, allow_scripts=allow_scripts)
        token = secrets.token_urlsafe(24)
        async with self._sessions() as s:
            flow = Flow(
                name=name,
                description=description,
                enabled=enabled,
                trigger_type=str(trig.get("type", "manual")),
                latest_version=1,
                published_version=1 if publish else None,
                webhook_token_hash=_hash(token),
                created_by=actor,
            )
            s.add(flow)
            try:
                await s.flush()
            except IntegrityError as exc:
                raise FlowValidationError([f"a flow named {name!r} already exists"]) from exc
            v = FlowVersion(
                flow_id=flow.id,
                version=1,
                trigger=trig,
                definition=d,
                comment=comment,
                created_by=actor,
            )
            s.add(v)
            await s.commit()
            await s.refresh(flow)
            return flow_dict(flow, v), token

    async def update(
        self,
        flow_id: str,
        *,
        actor: str,
        allow_scripts: bool,
        name: str | None = None,
        description: str | None = None,
        trigger: dict[str, Any] | None = None,
        definition: dict[str, Any] | None = None,
        comment: str = "",
    ) -> dict[str, Any]:
        async with self._sessions() as s:
            flow = await self._flow(s, flow_id)
            latest = await self._version(s, flow.id, flow.latest_version)
            trig = dict(trigger if trigger is not None else latest.trigger)
            d = ensure_valid(
                definition if definition is not None else latest.definition,
                trig,
                allow_scripts=allow_scripts,
            )
            if name is not None and name.strip():
                flow.name = name.strip()
            if description is not None:
                flow.description = description
            v = latest
            if d != latest.definition or trig != latest.trigger:
                flow.latest_version += 1
                v = FlowVersion(
                    flow_id=flow.id,
                    version=flow.latest_version,
                    trigger=trig,
                    definition=d,
                    comment=comment or f"version {flow.latest_version}",
                    created_by=actor,
                )
                s.add(v)
            flow.trigger_type = str(trig.get("type", "manual"))
            try:
                await s.commit()
            except IntegrityError as exc:
                raise FlowValidationError([f"a flow named {name!r} already exists"]) from exc
            await s.refresh(flow)
            return flow_dict(flow, v)

    async def publish(self, flow_id: str, version: int | None = None) -> dict[str, Any]:
        async with self._sessions() as s:
            flow = await self._flow(s, flow_id)
            v = await self._version(s, flow.id, version or flow.latest_version)
            flow.published_version = v.version
            await s.commit()
            await s.refresh(flow)
            return flow_dict(flow, v)

    async def set_enabled(self, flow_id: str, enabled: bool) -> dict[str, Any]:
        async with self._sessions() as s:
            flow = await self._flow(s, flow_id)
            if enabled and flow.published_version is None:
                raise FlowValidationError(["publish a version before enabling the trigger"])
            flow.enabled = enabled
            await s.commit()
            await s.refresh(flow)
            return flow_dict(flow)

    async def rotate_token(self, flow_id: str) -> str:
        token = secrets.token_urlsafe(24)
        async with self._sessions() as s:
            flow = await self._flow(s, flow_id)
            flow.webhook_token_hash = _hash(token)
            await s.commit()
        return token

    async def check_token(self, flow_id: str, token: str) -> bool:
        async with self._sessions() as s:
            flow = await self._flow(s, flow_id)
            return bool(token) and hmac.compare_digest(flow.webhook_token_hash, _hash(token))

    async def delete(self, flow_id: str) -> None:
        async with self._sessions() as s:
            flow = await self._flow(s, flow_id)
            await s.execute(delete(FlowRun).where(FlowRun.flow_id == flow.id))
            await s.execute(delete(FlowVersion).where(FlowVersion.flow_id == flow.id))
            await s.delete(flow)
            await s.commit()

    async def get(self, flow_id: str, version: int | None = None) -> dict[str, Any]:
        async with self._sessions() as s:
            flow = await self._flow(s, flow_id)
            v = await self._version(s, flow.id, version or flow.latest_version)
            return flow_dict(flow, v)

    async def get_by_name(self, name: str) -> dict[str, Any]:
        async with self._sessions() as s:
            flow = (await s.execute(select(Flow).where(Flow.name == name))).scalar_one_or_none()
            if flow is None:
                raise FlowNotFound(name)
            v = await self._version(s, flow.id, flow.published_version or flow.latest_version)
            return flow_dict(flow, v)

    async def list_flows(self) -> list[dict[str, Any]]:
        async with self._sessions() as s:
            flows = (await s.execute(select(Flow).order_by(Flow.name))).scalars().all()
            last = dict(
                (
                    await s.execute(
                        select(FlowRun.flow_id, func.max(FlowRun.created_at)).group_by(
                            FlowRun.flow_id
                        )
                    )
                ).all()
            )
            counts = dict(
                (
                    await s.execute(select(FlowRun.flow_id, func.count()).group_by(FlowRun.flow_id))
                ).all()
            )
            out = []
            for f in flows:
                v = await self._version(s, f.id, f.latest_version)
                d = flow_dict(f)
                d["trigger"] = v.trigger
                d["steps"] = len(v.definition.get("steps") or [])
                d["runs"] = counts.get(f.id, 0)
                d["last_run_at"] = last[f.id].isoformat() if last.get(f.id) else None
                out.append(d)
            return out

    async def versions(self, flow_id: str) -> list[dict[str, Any]]:
        async with self._sessions() as s:
            flow = await self._flow(s, flow_id)
            rows = (
                (
                    await s.execute(
                        select(FlowVersion)
                        .where(FlowVersion.flow_id == flow.id)
                        .order_by(FlowVersion.version.desc())
                    )
                )
                .scalars()
                .all()
            )
            return [
                {
                    "version": r.version,
                    "comment": r.comment,
                    "created_by": r.created_by,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "published": r.version == flow.published_version,
                    "steps": len(r.definition.get("steps") or []),
                    "trigger_type": r.trigger.get("type", "manual"),
                }
                for r in rows
            ]

    async def triggered_flows(self, trigger_types: set[str]) -> list[dict[str, Any]]:
        """Enabled flows with a published version, for these trigger types."""
        async with self._sessions() as s:
            flows = (
                (
                    await s.execute(
                        select(Flow).where(
                            Flow.enabled.is_(True),
                            Flow.published_version.is_not(None),
                            Flow.trigger_type.in_(sorted(trigger_types)),
                        )
                    )
                )
                .scalars()
                .all()
            )
            out = []
            for f in flows:
                v = await self._version(s, f.id, int(f.published_version or 1))
                out.append(flow_dict(f, v))
            return out

    def export(self, flow: dict[str, Any]) -> dict[str, Any]:
        return {
            "format": EXPORT_FORMAT,
            "name": flow["name"],
            "description": flow.get("description", ""),
            "trigger": flow.get("trigger") or {"type": "manual"},
            "definition": flow.get("definition") or {},
        }

    # --- runs ----------------------------------------------------------------------------------

    async def create_run(
        self,
        flow: dict[str, Any],
        *,
        trigger_type: str,
        event: dict[str, Any],
        inputs: dict[str, Any] | None = None,
        dedup_key: str = "",
        started_by: str = "",
        parent_run_id: str | None = None,
    ) -> dict[str, Any] | None:
        """None when a run with the same dedup key already exists for this flow."""
        async with self._sessions() as s:
            run = FlowRun(
                flow_id=_uuid(flow["id"]),
                version=int(flow["version"]),
                trigger_type=trigger_type,
                trigger_event=event,
                inputs=inputs or {},
                dedup_key=dedup_key[:255] or None,
                status="queued",
                log=[],
                variables={},
                outputs={},
                waiting={},
                started_by=started_by,
                parent_run_id=_uuid(parent_run_id) if parent_run_id else None,
            )
            s.add(run)
            try:
                await s.commit()
            except IntegrityError:
                return None
            await s.refresh(run)
            return run_dict(run, flow.get("name", ""))

    async def append_log(
        self,
        run_id: str,
        entries: list[dict[str, Any]],
        *,
        status: str | None = None,
        waiting: dict[str, Any] | None = None,
    ) -> None:
        async with self._sessions() as s:
            run = (
                await s.execute(
                    select(FlowRun).where(FlowRun.id == _uuid(run_id)).with_for_update()
                )
            ).scalar_one_or_none()
            if run is None:
                raise FlowNotFound(run_id)
            log = list(run.log or []) + entries
            run.log = log[-MAX_LOG_ENTRIES:]
            if status and run.status not in TERMINAL:
                run.status = status
            if waiting is not None:
                run.waiting = waiting
            await s.commit()

    async def finish(self, run_id: str, result: dict[str, Any]) -> None:
        async with self._sessions() as s:
            run = await s.get(FlowRun, _uuid(run_id))
            if run is None:
                raise FlowNotFound(run_id)
            run.status = str(result.get("status", "failed"))
            run.error = str(result.get("error", ""))[:4000]
            run.variables = result.get("vars") or {}
            run.outputs = result.get("outputs") or {}
            run.waiting = {}
            run.finished_at = datetime.now(UTC)
            await s.commit()

    async def get_run(self, run_id: str) -> dict[str, Any]:
        async with self._sessions() as s:
            run = await s.get(FlowRun, _uuid(run_id))
            if run is None:
                raise FlowNotFound(run_id)
            flow = await s.get(Flow, run.flow_id)
            return run_dict(run, flow.name if flow else "")

    async def list_runs(self, flow_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        async with self._sessions() as s:
            q = select(FlowRun, Flow.name).join(Flow, Flow.id == FlowRun.flow_id)
            if flow_id:
                q = q.where(FlowRun.flow_id == _uuid(flow_id))
            rows = (await s.execute(q.order_by(FlowRun.created_at.desc()).limit(limit))).all()
            return [run_dict(r, n, full=False) for r, n in rows]

    # --- helpers ----------------------------------------------------------------------------------

    async def _flow(self, s: AsyncSession, flow_id: str) -> Flow:
        flow = await s.get(Flow, _uuid(flow_id))
        if flow is None:
            raise FlowNotFound(flow_id)
        return flow

    async def _version(self, s: AsyncSession, flow_id: uuid.UUID, version: int) -> FlowVersion:
        v = (
            await s.execute(
                select(FlowVersion).where(
                    FlowVersion.flow_id == flow_id, FlowVersion.version == version
                )
            )
        ).scalar_one_or_none()
        if v is None:
            raise FlowNotFound(f"version {version}")
        return v
