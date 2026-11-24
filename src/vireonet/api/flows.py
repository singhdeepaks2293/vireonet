"""Workflow builder API: flows, versions, runs, approvals, triggers, webhooks.

Roles: viewers read; operators build, test and run flows; approvers publish
and enable triggers (a trigger runs without anyone clicking, so turning one
on is a separate, more trusted decision) and answer approval steps that ask
for them; admins save custom-script steps, delete flows and rotate tokens.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from vireonet.api.security import CurrentUser, require_role
from vireonet.flows.catalog import TRIGGERS, catalog
from vireonet.flows.dispatch import FlowDispatcher
from vireonet.flows.engine import Interpreter, MemoryRuntime
from vireonet.flows.examples import EXAMPLES, example
from vireonet.flows.schema import (
    FlowValidationError,
    normalise,
    uses_scripts,
    validate_definition,
    validate_trigger,
)
from vireonet.flows.sources import NORMALISERS
from vireonet.flows.sources import normalise as normalise_event
from vireonet.flows.store import EXPORT_FORMAT, FlowNotFound, FlowStore
from vireonet.flows.triggers import match
from vireonet.iam.permissions import can

router = APIRouter(tags=["flows"])


class FlowBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    trigger: dict[str, Any] = Field(default_factory=lambda: {"type": "manual"})
    definition: dict[str, Any] = Field(default_factory=lambda: {"steps": []})
    comment: str = ""


class FlowUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    trigger: dict[str, Any] | None = None
    definition: dict[str, Any] | None = None
    comment: str = ""


class RunBody(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)
    version: int | None = None


class Decision(BaseModel):
    approved: bool
    comment: str = ""


class ValidateBody(BaseModel):
    trigger: dict[str, Any] | None = None
    definition: dict[str, Any] = Field(default_factory=dict)


class TriggerTest(BaseModel):
    trigger: dict[str, Any]
    event: dict[str, Any] | None = None


class SimulateBody(BaseModel):
    definition: dict[str, Any]
    trigger_event: dict[str, Any] = Field(default_factory=dict)
    inputs: dict[str, Any] = Field(default_factory=dict)
    approve_all: bool = True


class ImportBody(BaseModel):
    data: dict[str, Any]
    name: str | None = None


def _store(request: Request) -> FlowStore:
    store = getattr(request.app.state, "flow_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="flows are not available")
    return store  # type: ignore[no-any-return]


def _dispatcher(request: Request) -> FlowDispatcher:
    d = getattr(request.app.state, "flow_dispatcher", None)
    if d is None:
        raise HTTPException(status_code=503, detail="flows are not available")
    return d  # type: ignore[no-any-return]


def _bad(exc: FlowValidationError) -> HTTPException:
    return HTTPException(status_code=422, detail={"errors": exc.errors})


async def _sync_schedule(request: Request, flow_id: str) -> str:
    """Create/update/delete the Temporal schedule for a schedule-triggered flow."""
    client = getattr(request.app.state, "temporal_client", None)
    if client is None or not hasattr(client, "upsert_flow_schedule"):
        return "no temporal"
    flow = await _store(request).get(flow_id)
    version = flow["published_version"]
    trig = (await _store(request).get(flow_id, version))["trigger"] if version else flow["trigger"]
    try:
        return str(
            await client.upsert_flow_schedule(flow_id, trig, bool(flow["enabled"] and version))
        )
    except Exception as exc:  # the flow is saved; report the schedule problem
        return f"schedule not updated: {exc}"


def _webhook_url(request: Request, flow_id: str) -> str:
    return f"/webhooks/flows/{flow_id}"


# --- catalog, validation, testing ----------------------------------------------------------


@router.get("/flows/catalog")
async def get_catalog(_u: CurrentUser = Depends(require_role("viewer"))) -> dict[str, Any]:
    return {
        **catalog(),
        "examples": [
            {
                "key": k,
                "name": v["name"],
                "description": v["description"],
                "trigger_type": v["trigger"]["type"],
            }
            for k, v in EXAMPLES.items()
        ],
        "sources": sorted(NORMALISERS),
    }


@router.post("/flows/validate")
async def validate(
    body: ValidateBody, user: CurrentUser = Depends(require_role("viewer"))
) -> dict[str, Any]:
    d = normalise(body.definition)
    errors = validate_trigger(body.trigger) + validate_definition(d, allow_scripts=True)
    warnings = []
    if uses_scripts(d) and not can(user.role, "admin"):
        warnings.append("this flow has custom-script steps: only an admin can save it")
    return {"valid": not errors, "errors": errors, "warnings": warnings, "definition": d}


@router.post("/flows/test-trigger")
async def test_trigger(
    body: TriggerTest, _u: CurrentUser = Depends(require_role("viewer"))
) -> dict[str, Any]:
    """Like Power Automate's 'test with sample data': does this event start the flow, and what does it capture?"""
    errors = validate_trigger(body.trigger)
    if errors:
        return {"valid": False, "errors": errors, "matched": False}
    event = (
        body.event
        if body.event is not None
        else TRIGGERS.get(str(body.trigger.get("type")), {}).get("event", {})
    )
    m = match(body.trigger, event)
    return {
        "valid": True,
        "errors": [],
        "matched": m.matched,
        "reason": m.reason,
        "captures": m.captures,
        "dedup_key": m.dedup_key,
        "event": event,
    }


