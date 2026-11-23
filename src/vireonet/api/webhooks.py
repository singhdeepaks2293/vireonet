from __future__ import annotations

import hmac

from fastapi import APIRouter, Header, HTTPException, Request

from vireonet.api.security import verify_webhook
from vireonet.structlog_config import get_logger


async def _handle_drift_mr(request: Request, branch: str, merged: bool) -> None:
    """Drift MR signal (D4): a drift/<device>/<short> branch that merged
    (or closed unmerged) resolves (or reopens) the matching drift record.

    Best-effort and never fatal to the webhook: a drift failure must not
    break the normal change signal path. No-op for non-drift branches.
    """
    if not branch.startswith("drift/"):
        return
    try:
        from vireonet.drift.collector import DriftCollector
        from vireonet.drift.resolve import handle_mr_merged

        sessions = getattr(request.app.state, "session_factory", None)
        if sessions is None:
            return
        engine = getattr(request.app.state, "execution_engine", None)
        locks = getattr(request.app.state, "lock_service", None)
        collector = None
        if engine and locks:
            drift_collector = DriftCollector(engine, locks)

            async def collect(device: str) -> str:
                return await drift_collector.collect(device, "", "", "drift-mr-merged")

            collector = collect
        settings = request.app.state.settings
        await handle_mr_merged(
            sessions,
            branch=branch,
            merged=merged,
            config_store=getattr(request.app.state, "config_store", None),
            device_repos=getattr(request.app.state, "device_repo_manager", None),
            collector=collector,
            notifier=(getattr(request.app.state, "adapters", {}) or {}).get("notification"),
            channel=settings.notification_default_channel,
        )
    except Exception as exc:  # noqa: BLE001 - drift webhook handling must not fail the webhook
        get_logger(__name__).warning("drift MR webhook handling failed", error=str(exc))


router = APIRouter()


def _verify_provider_token(request: Request, header: str) -> None:
    """Plain shared-token check for a Git provider webhook.

    The expected token is settings.gitlab_webhook_token (the single
    configured Git webhook token). No token configured is fail-closed:
    only an explicit dev flag allows the call through (with a warning).
    """
    settings = request.app.state.settings
    expected = settings.gitlab_webhook_token
    if not expected:
        if settings.dev:
            get_logger(__name__).warning("git webhook called with no token configured (dev mode)")
            return
        raise HTTPException(
            status_code=401, detail="webhook authentication required: no secret configured"
        )
    if not hmac.compare_digest(request.headers.get(header, ""), expected):
        raise HTTPException(status_code=401, detail="bad GitLab webhook token")


@router.post("/webhooks/gitlab")
async def gitlab_webhook(request: Request) -> dict[str, str]:
    _verify_provider_token(request, "X-Gitlab-Token")
    payload = await request.json()
    kind = str(payload.get("object_kind", ""))
    temporal = request.app.state.temporal_client
    # Pipeline event: signal CI pass/fail to the workflow (Workflow ID = ticket).
    # The ticket is carried in the MR title / pipeline variables as `ticket`.
    # If the payload has no ticket, we cannot route it — return 202 and log it.
    if kind == "pipeline":
        status = str(payload.get("object_attributes", {}).get("status", ""))
        ticket = str(
            payload.get("object_attributes", {}).get("variables", [{}])[0].get("value", "")
            if payload.get("object_attributes", {}).get("variables")
            else ""
        )
        # Fallback: ticket from the MR title is resolved by the caller in M5
        # via the `ticket` variable on the pipeline; if absent, do nothing.
        if not ticket:
            return {"status": "ignored", "reason": "no ticket in pipeline payload"}
        if temporal is not None:
            await temporal.signal_ci(ticket, status == "success", f"pipeline {status}")
        return {"status": "accepted", "signal": "ci"}
    if kind == "merge_request":
        attrs = payload.get("object_attributes", {})
        if str(attrs.get("state")) == "merged":
            # Ticket is the MR title prefix `RITM...:` or the source branch `change/RITM...`.
            title = str(attrs.get("title", ""))
            ticket = title.split(":")[0].strip() if ":" in title else ""
            await _handle_drift_mr(request, str(attrs.get("source_branch", "")), merged=True)
            if ticket and temporal is not None:
                await temporal.signal_merge(ticket)
            return {"status": "accepted", "signal": "merge"}
        if str(attrs.get("state")) == "closed":
            await _handle_drift_mr(request, str(attrs.get("source_branch", "")), merged=False)
        return {"status": "ignored", "reason": "merge_request not merged"}
    return {"status": "ignored", "reason": f"unknown object_kind {kind}"}


