"""Drift API (D3 scan/events, D4 actions, D5 list/detail/summary/rules).

POST /drift/scan starts a DriftScanWorkflow via the Temporal client,
the same mechanism POST /changes uses to start a ChangeWorkflow. When
the wired client has no start_drift_scan method (an older client), the
scan is recorded in the DB as queued and the worker picks it up — the
endpoint never invents a Temporal client API.

POST /drift/events has no JWT: it authenticates with the drift event
token/HMAC (events.verify_event_token) and fails closed with 401 when
no drift_event_secret is configured.

Resolution endpoints (D4): rollback / merge / exception need approver
or admin; emergency-accept needs operator. Every action body needs a
non-empty reason and ServiceNow reference; the attributed actor cannot
decide their own drift (403); the device lock is held for the action
(409 when another ticket holds it); every action records a
DriftDecision and moves the record through transition_drift (audit).

GET /drift/summary never-scanned devices are best-effort: scans are
per site, not per device, so "scanned" means "has at least one drift
record" and never-scanned is NetBox devices with no drift record at
all. When NetBox is unavailable the list is empty and the response
says so (never_scanned_best_effort=true).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vireonet.api.security import CurrentUser, require_role
from vireonet.drift.collector import DriftCollector
from vireonet.drift.events import DebouncedScanner, verify_event_token
from vireonet.drift.models import DriftAuditLog, DriftDecision, DriftIgnoreRule, DriftScan
from vireonet.drift.resolve import (
    EmergencyAction,
    ExceptionAction,
    MergeAction,
    ResolutionError,
    RollbackAction,
    SelfApprovalError,
    check_self_approval,
    ensure_incident,
    handle_mr_merged,
)
from vireonet.drift.workflows import DriftScanInput
from vireonet.models import DriftRecord
from vireonet.reconciliation.drift import DriftStore
from vireonet.safety import LockHeldError
from vireonet.structlog_config import get_logger

router = APIRouter()
log = get_logger(__name__)


class DriftScanRequest(BaseModel):
    device: str | None = None
    site: str | None = None


class DriftEventRequest(BaseModel):
    device: str
    message: str = ""


class DriftActionRequest(BaseModel):
    reason: str = ""
    ticket_ref: str = ""
    owner: str | None = None
    expiry: str | None = None


class EmergencyAcceptRequest(BaseModel):
    reason: str = ""
    ticket_ref: str = ""
    deadline_hours: int | None = None


class MrMergedRequest(BaseModel):
    merged: bool = True
    branch: str | None = None


class IgnoreRuleInput(BaseModel):
    pattern: str
    platform: str = "*"
    scope: str = "global"
    target: str = ""


class IgnoreRulesRequest(BaseModel):
    rules: list[IgnoreRuleInput] = []


class DriftRecordResponse(BaseModel):
    id: str
    device: str
    kind: str
    severity: str
    status: str
    first_seen: str | None = None
    last_seen: str | None = None
    baseline_ref: str | None = None
    baseline_hash: str | None = None
    running_hash: str | None = None
    diff: str = ""
    attributed_actor: str | None = None
    attributed_at: str | None = None
    linked_ticket: str | None = None


class DriftDecisionResponse(BaseModel):
    id: str
    action: str
    actor: str
    reason: str
    ticket_ref: str
    expiry: str | None = None
    created_at: str | None = None


class DriftAuditResponse(BaseModel):
    id: str
    from_state: str
    to_state: str
    actor: str
    reason: str
    created_at: str | None = None


class DriftDetailResponse(BaseModel):
    record: DriftRecordResponse
    decisions: list[DriftDecisionResponse] = []
    audit: list[DriftAuditResponse] = []


class DriftListResponse(BaseModel):
    items: list[DriftRecordResponse]
    total: int
    limit: int
    offset: int


class LastScanResponse(BaseModel):
    site: str
    scan_id: str | None = None
    status: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    devices_scanned: int = 0
    devices_with_drift: int = 0
    devices_failed: int = 0


class DriftSummaryResponse(BaseModel):
    by_severity: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    by_status: dict[str, int] = {}
    total_open: int = 0
    last_scan_per_site: list[LastScanResponse] = []
    scanned_devices: list[str] = []
    never_scanned_devices: list[str] = []
    never_scanned_best_effort: bool = True


class IgnoreRuleResponse(BaseModel):
    id: str
    pattern: str
    platform: str
    scope: str
    target: str
    created_by: str
    created_at: str | None = None


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _debouncer(request: Request) -> DebouncedScanner:
    debouncer = getattr(request.app.state, "drift_debouncer", None)
    if debouncer is None:
        debouncer = DebouncedScanner()
        request.app.state.drift_debouncer = debouncer
    return debouncer


def _sessions(request: Request) -> async_sessionmaker[AsyncSession]:
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise HTTPException(status_code=503, detail="session factory not configured")
    return cast(async_sessionmaker[AsyncSession], factory)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _record_response(record: DriftRecord) -> DriftRecordResponse:
    return DriftRecordResponse(
        id=str(record.id),
        device=str(record.device),
        kind=str(record.kind),
        severity=str(record.severity),
        status=str(record.status),
        first_seen=_iso(record.first_seen),
        last_seen=_iso(record.last_seen),
        baseline_ref=record.baseline_ref,
        baseline_hash=record.baseline_hash,
        running_hash=record.running_hash,
        diff=str(record.diff or ""),
        attributed_actor=record.attributed_actor,
        attributed_at=_iso(record.attributed_at),
        linked_ticket=record.linked_ticket,
    )


async def _get_record(sessions: async_sessionmaker[AsyncSession], record_id: str) -> DriftRecord:
    import uuid

    try:
        pk = uuid.UUID(str(record_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="drift record not found") from None
    async with sessions() as session:
        record = await session.get(DriftRecord, pk)
    if record is None:
        raise HTTPException(status_code=404, detail="drift record not found")
    return record


def _validate_action_body(body: DriftActionRequest | EmergencyAcceptRequest) -> None:
    if not body.reason.strip():
        raise HTTPException(status_code=422, detail="reason is required")
    if not body.ticket_ref.strip():
        raise HTTPException(status_code=422, detail="ticket_ref (ServiceNow reference) is required")


def _check_self_approval(record: DriftRecord, user: CurrentUser) -> None:
    try:
        check_self_approval(record, user.username)
    except SelfApprovalError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _adapters(request: Request) -> dict[str, Any]:
    return dict(getattr(request.app.state, "adapters", {}) or {})


def _notifier(request: Request) -> Any | None:
    return _adapters(request).get("notification")


def _servicenow(request: Request) -> Any | None:
    return _adapters(request).get("servicenow")


async def _post_action_incident(request: Request, record_id: str) -> None:
    """Work-note / incident handling after a decision (best-effort)."""
    settings = request.app.state.settings
    try:
        record = await _get_record(_sessions(request), record_id)
    except HTTPException:
        return
    await ensure_incident(
        record,
        servicenow=_servicenow(request),
        notifier=_notifier(request),
        channel=settings.notification_default_channel,
        threshold=settings.drift_incident_severity_threshold,
    )


def _parse_expiry(raw: str | None) -> datetime | None:
    if raw is None or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=422, detail=f"expiry is not an ISO datetime: {raw!r}"
        ) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


async def _start_scan(
    request: Request, *, site: str, device: str | None, date: str
) -> dict[str, Any]:
    """Start a drift scan workflow, or queue it in the DB as a fallback.

    Returns {"workflow_id": ...} when Temporal accepted the start, or
    {"scan_id": ..., "status": "queued"} when the client cannot start
    workflows (worker picks queued scans up — see worker.py).
    """
    client = getattr(request.app.state, "temporal_client", None)
    start = getattr(client, "start_drift_scan", None) if client is not None else None
    if callable(start):
        workflow_id = await start(
            DriftScanInput(site=site, date=date, ticket=f"drift-scan-{site}-{date}")
        )
        log.info("drift scan started", site=site, device=device or "", workflow_id=str(workflow_id))
        return {"workflow_id": str(workflow_id), "site": site, "date": date}
    sessions = getattr(request.app.state, "session_factory", None)
    if sessions is None:
        raise HTTPException(
            status_code=503, detail="drift scan cannot be queued: no session factory"
        )
    scan_id = await DriftStore(sessions).create_scan(site)
    log.info("drift scan queued (no temporal client)", site=site, scan_id=scan_id)
    return {"scan_id": scan_id, "site": site, "date": date, "status": "queued"}


@router.post("/drift/scan", status_code=202)
async def start_drift_scan(
    payload: DriftScanRequest,
    request: Request,
    _role: str = Depends(require_role("operator")),
) -> dict[str, Any]:
    settings = request.app.state.settings
    site = payload.site or (settings.drift_sites[0] if settings.drift_sites else "dc1")
    result = await _start_scan(request, site=site, device=payload.device, date=_today())
    if payload.device:
        result["device"] = payload.device
    return result


@router.post("/drift/events", status_code=202)
async def drift_event(payload: DriftEventRequest, request: Request) -> dict[str, Any]:
    """Device/syslog event trigger: debounced per device, then a scan."""
    settings = request.app.state.settings
    body = await request.body()
    verify_event_token(request.headers, body, settings.drift_event_secret)
    debouncer = _debouncer(request)
    if not debouncer.should_scan(payload.device):
        return {"status": "accepted", "device": payload.device, "debounced": True}
    site = settings.drift_sites[0] if settings.drift_sites else "dc1"
    result = await _start_scan(request, site=site, device=payload.device, date=_today())
    result["debounced"] = False
    result["device"] = payload.device
    return result


# -- D5: list / summary / ignore rules (static paths before /drift/{id}) -----


@router.get("/drift", response_model=DriftListResponse)
async def list_drift(
    request: Request,
    device: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    status: str | None = Query(default=None),
    site: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _role: str = Depends(require_role("viewer")),
) -> DriftListResponse:
    sessions = _sessions(request)
    async with sessions() as session:
        query = select(DriftRecord)
        count_query = select(func.count()).select_from(DriftRecord)
        if device:
            query = query.where(DriftRecord.device == device)
            count_query = count_query.where(DriftRecord.device == device)
        if kind:
            query = query.where(DriftRecord.kind == kind)
            count_query = count_query.where(DriftRecord.kind == kind)
        if severity:
            query = query.where(DriftRecord.severity == severity)
            count_query = count_query.where(DriftRecord.severity == severity)
        if status:
            query = query.where(DriftRecord.status == status)
            count_query = count_query.where(DriftRecord.status == status)
        if site:
            # Records carry no site column; devices are named
            # <site>-<name> in this deployment, so the site filter is a
            # device-prefix match (documented in docs/DRIFT.md).
            query = query.where(DriftRecord.device.like(f"{site}-%"))
            count_query = count_query.where(DriftRecord.device.like(f"{site}-%"))
        total = int((await session.execute(count_query)).scalar_one())
        rows = list(
            (
                await session.execute(
                    query.order_by(DriftRecord.last_seen.desc()).offset(offset).limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return DriftListResponse(
        items=[_record_response(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/drift/summary", response_model=DriftSummaryResponse)
async def drift_summary(
    request: Request,
    _role: str = Depends(require_role("viewer")),
) -> DriftSummaryResponse:
    sessions = _sessions(request)
    async with sessions() as session:
        by_severity: dict[str, int] = {
            str(sev): int(count)
            for sev, count in (
                await session.execute(
                    select(DriftRecord.severity, func.count()).group_by(DriftRecord.severity)
                )
            ).all()
        }
        by_kind: dict[str, int] = {
            str(kind): int(count)
            for kind, count in (
                await session.execute(
                    select(DriftRecord.kind, func.count()).group_by(DriftRecord.kind)
                )
            ).all()
        }
        by_status: dict[str, int] = {
            str(st): int(count)
            for st, count in (
                await session.execute(
                    select(DriftRecord.status, func.count()).group_by(DriftRecord.status)
                )
            ).all()
        }
        total_open = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(DriftRecord)
                    .where(DriftRecord.status != "resolved")
                )
            ).scalar_one()
        )
        scanned = [
            str(d)
            for d in (await session.execute(select(DriftRecord.device).distinct())).scalars().all()
        ]
        scans = list(
            (await session.execute(select(DriftScan).order_by(DriftScan.started_at.desc())))
            .scalars()
            .all()
        )
    last_per_site: dict[str, DriftScan] = {}
    for scan in scans:
        last_per_site.setdefault(str(scan.site), scan)
    never_scanned: list[str] = []
    best_effort = True
    netbox = _adapters(request).get("netbox")
    list_devices = getattr(netbox, "list_devices", None) if netbox is not None else None
    if callable(list_devices):
        try:
            devices = await list_devices()
            scanned_set = set(scanned)
            never_scanned = sorted(
                str(getattr(d, "name", d))
                for d in devices
                if str(getattr(d, "name", d)) not in scanned_set
            )
            best_effort = False
        except Exception as exc:  # noqa: BLE001 - NetBox down: summary still answers
            log.warning("drift summary netbox list failed", error=str(exc))
    return DriftSummaryResponse(
        by_severity=by_severity,
        by_kind=by_kind,
        by_status=by_status,
        total_open=total_open,
        last_scan_per_site=[
            LastScanResponse(
                site=site,
                scan_id=str(scan.id),
                status=str(scan.status),
                started_at=_iso(scan.started_at),
                finished_at=_iso(scan.finished_at),
                devices_scanned=int(scan.devices_scanned),
                devices_with_drift=int(scan.devices_with_drift),
                devices_failed=int(scan.devices_failed),
            )
            for site, scan in sorted(last_per_site.items())
        ],
        scanned_devices=sorted(scanned),
        never_scanned_devices=never_scanned,
        never_scanned_best_effort=best_effort,
    )


@router.get("/drift/ignore-rules", response_model=list[IgnoreRuleResponse])
async def list_ignore_rules(
    request: Request,
    _role: str = Depends(require_role("admin")),
) -> list[IgnoreRuleResponse]:
    sessions = _sessions(request)
    async with sessions() as session:
        rows = list(
            (await session.execute(select(DriftIgnoreRule).order_by(DriftIgnoreRule.pattern)))
            .scalars()
            .all()
        )
    return [
        IgnoreRuleResponse(
            id=str(r.id),
            pattern=str(r.pattern),
            platform=str(r.platform),
            scope=str(r.scope),
            target=str(r.target),
            created_by=str(r.created_by),
            created_at=_iso(r.created_at),
        )
        for r in rows
    ]


@router.put("/drift/ignore-rules", response_model=list[IgnoreRuleResponse])
async def replace_ignore_rules(
    payload: IgnoreRulesRequest,
    request: Request,
    user: CurrentUser = Depends(require_role("admin")),
) -> list[IgnoreRuleResponse]:
    import re

    for rule in payload.rules:
        try:
            re.compile(rule.pattern)
        except re.error as exc:
            raise HTTPException(
                status_code=422,
                detail=f"ignore rule pattern is not a valid regex: {rule.pattern!r} ({exc})",
            ) from exc
    sessions = _sessions(request)
    async with sessions() as session:
        existing = list((await session.execute(select(DriftIgnoreRule))).scalars().all())
        for row in existing:
            await session.delete(row)
        created: list[DriftIgnoreRule] = []
        for rule in payload.rules:
            row = DriftIgnoreRule(
                pattern=rule.pattern,
                platform=rule.platform,
                scope=rule.scope,
                target=rule.target,
                created_by=user.username,
            )
            session.add(row)
            created.append(row)
        await session.commit()
        for row in created:
            await session.refresh(row)
    # Audit: ignore rules have no drift record, so there is no
    # DriftAuditLog row for them; the replacement is logged with the
    # actor and rule count (see docs/DRIFT.md).
    log.info(
        "drift ignore rules replaced",
        actor=user.username,
        rules=len(payload.rules),
    )
    return [
        IgnoreRuleResponse(
            id=str(r.id),
            pattern=str(r.pattern),
            platform=str(r.platform),
            scope=str(r.scope),
            target=str(r.target),
            created_by=str(r.created_by),
            created_at=_iso(r.created_at),
        )
        for r in created
    ]


# -- D5: detail ---------------------------------------------------------------


@router.get("/drift/{record_id}", response_model=DriftDetailResponse)
async def get_drift(
    record_id: str,
    request: Request,
    _role: str = Depends(require_role("viewer")),
) -> DriftDetailResponse:
    sessions = _sessions(request)
    record = await _get_record(sessions, record_id)
    async with sessions() as session:
        import uuid

        decisions = list(
            (
                await session.execute(
                    select(DriftDecision)
                    .where(DriftDecision.record_id == uuid.UUID(record_id))
                    .order_by(DriftDecision.created_at)
                )
            )
            .scalars()
            .all()
        )
        audit = list(
            (
                await session.execute(
                    select(DriftAuditLog)
                    .where(DriftAuditLog.record_id == uuid.UUID(record_id))
                    .order_by(DriftAuditLog.created_at)
                )
            )
            .scalars()
            .all()
        )
    return DriftDetailResponse(
        record=_record_response(record),
        decisions=[
            DriftDecisionResponse(
                id=str(d.id),
                action=str(d.action),
                actor=str(d.actor),
                reason=str(d.reason),
                ticket_ref=str(d.ticket_ref),
                expiry=_iso(d.expiry),
                created_at=_iso(d.created_at),
            )
            for d in decisions
        ],
        audit=[
            DriftAuditResponse(
                id=str(a.id),
                from_state=str(a.from_state),
                to_state=str(a.to_state),
                actor=str(a.actor),
                reason=str(a.reason),
                created_at=_iso(a.created_at),
            )
            for a in audit
        ],
    )


# -- D4: resolution actions ---------------------------------------------------


def _collector(request: Request) -> Callable[[str], Awaitable[str]] | None:
    engine = getattr(request.app.state, "execution_engine", None)
    locks = getattr(request.app.state, "lock_service", None)
    if engine is None or locks is None:
        return None
    collector = DriftCollector(engine, locks)

    async def collect(device: str) -> str:
        return await collector.collect(device, "", "", "drift-collect")

    return collect


def _git_provider(request: Request) -> Any:
    from vireonet.adapters.git.registry import get_git_provider

    settings = request.app.state.settings
    try:
        return get_git_provider(
            provider=settings.git_provider,
            base_url=settings.git_base_url,
            token=settings.git_token_secret,
            repo=settings.git_repo,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"Git provider not configured for drift merge: {exc}"
        ) from exc


@router.post("/drift/{record_id}/rollback")
async def drift_rollback(
    record_id: str,
    payload: DriftActionRequest,
    request: Request,
    user: CurrentUser = Depends(require_role("approver")),
) -> dict[str, Any]:
    _validate_action_body(payload)
    sessions = _sessions(request)
    record = await _get_record(sessions, record_id)
    _check_self_approval(record, user)
    settings = request.app.state.settings
    engine = getattr(request.app.state, "execution_engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="no execution engine configured")
    action = RollbackAction(
        sessions,
        change_service=getattr(request.app.state, "change_service", None),
        engine=engine,
        config_store=getattr(request.app.state, "config_store", None),
        device_repos=getattr(request.app.state, "device_repo_manager", None),
        locks=getattr(request.app.state, "lock_service", None),
        collector=_collector(request),
        notifier=_notifier(request),
        channel=settings.notification_default_channel,
        servicenow=_servicenow(request),
    )
    try:
        outcome = await action.execute(
            record_id,
            actor=user.username,
            reason=payload.reason,
            ticket_ref=payload.ticket_ref,
        )
    except LockHeldError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ResolutionError, SelfApprovalError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await _post_action_incident(request, record_id)
    return outcome


@router.post("/drift/{record_id}/merge")
async def drift_merge(
    record_id: str,
    payload: DriftActionRequest,
    request: Request,
    user: CurrentUser = Depends(require_role("approver")),
) -> dict[str, Any]:
    _validate_action_body(payload)
    sessions = _sessions(request)
    record = await _get_record(sessions, record_id)
    _check_self_approval(record, user)
    settings = request.app.state.settings
    locks = getattr(request.app.state, "lock_service", None)
    ticket = f"DRIFT-MERGE-{record_id.replace('-', '')[:8]}"
    if locks is not None:
        try:
            await locks.acquire(str(record.device), ticket, holder=user.username)
        except LockHeldError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        action = MergeAction(
            sessions,
            git=_git_provider(request),
            config_store=getattr(request.app.state, "config_store", None),
            device_repos=getattr(request.app.state, "device_repo_manager", None),
            notifier=_notifier(request),
            channel=settings.notification_default_channel,
            target_branch=settings.git_default_branch,
        )
        outcome = await action.execute(
            record_id,
            actor=user.username,
            reason=payload.reason,
            ticket_ref=payload.ticket_ref,
        )
    except (ResolutionError, SelfApprovalError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        if locks is not None:
            with suppress(Exception):  # noqa: BLE001 - release is best-effort
                await locks.release(str(record.device), ticket)
    await _post_action_incident(request, record_id)
    return outcome


@router.post("/drift/{record_id}/exception")
async def drift_exception(
    record_id: str,
    payload: DriftActionRequest,
    request: Request,
    user: CurrentUser = Depends(require_role("approver")),
) -> dict[str, Any]:
    _validate_action_body(payload)
    if not (payload.owner or "").strip():
        raise HTTPException(status_code=422, detail="owner is required for an exception")
    sessions = _sessions(request)
    record = await _get_record(sessions, record_id)
    _check_self_approval(record, user)
    settings = request.app.state.settings
    expiry = _parse_expiry(payload.expiry) or (
        datetime.now(UTC) + timedelta(days=settings.drift_exception_max_days)
    )
    locks = getattr(request.app.state, "lock_service", None)
    ticket = f"DRIFT-EXC-{record_id.replace('-', '')[:8]}"
    if locks is not None:
        try:
            await locks.acquire(str(record.device), ticket, holder=user.username)
        except LockHeldError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        action = ExceptionAction(
            sessions,
            notifier=_notifier(request),
            channel=settings.notification_default_channel,
            max_days=settings.drift_exception_max_days,
        )
        outcome = await action.execute(
            record_id,
            actor=user.username,
            reason=payload.reason,
            ticket_ref=payload.ticket_ref,
            owner=str(payload.owner),
            expiry=expiry,
        )
    except (ResolutionError, SelfApprovalError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        if locks is not None:
            with suppress(Exception):  # noqa: BLE001 - release is best-effort
                await locks.release(str(record.device), ticket)
    await _post_action_incident(request, record_id)
    return outcome


@router.post("/drift/{record_id}/emergency-accept")
async def drift_emergency_accept(
    record_id: str,
    payload: EmergencyAcceptRequest,
    request: Request,
    user: CurrentUser = Depends(require_role("operator")),
) -> dict[str, Any]:
    _validate_action_body(payload)
    sessions = _sessions(request)
    record = await _get_record(sessions, record_id)
    settings = request.app.state.settings
    locks = getattr(request.app.state, "lock_service", None)
    ticket = f"DRIFT-EMG-{record_id.replace('-', '')[:8]}"
    if locks is not None:
        try:
            await locks.acquire(str(record.device), ticket, holder=user.username)
        except LockHeldError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        action = EmergencyAction(
            sessions,
            notifier=_notifier(request),
            channel=settings.notification_default_channel,
            deadline_hours=settings.drift_emergency_deadline_hours,
        )
        outcome = await action.execute(
            record_id,
            actor=user.username,
            reason=payload.reason,
            ticket_ref=payload.ticket_ref,
            deadline_hours=payload.deadline_hours,
        )
    except ResolutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        if locks is not None:
            with suppress(Exception):  # noqa: BLE001 - release is best-effort
                await locks.release(str(record.device), ticket)
    await _post_action_incident(request, record_id)
    return outcome


@router.post("/drift/{record_id}/mr-merged")
async def drift_mr_merged(
    record_id: str,
    payload: MrMergedRequest,
    request: Request,
    user: CurrentUser = Depends(require_role("operator")),
) -> dict[str, Any]:
    """Fallback for the Git MR-merged webhook (see api/webhooks.py).

    Body: {"merged": true, "branch": "drift/<device>/<record-short>"}.
    The branch carries the record id; the path id must match it.
    """
    if not payload.branch:
        raise HTTPException(
            status_code=422, detail="branch is required (drift/<device>/<record-short>)"
        )
    sessions = _sessions(request)
    record = await _get_record(sessions, record_id)
    short = str(record.id).replace("-", "")[:8]
    if not payload.branch.rsplit("/", 1)[-1].startswith(short):
        raise HTTPException(status_code=422, detail="branch does not match this drift record")
    result = await handle_mr_merged(
        sessions,
        branch=payload.branch,
        merged=payload.merged,
        config_store=getattr(request.app.state, "config_store", None),
        device_repos=getattr(request.app.state, "device_repo_manager", None),
        collector=_collector(request),
        notifier=_notifier(request),
        channel=request.app.state.settings.notification_default_channel,
        actor=user.username,
    )
    if result is None:
        raise HTTPException(
            status_code=404, detail="no merge_pending drift record matches this branch"
        )
    return result