@router.post("/flows/simulate")
async def simulate(
    body: SimulateBody, _u: CurrentUser = Depends(require_role("operator"))
) -> dict[str, Any]:
    """Run the flow logic in memory: control flow and expressions are real,
    every action (API, script, devices, ITSM, changes) is simulated."""
    d = normalise(body.definition)
    errors = validate_definition(d, allow_scripts=True)
    if errors:
        return {"valid": False, "errors": errors}

    class _Approve(dict[str, Any]):
        def get(self, key: str, default: Any = None) -> Any:
            return {"approved": body.approve_all, "by": "simulation"}

    rt = MemoryRuntime(approvals=_Approve())
    result = await Interpreter(rt).run(
        d,
        trigger=body.trigger_event,
        inputs=body.inputs,
        run={"id": "simulation", "flow": "simulation"},
    )
    return {"valid": True, "errors": [], "result": result, "log": rt.log, "waits": rt.slept}


# --- flows ------------------------------------------------------------------------------------------


@router.get("/flows")
async def list_flows(
    request: Request, _u: CurrentUser = Depends(require_role("viewer"))
) -> dict[str, Any]:
    return {"items": await _store(request).list_flows()}


@router.post("/flows", status_code=201)
async def create_flow(
    body: FlowBody, request: Request, user: CurrentUser = Depends(require_role("operator"))
) -> dict[str, Any]:
    try:
        flow, token = await _store(request).create(
            name=body.name,
            description=body.description,
            trigger=body.trigger,
            definition=body.definition,
            actor=user.username,
            allow_scripts=can(user.role, "admin"),
            comment=body.comment or "created",
        )
    except FlowValidationError as exc:
        raise _bad(exc) from exc
    return {**flow, "webhook_url": _webhook_url(request, flow["id"]), "webhook_token": token}


@router.get("/flows/runs")
async def list_runs(
    request: Request,
    flow_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    _u: CurrentUser = Depends(require_role("viewer")),
) -> dict[str, Any]:
    try:
        return {"items": await _store(request).list_runs(flow_id, limit)}
    except FlowNotFound as exc:
        raise HTTPException(status_code=404, detail="flow not found") from exc


@router.get("/flows/runs/{run_id}")
async def get_run(
    run_id: str, request: Request, _u: CurrentUser = Depends(require_role("viewer"))
) -> dict[str, Any]:
    try:
        return await _store(request).get_run(run_id)
    except FlowNotFound as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@router.post("/flows/runs/{run_id}/decide/{step_id}")
async def decide(
    run_id: str,
    step_id: str,
    body: Decision,
    request: Request,
    user: CurrentUser = Depends(require_role("operator")),
) -> dict[str, Any]:
    try:
        run = await _store(request).get_run(run_id)
    except FlowNotFound as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    waiting = (run.get("waiting") or {}).get(step_id)
    if not waiting:
        raise HTTPException(status_code=409, detail=f"step {step_id} is not waiting for a decision")
    needed = str(waiting.get("role") or "approver")
    if not can(user.role, needed):
        raise HTTPException(status_code=403, detail=f"{needed} role required for this approval")
    if (
        waiting.get("requested_by")
        and waiting["requested_by"] == user.username
        and needed != "operator"
    ):
        raise HTTPException(status_code=403, detail="you cannot approve your own request")
    await request.app.state.temporal_client.signal_flow(
        run_id, "decide", step_id, body.approved, user.username, body.comment
    )
    return {"run_id": run_id, "step_id": step_id, "approved": body.approved, "by": user.username}


