from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from vireonet.api.security import require_role, verify_webhook
from vireonet.services.changes import ChangeService
from vireonet.temporal.workflow import ChangeWorkflowInput

router = APIRouter()


class CreateChangeRequest(BaseModel):
    intent: dict[str, Any] | None = None
    ticket_number: str | None = None
    sys_id: str | None = None
    emergency: bool = False


class ApprovalSignal(BaseModel):
    approved: bool
    reason: str = ""


class CiSignal(BaseModel):
    passed: bool
    reason: str = ""
    pipeline_url: str = ""


class MergeSignal(BaseModel):
    merged: bool = True


class ChangeResponse(BaseModel):
    id: str
    ticket_number: str
    change_type: str
    state: str
    requester: str
    devices: list[str]
    created_at: str | None = None


class PaginatedChanges(BaseModel):
    items: list[ChangeResponse]
    total: int
    page: int
    page_size: int
    pages: int


def _to_response(change: Any) -> ChangeResponse:
    created = getattr(change, "created_at", None)
    return ChangeResponse(
        id=str(change.id),
        ticket_number=change.ticket_number,
        change_type=change.change_type,
        state=str(change.state.value if hasattr(change.state, "value") else change.state),
        requester=change.requester,
        devices=list(change.devices or []),
        created_at=created.isoformat() if created is not None else None,
    )


def get_service(request: Request) -> ChangeService:
    service = request.app.state.change_service
    if service is None:
        raise HTTPException(status_code=503, detail="change service not configured")
    return cast(ChangeService, service)


def _temporal(request: Request):  # type: ignore[no-untyped-def]
    return request.app.state.temporal_client


@router.post("/changes", status_code=202, response_model=ChangeResponse)
async def create_change(
    payload: CreateChangeRequest, request: Request, service: ChangeService = Depends(get_service)
) -> ChangeResponse:
    await verify_webhook(request, request.app.state.settings)
    actor = "servicenow-webhook"
    if payload.intent is not None:
        change, created = await service.create_from_intent(
            payload.intent, actor=actor, emergency=payload.emergency
        )
    elif payload.ticket_number or payload.sys_id:
        change, created = await service.create_from_servicenow(
            payload.ticket_number or "", actor=actor, sys_id=payload.sys_id
        )
    else:
        raise HTTPException(status_code=422, detail="provide intent or ticket_number/sys_id")
    if (
        created
        and str(change.state.value if hasattr(change.state, "value") else change.state)
        == "validated"
    ):
        client = _temporal(request)
        if client is not None:
            await client.start_change(
                ChangeWorkflowInput(
                    change_id=str(change.id),
                    ticket_number=change.ticket_number,
                    intent=change.intent,
                    requester=change.requester,
                )
            )
    return _to_response(change)