def _ticket_from_title(title: str) -> str | None:
    """Titles are "RITM0001234: Add VLAN 100". Return the ticket prefix or None."""
    import re

    m = re.match(r"(RITM\d+|CHG\d+|ZTP-[A-Z0-9]+)", title)
    return m.group(1) if m else None


@router.post("/webhooks/github")
async def github_webhook(
    request: Request,
    x_github_event: str | None = Header(default=None),
) -> dict[str, str]:
    await verify_webhook(request, request.app.state.settings)
    payload = await request.json()
    temporal = request.app.state.temporal_client
    if x_github_event in {"check_run", "check_suite"}:
        run = payload.get("check_run") or payload.get("check_suite") or {}
        passed = run.get("conclusion") == "success"
        pr = run.get("pull_request") or {}
        ticket = _ticket_from_title(str(pr.get("title", "")))
        if ticket and temporal is not None:
            await temporal.signal_ci(ticket, passed, "github check run")
    elif (
        x_github_event == "pull_request"
        and payload.get("action") == "closed"
        and payload.get("pull_request", {}).get("merged")
    ):
        pr = payload.get("pull_request", {})
        await _handle_drift_mr(request, str(pr.get("head", {}).get("ref", "")), merged=True)
        ticket = _ticket_from_title(str(payload["pull_request"].get("title", "")))
        if ticket and temporal is not None:
            await temporal.signal_merge(ticket)
    elif x_github_event == "pull_request" and payload.get("action") == "closed":
        pr = payload.get("pull_request", {})
        await _handle_drift_mr(request, str(pr.get("head", {}).get("ref", "")), merged=False)
    return {"status": "accepted", "provider": "github"}


@router.post("/webhooks/bitbucket")
async def bitbucket_webhook(
    request: Request,
    x_event_key: str | None = Header(default=None),
) -> dict[str, str]:
    await verify_webhook(request, request.app.state.settings)
    payload = await request.json()
    temporal = request.app.state.temporal_client
    if x_event_key == "repo:commit_status_created":
        state = str(payload.get("commit_status", {}).get("state", ""))
        passed = state == "SUCCESSFUL"
        # Commit-status webhooks carry no PR title; the ticket is parsed from
        # the commit message, which must start with the ticket number.
        message = str(payload.get("commit_status", {}).get("commit", {}).get("message", ""))
        ticket = _ticket_from_title(message)
        if ticket and temporal is not None:
            await temporal.signal_ci(ticket, passed, "bitbucket commit status")
    elif x_event_key == "pullrequest:fulfilled":
        ticket = _ticket_from_title(str(payload.get("pullrequest", {}).get("title", "")))
        if ticket and temporal is not None:
            await temporal.signal_merge(ticket)
    return {"status": "accepted", "provider": "bitbucket"}


@router.post("/webhooks/gitea")
async def gitea_webhook(
    request: Request,
    x_gitea_event: str | None = Header(default=None),
) -> dict[str, str]:
    await verify_webhook(request, request.app.state.settings)
    payload = await request.json()
    temporal = request.app.state.temporal_client
    if x_gitea_event == "workflow_run":
        # UNCERTAIN: Gitea's workflow_run payload follows GitHub's, but the
        # conclusion field is not guaranteed in older Gitea.
        conclusion = str(payload.get("workflow_run", {}).get("conclusion", ""))
        passed = conclusion == "success"
        ticket = _ticket_from_title(str(payload.get("workflow_run", {}).get("head_branch", "")))
        if ticket and temporal is not None:
            await temporal.signal_ci(ticket, passed, "gitea workflow run")
    elif (
        x_gitea_event == "pull_request"
        and payload.get("action") == "closed"
        and payload.get("pull_request", {}).get("merged")
    ):
        pr = payload.get("pull_request", {})
        await _handle_drift_mr(request, str(pr.get("head", {}).get("ref", "")), merged=True)
        ticket = _ticket_from_title(str(payload["pull_request"].get("title", "")))
        if ticket and temporal is not None:
            await temporal.signal_merge(ticket)
    elif x_gitea_event == "pull_request" and payload.get("action") == "closed":
        pr = payload.get("pull_request", {})
        await _handle_drift_mr(request, str(pr.get("head", {}).get("ref", "")), merged=False)
    return {"status": "accepted", "provider": "gitea"}