@router.post("/flows/runs/{run_id}/cancel")
async def cancel_run(
    run_id: str, request: Request, user: CurrentUser = Depends(require_role("operator"))
) -> dict[str, Any]:
    try:
        run = await _store(request).get_run(run_id)
    except FlowNotFound as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    if run["status"] in ("succeeded", "failed", "cancelled"):
        raise HTTPException(status_code=409, detail=f"the run already {run['status']}")
    await request.app.state.temporal_client.signal_flow(run_id, "cancel", user.username)
    return {"run_id": run_id, "cancel_requested": True}


@router.get("/flows/examples/{key}")
async def get_example(
    key: str, _u: CurrentUser = Depends(require_role("viewer"))
) -> dict[str, Any]:
    if key not in EXAMPLES:
        raise HTTPException(status_code=404, detail="no such example")
    return example(key)


@router.post("/flows/import", status_code=201)
async def import_flow(
    body: ImportBody, request: Request, user: CurrentUser = Depends(require_role("operator"))
) -> dict[str, Any]:
    data = body.data
    if data.get("format") != EXPORT_FORMAT:
        raise HTTPException(
            status_code=422, detail=f"not a VireoNet flow export (format must be {EXPORT_FORMAT})"
        )
    created = await create_flow(
        FlowBody(
            name=body.name or str(data.get("name", "")),
            description=str(data.get("description", "")),
            trigger=data.get("trigger") or {"type": "manual"},
            definition=data.get("definition") or {},
            comment="imported",
        ),
        request,
        user,
    )
    return created


@router.get("/flows/{flow_id}")
async def get_flow(
    flow_id: str,
    request: Request,
    version: int | None = Query(default=None),
    _u: CurrentUser = Depends(require_role("viewer")),
) -> dict[str, Any]:
    try:
        flow = await _store(request).get(flow_id, version)
    except FlowNotFound as exc:
        raise HTTPException(status_code=404, detail="flow not found") from exc
    return {**flow, "webhook_url": _webhook_url(request, flow_id)}


@router.put("/flows/{flow_id}")
async def update_flow(
    flow_id: str,
    body: FlowUpdate,
    request: Request,
    user: CurrentUser = Depends(require_role("operator")),
) -> dict[str, Any]:
    try:
        flow = await _store(request).update(
            flow_id,
            actor=user.username,
            allow_scripts=can(user.role, "admin"),
            name=body.name,
            description=body.description,
            trigger=body.trigger,
            definition=body.definition,
            comment=body.comment,
        )
    except FlowValidationError as exc:
        raise _bad(exc) from exc
    except FlowNotFound as exc:
        raise HTTPException(status_code=404, detail="flow not found") from exc
    return flow


@router.delete("/flows/{flow_id}", status_code=204)
async def delete_flow(
    flow_id: str, request: Request, _u: CurrentUser = Depends(require_role("admin"))
) -> None:
    store = _store(request)
    try:
        await store.set_enabled(flow_id, False)
        await _sync_schedule(request, flow_id)
        await store.delete(flow_id)
    except FlowNotFound as exc:
        raise HTTPException(status_code=404, detail="flow not found") from exc


@router.get("/flows/{flow_id}/versions")
async def versions(
    flow_id: str, request: Request, _u: CurrentUser = Depends(require_role("viewer"))
) -> dict[str, Any]:
    try:
        return {"items": await _store(request).versions(flow_id)}
    except FlowNotFound as exc:
        raise HTTPException(status_code=404, detail="flow not found") from exc


@router.post("/flows/{flow_id}/publish")
async def publish(
    flow_id: str,
    request: Request,
    version: int | None = Query(default=None),
    _u: CurrentUser = Depends(require_role("approver")),
) -> dict[str, Any]:
    try:
        flow = await _store(request).publish(flow_id, version)
    except FlowNotFound as exc:
        raise HTTPException(status_code=404, detail="flow or version not found") from exc
    return {**flow, "schedule": await _sync_schedule(request, flow_id)}


@router.post("/flows/{flow_id}/enable")
async def enable(
    flow_id: str,
    request: Request,
    enabled: bool = Query(default=True),
    _u: CurrentUser = Depends(require_role("approver")),
) -> dict[str, Any]:
    try:
        flow = await _store(request).set_enabled(flow_id, enabled)
    except FlowValidationError as exc:
        raise HTTPException(status_code=409, detail=exc.errors[0]) from exc
    except FlowNotFound as exc:
        raise HTTPException(status_code=404, detail="flow not found") from exc
    return {**flow, "schedule": await _sync_schedule(request, flow_id)}