@router.get("/changes", response_model=PaginatedChanges)
async def list_changes(
    state: str | None = Query(default=None),
    change_type: str | None = Query(default=None),
    device: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    sort_by: str = Query(default="created_at"),
    sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    service: ChangeService = Depends(get_service),
    _role: str = Depends(require_role("viewer")),
) -> PaginatedChanges:
    items, total = await service.list(
        state=state,
        change_type=change_type,
        device=device,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    pages = (total + page_size - 1) // page_size if total else 0
    return PaginatedChanges(
        items=[_to_response(c) for c in items],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@router.get("/changes/{change_id}", response_model=ChangeResponse)
async def get_change(
    change_id: str,
    service: ChangeService = Depends(get_service),
    _role: str = Depends(require_role("viewer")),
) -> ChangeResponse:
    change = await service.get(change_id)
    if change is None:
        raise HTTPException(status_code=404, detail="change not found")
    return _to_response(change)


async def _signal(
    request: Request, change_id: str, kind: str, payload: BaseModel, service: ChangeService
) -> dict[str, str]:
    change = await service.get(change_id)
    if change is None:
        raise HTTPException(status_code=404, detail="change not found")
    client = _temporal(request)
    if client is None:
        raise HTTPException(status_code=503, detail="temporal client not configured")
    ticket = change.ticket_number
    if kind == "approval":
        assert isinstance(payload, ApprovalSignal)
        if payload.approved:
            await client.signal_approve(ticket, payload.reason)
        else:
            await client.signal_reject(ticket, payload.reason)
    elif kind == "ci":
        assert isinstance(payload, CiSignal)
        await client.signal_ci(ticket, payload.passed, payload.reason)
    elif kind == "merge":
        await client.signal_merge(ticket)
    return {"ticket": ticket, "signal": kind, "status": "accepted"}


@router.post("/changes/{change_id}/signals/approval", status_code=202)
async def signal_approval(
    change_id: str,
    payload: ApprovalSignal,
    request: Request,
    service: ChangeService = Depends(get_service),
    _role: str = Depends(require_role("approver")),
) -> dict[str, str]:
    return await _signal(request, change_id, "approval", payload, service)


@router.post("/changes/{change_id}/signals/ci", status_code=202)
async def signal_ci(
    change_id: str,
    payload: CiSignal,
    request: Request,
    service: ChangeService = Depends(get_service),
) -> dict[str, str]:
    await verify_webhook(request, request.app.state.settings)
    return await _signal(request, change_id, "ci", payload, service)


@router.post("/changes/{change_id}/signals/merge", status_code=202)
async def signal_merge(
    change_id: str,
    payload: MergeSignal,
    request: Request,
    service: ChangeService = Depends(get_service),
) -> dict[str, str]:
    await verify_webhook(request, request.app.state.settings)
    return await _signal(request, change_id, "merge", payload, service)


class TransitionResponse(BaseModel):
    from_state: str
    to_state: str
    actor: str
    reason: str
    evidence: dict[str, Any]
    created_at: str | None = None


class DiffLineResponse(BaseModel):
    old_line: int | None = None
    new_line: int | None = None
    type: str
    text: str


class ChangeDetailResponse(BaseModel):
    change: ChangeResponse
    intent: dict[str, Any]
    diff: list[DiffLineResponse]
    diff_summary: list[dict[str, Any]] = []
    checks: list[dict[str, Any]]
    batfish: dict[str, Any]
    awx_jobs: list[dict[str, Any]]
    transitions: list[TransitionResponse]
    # E5 execution provenance, derived from stored data only (see
    # ChangeService.get_detail): job_type defaults to "playbook" when the
    # change record/intent stores none; the rest are empty/None when absent.
    job_type: str = "playbook"
    awx_job_id: int | None = None
    awx_job_url: str | None = None
    script_name: str | None = None
    script_sha: str | None = None
    declared_changes: list[dict[str, Any]] = []
    reconciliation_status: str | None = None
    drift_count: int = 0


@router.get("/changes/{change_id}/detail", response_model=ChangeDetailResponse)
async def get_change_detail(
    change_id: str,
    request: Request,
    service: ChangeService = Depends(get_service),
    _role: str = Depends(require_role("viewer")),
) -> ChangeDetailResponse:
    detail = await service.get_detail(change_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="change not found")
    change = detail["change"]
    awx_job_id: int | None = None
    for job in detail["awx_jobs"]:
        if job.get("job_id") is not None:
            awx_job_id = int(job["job_id"])
            break
    # The AWX job URL is built from the configured AWX base URL; without
    # one there is no URL to link to, so it stays None.
    settings = request.app.state.settings
    awx_base_url = str(getattr(settings, "awx_base_url", "") or "").rstrip("/")
    awx_job_url = (
        f"{awx_base_url}/#/jobs/playbook/{awx_job_id}" if awx_base_url and awx_job_id else None
    )
    return ChangeDetailResponse(
        change=_to_response(change),
        intent=detail["intent"],
        diff=[DiffLineResponse(**d) for d in detail["diff"]],
        diff_summary=detail.get("diff_summary", []),
        checks=detail["checks"],
        batfish=detail["batfish"],
        awx_jobs=detail["awx_jobs"],
        transitions=[TransitionResponse(**t) for t in detail["transitions"]],
        job_type=str(detail.get("job_type", "playbook")),
        awx_job_id=awx_job_id,
        awx_job_url=awx_job_url,
        script_name=detail.get("script_name"),
        script_sha=detail.get("script_sha"),
        declared_changes=list(detail.get("declared_changes", [])),
        reconciliation_status=detail.get("reconciliation_status"),
        drift_count=int(detail.get("drift_count", 0)),
    )


@router.post("/changes/{change_id}/collect-before", status_code=200)
async def collect_before_change(
    change_id: str,
    service: ChangeService = Depends(get_service),
    user: Any = Depends(require_role("operator")),
) -> dict[str, Any]:
    """Capture running config and persist real candidate diffs for review."""
    try:
        results = await service.collect_before_change(change_id, actor=user.username)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — collection failure must be visible
        raise HTTPException(status_code=502, detail=f"pre-change collection failed: {exc}") from exc
    return {"change_id": change_id, "devices": results, "status": "collected"}


class ReconcileOverrideRequest(BaseModel):
    reason: str


class ReconcileOverrideResponse(BaseModel):
    id: str
    ticket_number: str
    state: str
    status: str


@router.post(
    "/changes/{change_id}/reconcile-override",
    status_code=200,
    response_model=ReconcileOverrideResponse,
)
async def reconcile_override(
    change_id: str,
    payload: ReconcileOverrideRequest,
    service: ChangeService = Depends(get_service),
    user: Any = Depends(require_role("approver")),
) -> ReconcileOverrideResponse:
    """Approver override for a change stuck in reconciliation_pending.

    Records the override in the audit trail (two transitions,
    reconciliation_pending -> reconciling -> reconciled, both carrying the
    reason) and leaves the change reconciled and close-eligible. The
    Temporal workflow has already returned by this point; this endpoint
    does not re-run it. Requires a reason.
    """
    if not payload.reason.strip():
        raise HTTPException(status_code=422, detail="reconcile override requires a reason")
    try:
        change = await service.reconcile_override(
            change_id, actor=user.username, reason=payload.reason
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if change is None:
        raise HTTPException(status_code=404, detail="change not found")
    return ReconcileOverrideResponse(
        id=str(change.id),
        ticket_number=change.ticket_number,
        state=str(change.state.value if hasattr(change.state, "value") else change.state),
        status="override-recorded",
    )


@router.post("/changes/{change_id}/retry-close", status_code=200, response_model=ReconcileOverrideResponse)
async def retry_close(
    change_id: str,
    request: Request,
    service: ChangeService = Depends(get_service),
    user: Any = Depends(require_role("approver")),
) -> ReconcileOverrideResponse:
    """Retry ServiceNow/NetBox closure after an earlier external failure."""
    try:
        change = await service.retry_close(
            change_id, actor=user.username, reason="operator-approved closure retry"
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — preserve closure_pending and surface retry failure
        raise HTTPException(status_code=502, detail=f"external closure retry failed: {exc}") from exc
    if change is None:
        raise HTTPException(status_code=404, detail="change not found")
    return ReconcileOverrideResponse(
        id=str(change.id),
        ticket_number=change.ticket_number,
        state=str(change.state.value if hasattr(change.state, "value") else change.state),
        status="closed",
    )


async def signal_rollback(
    change_id: str,
    request: Request,
    service: ChangeService = Depends(get_service),
    _role: str = Depends(require_role("operator")),
) -> dict[str, str]:
    try:
        change = await service.rollback(
            change_id, actor="api-rollback", reason="manual rollback from UI"
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if change is None:
        raise HTTPException(status_code=404, detail="change not found")
    # The Temporal workflow also rolls back on post-check failure; this endpoint
    # records the manual rollback in the audit trail. The AWX rollback job itself
    # is launched by the workflow's rollback activity (M4).
    return {
        "ticket": change.ticket_number,
        "signal": "rollback",
        "status": "accepted",
        "state": str(change.state.value if hasattr(change.state, "value") else change.state),
    }
