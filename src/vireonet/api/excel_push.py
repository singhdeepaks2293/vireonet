"""Excel config push API and ITSM endpoints."""

from __future__ import annotations

import base64
import binascii
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError

from vireonet.api.changes import _to_response, get_service, start_workflow_if_validated
from vireonet.api.security import CurrentUser, require_role, verify_webhook
from vireonet.excel_push.window import iso, parse_when
from vireonet.excel_push.workbook import WorkbookError, demo_workbook
from vireonet.itsm.base import XLSX_TYPE, ItsmError
from vireonet.services.changes import ChangeService

router = APIRouter()


def _svc(request: Request) -> Any:
    svc = getattr(request.app.state, "excel_push", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Excel config push is not configured")
    return svc


def _decode(content_b64: str) -> bytes:
    try:
        return base64.b64decode(content_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="content_b64 is not valid base64") from exc


class UploadBody(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_b64: str


Automation = Literal["manual", "full"]


class SubmitUpload(UploadBody):
    ticket_number: str = Field(min_length=1, max_length=64)
    site: str = Field(min_length=1)
    emergency: bool = False
    # manual: an approver approves in VireoNet. full: no approval click (the
    # submitter, who must be an approver, approves by submitting).
    automation: Automation = "manual"
    # Planned window (ISO 8601); required for "full" (see settings).
    planned_start: str = ""
    planned_end: str = ""


class SubmitTicket(BaseModel):
    ticket_number: str = Field(min_length=1, max_length=64)
    site: str = Field(min_length=1)
    emergency: bool = False
    requested_by: str = ""
    automation: Automation = "manual"


class StateEvent(BaseModel):
    """An ITSM state-change webhook (ServiceNow business rule / flow, Jira
    automation, ...). Only the ticket key matters: the state is re-read from
    the ITSM tool, never trusted from the payload."""

    ticket_number: str = Field(default="", max_length=64)
    number: str = Field(default="", max_length=64)  # ServiceNow field name
    key: str = Field(default="", max_length=64)  # Jira field name
    state: str = ""
    site: str = ""
    emergency: bool = False
    # For flow triggers: created / updated / state_changed, and the state
    # before the change when the ITSM tool sends it ("changed from → to").
    event: str = Field(default="state_changed", max_length=32)
    previous_state: str = Field(default="", max_length=64)


def _preview(request: Request, content: bytes) -> dict[str, Any]:
    svc = _svc(request)
    try:
        wb = svc.parse(content)
    except WorkbookError as exc:
        return {"valid": False, "errors": exc.errors, "warnings": [], "devices": []}
    policy = getattr(request.app.state, "policy", None)
    denials = policy.raw_config_denials(wb.configs) if policy is not None else []
    return {
        "valid": not denials,
        "errors": denials,
        "warnings": wb.warnings,
        "devices": wb.summary(),
    }


@router.get("/config-push/template.xlsx")
async def template(_role: Any = Depends(require_role("viewer"))) -> Response:
    return Response(
        demo_workbook(),
        media_type=XLSX_TYPE,
        headers={
            "Content-Disposition": 'attachment; filename="vireonet-config-push-template.xlsx"'
        },
    )


@router.post("/config-push/preview")
async def preview(
    payload: UploadBody, request: Request, _role: Any = Depends(require_role("viewer"))
) -> dict[str, Any]:
    return _preview(request, _decode(payload.content_b64))


async def _create(
    request: Request,
    service: ChangeService,
    *,
    ticket: str,
    site: str,
    filename: str,
    content: bytes,
    requested_by: str,
    emergency: bool,
    source: str,
    actor: str,
    pre_approved: bool = False,
    approval_note: str = "",
    window_start: str = "",
    window_end: str = "",
) -> dict[str, Any]:
    svc = _svc(request)
    existing = await service.get_by_ticket(ticket) if hasattr(service, "get_by_ticket") else None
    if existing is not None:
        # Repeated webhook / double submit: the ticket is the change key.
        return {
            "change": _to_response(existing).model_dump(),
            "created": False,
            "workflow_started": False,
            "workbook_id": "",
            "warnings": [],
            "automation": "full" if (existing.intent or {}).get("pre_approved") else "manual",
            "window": {
                "start": (existing.intent or {}).get("window_start", ""),
                "end": (existing.intent or {}).get("window_end", ""),
            },
        }
    start, end = _check_window(request, window_start, window_end, pre_approved)
    try:
        wb = svc.parse(content)
    except WorkbookError as exc:
        raise HTTPException(status_code=422, detail={"errors": exc.errors}) from exc
    workbook_id = await svc.store(ticket, filename, content, source=source)
    intent = {
        "change_type": "config_push",
        "devices": list(wb.configs),
        "site": site,
        "ticket_number": ticket,
        "requested_by": requested_by or actor,
        "configs": wb.configs,
        "rollback_configs": wb.rollback_configs,
        "source_file": filename,
        "itsm": source,
        "workbook_id": workbook_id,
        "skip_ci": True,
        "pre_approved": pre_approved,
        "approval_note": approval_note,
        "window_start": iso(start),
        "window_end": iso(end),
    }
    try:
        # The policy change window is judged at the planned start, not now:
        # a ticket approved in the afternoon for a 02:00 window is valid.
        change, created = await service.create_from_intent(
            intent, actor=actor, emergency=emergency, now=start
        )
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"intent is invalid: {exc}") from exc
    started = (
        await start_workflow_if_validated(request, change, emergency=emergency)
        if created
        else False
    )
    return {
        "change": _to_response(change).model_dump(),
        "created": created,
        "workflow_started": started,
        "workbook_id": workbook_id,
        "warnings": wb.warnings,
        "automation": "full" if pre_approved else "manual",
        "window": {"start": iso(start), "end": iso(end)},
    }


def _check_window(
    request: Request, window_start: str, window_end: str, pre_approved: bool
) -> tuple[Any, Any]:
    """Parse and check the planned window. Fully automatic pushes must have
    one (VIREONET_ITSM_REQUIRE_PLANNED_WINDOW) and it must not be over."""
    from datetime import UTC, datetime

    start, end = parse_when(window_start), parse_when(window_end)
    if (window_start and start is None) or (window_end and end is None):
        raise HTTPException(status_code=422, detail="planned start/end is not a valid date-time")
    if start and end and end <= start:
        raise HTTPException(status_code=422, detail="planned end must be after planned start")
    if end and end <= datetime.now(UTC):
        raise HTTPException(
            status_code=409,
            detail=f"the planned window ended at {iso(end)}; nothing will be pushed",
        )
    settings = request.app.state.settings
    if pre_approved and settings.itsm_require_planned_window and not (start and end):
        raise HTTPException(
            status_code=409,
            detail="fully automatic pushes run only inside a planned window: the ticket "
            "(or request) needs a planned start and end",
        )
    return start, end


@router.post("/config-push", status_code=202)
async def submit_upload(
    payload: SubmitUpload,
    request: Request,
    service: ChangeService = Depends(get_service),
    user: CurrentUser = Depends(require_role("operator")),
) -> dict[str, Any]:
    full = _check_full(payload.automation, user)
    return await _create(
        request,
        service,
        ticket=payload.ticket_number,
        site=payload.site,
        filename=payload.filename,
        content=_decode(payload.content_b64),
        requested_by=user.username,
        emergency=payload.emergency,
        source="upload",
        actor=user.username,
        pre_approved=full,
        approval_note=f"approved by {user.username} at submission (fully automatic)"
        if full
        else "",
        window_start=payload.planned_start,
        window_end=payload.planned_end,
    )


def _check_full(automation: str, user: Any) -> bool:
    """Fully automatic from the UI skips the approval click, so only an
    approver may choose it (they approve by submitting)."""
    from vireonet.iam.permissions import can

    if automation != "full":
        return False
    if not can(getattr(user, "role", "viewer"), "approver"):
        raise HTTPException(
            status_code=403, detail="fully automatic needs the approver role (it skips approval)"
        )
    return True


async def _from_ticket(
    request: Request,
    service: ChangeService,
    payload: SubmitTicket,
    actor: str,
    *,
    pre_approved: bool = False,
    approval_note: str = "",
) -> dict[str, Any]:
    svc = _svc(request)
    try:
        filename, content, notes = await svc.fetch_from_ticket(payload.ticket_number)
        ticket = await svc.itsm.get_ticket(payload.ticket_number)
    except WorkbookError as exc:
        raise HTTPException(status_code=422, detail={"errors": exc.errors}) from exc
    except ItsmError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    result = await _create(
        request,
        service,
        ticket=payload.ticket_number,
        site=payload.site,
        filename=filename,
        content=content,
        requested_by=payload.requested_by or ticket.requester,
        emergency=payload.emergency,
        source=svc.itsm.name,
        actor=actor,
        pre_approved=pre_approved,
        approval_note=approval_note,
        window_start=str(ticket.fields.get("planned_start") or ""),
        window_end=str(ticket.fields.get("planned_end") or ""),
    )
    result["skipped_attachments"] = notes
    return result


@router.post("/config-push/from-ticket", status_code=202)
async def submit_from_ticket(
    payload: SubmitTicket,
    request: Request,
    service: ChangeService = Depends(get_service),
    user: CurrentUser = Depends(require_role("operator")),
) -> dict[str, Any]:
    full = _check_full(payload.automation, user)
    note = f"approved by {user.username} at submission (fully automatic)" if full else ""
    return await _from_ticket(
        request, service, payload, user.username, pre_approved=full, approval_note=note
    )


@router.post("/webhooks/itsm/config-push", status_code=202)
async def webhook_from_ticket(
    payload: SubmitTicket, request: Request, service: ChangeService = Depends(get_service)
) -> dict[str, Any]:
    """For ITSM automation (ServiceNow flow, Jira automation, ...): same as
    /config-push/from-ticket, authenticated with the webhook secret."""
    await verify_webhook(request, request.app.state.settings)
    return await _from_ticket(request, service, payload, "itsm-webhook")


def _is_trigger(ticket: Any, triggers: list[str]) -> bool:
    wanted = {t.strip().lower() for t in triggers}
    values = {
        str(ticket.status).strip().lower(),
        str(ticket.fields.get("state_value", "")).strip().lower(),
    }
    return bool(wanted & values)


@router.post("/webhooks/itsm/state", status_code=202)
@router.post("/webhooks/servicenow", status_code=202)
async def webhook_state(
    payload: StateEvent, request: Request, service: ChangeService = Depends(get_service)
) -> dict[str, Any]:
    """End-to-end automation. The ITSM tool calls this when a ticket changes
    state. If the ticket is now in a trigger state (VIREONET_ITSM_TRIGGER_STATES,
    default Implement), VireoNet reads the Excel workbook attached to it and
    starts the change, with the ITSM state as the approval when
    VIREONET_ITSM_TRIGGER_APPROVES is true (default): no click is needed.
    Calls for other states are acknowledged and ignored. Repeats are harmless:
    the ticket number is the change key."""
    await verify_webhook(request, request.app.state.settings)
    settings = request.app.state.settings
    key = payload.ticket_number or payload.number or payload.key
    if not key:
        raise HTTPException(status_code=422, detail="ticket_number (or number / key) is required")
    svc = _svc(request)
    if svc.itsm is None:
        raise HTTPException(status_code=503, detail="no ITSM provider configured")
    try:
        ticket = await svc.itsm.get_ticket(key)
    except ItsmError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    # Flows whose ITSM trigger matches this ticket event (any provider).
    flows = await _dispatch_itsm(request, svc.itsm.name, ticket, payload)
    # A flow handles (or already handled) this ticket event.
    started_flows = [f for f in flows if f["status"] in ("started", "duplicate")]
    if not _is_trigger(ticket, list(settings.itsm_trigger_states)):
        return {
            "status": "flows" if started_flows else "ignored",
            "ticket": key,
            "state": ticket.status,
            "reason": f"state {ticket.status!r} is not a trigger state "
            f"{list(settings.itsm_trigger_states)}",
            "flows": flows,
        }
    if started_flows:
        atts = await svc.itsm.list_attachments(key)
        if not any(a.filename.lower().endswith((".xlsx", ".xlsm")) for a in atts):
            # A flow handles this ticket and there is no workbook to push.
            return {"status": "flows", "ticket": key, "state": ticket.status, "flows": flows}
    approves = bool(settings.itsm_trigger_approves)
    site = payload.site or str(
        ticket.fields.get("site") or ticket.fields.get("u_site") or settings.itsm_default_site
    )
    result = await _from_ticket(
        request,
        service,
        SubmitTicket(ticket_number=key, site=site, emergency=payload.emergency),
        f"{svc.itsm.name}-state-webhook",
        pre_approved=approves,
        approval_note=(
            f"approved in {svc.itsm.name}: ticket {key} entered state {ticket.status!r}"
            if approves
            else ""
        ),
    )
    status = "started" if result.get("created") else "exists"
    return {"status": status, "ticket": key, "state": ticket.status, **result, "flows": flows}


async def _dispatch_itsm(
    request: Request, provider: str, ticket: Any, payload: StateEvent
) -> list[dict[str, Any]]:
    dispatcher = getattr(request.app.state, "flow_dispatcher", None)
    if dispatcher is None:
        return []
    event: dict[str, Any] = {
        "source": "itsm",
        "event": payload.event or "state_changed",
        "provider": provider,
        "ticket": ticket.to_dict(),
    }
    if payload.previous_state:
        event["previous"] = {"ticket": {"status": payload.previous_state}}
    return list(await dispatcher.dispatch(event, started_by=f"{provider}-webhook"))


@router.get("/changes/{change_id}/workbook")
async def change_workbook(
    change_id: str,
    request: Request,
    service: ChangeService = Depends(get_service),
    _role: Any = Depends(require_role("viewer")),
) -> Response:
    change = await service.get(change_id)
    if change is None:
        raise HTTPException(status_code=404, detail="change not found")
    row = await _svc(request).latest(change.ticket_number)
    if row is None:
        raise HTTPException(status_code=404, detail="no workbook for this change")
    return Response(
        row.content,
        media_type=XLSX_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{row.filename}"',
            "X-VireoNet-Workbook": row.kind,
        },
    )


@router.get("/itsm/health")
async def itsm_health(
    request: Request, _role: Any = Depends(require_role("viewer"))
) -> dict[str, Any]:
    itsm = getattr(request.app.state, "itsm", None)
    if itsm is None:
        return {"backend": None, "status": "disabled"}
    try:
        return dict(await itsm.health())
    except ItsmError as exc:
        return {"backend": itsm.name, "status": "down", "detail": str(exc)}


@router.get("/itsm/tickets/{key}")
async def itsm_ticket(
    key: str, request: Request, _role: Any = Depends(require_role("viewer"))
) -> dict[str, Any]:
    itsm = getattr(request.app.state, "itsm", None)
    if itsm is None:
        raise HTTPException(status_code=503, detail="no ITSM provider configured")
    try:
        ticket = await itsm.get_ticket(key)
        atts = await itsm.list_attachments(key)
        # Only the demo ITSM exposes its notes; real tools show them natively.
        notes = await itsm.comments(key) if hasattr(itsm, "comments") else None
    except ItsmError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    out: dict[str, Any] = {
        "provider": itsm.name,
        "ticket": ticket.to_dict(),
        "attachments": [a.to_dict() for a in atts],
    }
    if notes is not None:
        out["comments"] = notes
    return out