@router.post("/flows/{flow_id}/run", status_code=202)
async def run_flow(
    flow_id: str,
    body: RunBody,
    request: Request,
    user: CurrentUser = Depends(require_role("operator")),
) -> dict[str, Any]:
    try:
        return await _dispatcher(request).run_manual(
            flow_id, inputs=body.inputs, user=user.username, version=body.version
        )
    except FlowNotFound as exc:
        raise HTTPException(status_code=404, detail="flow or version not found") from exc


@router.get("/flows/{flow_id}/export")
async def export(
    flow_id: str, request: Request, _u: CurrentUser = Depends(require_role("viewer"))
) -> dict[str, Any]:
    try:
        return _store(request).export(await _store(request).get(flow_id))
    except FlowNotFound as exc:
        raise HTTPException(status_code=404, detail="flow not found") from exc


@router.post("/flows/{flow_id}/token")
async def rotate_token(
    flow_id: str, request: Request, _u: CurrentUser = Depends(require_role("admin"))
) -> dict[str, Any]:
    try:
        token = await _store(request).rotate_token(flow_id)
    except FlowNotFound as exc:
        raise HTTPException(status_code=404, detail="flow not found") from exc
    return {"webhook_url": _webhook_url(request, flow_id), "webhook_token": token}


# --- inbound events -------------------------------------------------------------------------------


def _event_auth(request: Request, body: bytes) -> None:
    """Adapter event webhooks: Bearer / X-VireoNet-Event-Token, or HMAC
    (X-VireoNet-Signature sha256, or NetBox's X-Hook-Signature sha512)."""
    settings = request.app.state.settings
    secret = settings.flow_event_token or settings.webhook_secret
    if not secret:
        if settings.dev:
            return
        raise HTTPException(
            status_code=401, detail="set VIREONET_FLOW_EVENT_TOKEN to accept adapter events"
        )
    h = request.headers
    token = (
        h.get("X-VireoNet-Event-Token")
        or h.get("Authorization", "").removeprefix("Bearer ").strip()
    )
    if token and hmac.compare_digest(token.encode(), secret.encode()):
        return
    sig256 = h.get("X-VireoNet-Signature", "").removeprefix("sha256=")
    if sig256 and hmac.compare_digest(
        sig256, hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    ):
        return
    sig512 = h.get("X-Hook-Signature", "")
    if sig512 and hmac.compare_digest(
        sig512, hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
    ):
        return
    raise HTTPException(status_code=401, detail="bad or missing event token")


@router.post("/webhooks/flows/source/{source}", status_code=202)
async def adapter_event(source: str, request: Request) -> dict[str, Any]:
    raw = await request.body()
    _event_auth(request, raw)
    if source not in NORMALISERS:
        raise HTTPException(
            status_code=404,
            detail=f"unknown source {source!r} (one of: {', '.join(sorted(NORMALISERS))})",
        )
    try:
        body = await request.json()
        events = normalise_event(source, body, dict(request.headers))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    d = _dispatcher(request)
    results = []
    for e in events:
        results.append(
            {"event": e.get("event"), "flows": await d.dispatch(e, started_by=f"{source}-webhook")}
        )
    return {"source": source, "events": len(events), "results": results}


@router.post("/webhooks/flows/{flow_id}", status_code=202)
async def flow_webhook(
    flow_id: str, request: Request, x_vireonet_flow_token: str = Header(default="")
) -> dict[str, Any]:
    """A flow's own webhook. The token was shown when the flow was created."""
    store = _store(request)
    token = (
        x_vireonet_flow_token
        or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    )
    try:
        if not await store.check_token(flow_id, token):
            raise HTTPException(status_code=401, detail="bad or missing X-VireoNet-Flow-Token")
    except FlowNotFound as exc:
        raise HTTPException(status_code=404, detail="flow not found") from exc
    try:
        body = await request.json()
    except ValueError:
        body = {"text": (await request.body()).decode(errors="replace")[:100_000]}
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in ("authorization", "x-vireonet-flow-token", "cookie")
    }
    return await _dispatcher(request).run_webhook(flow_id, body, headers)
