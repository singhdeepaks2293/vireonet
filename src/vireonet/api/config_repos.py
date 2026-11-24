from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from vireonet.api.security import CurrentUser, require_role
from vireonet.config_store.layout import device_repo

router = APIRouter()


class ConfigRepoResponse(BaseModel):
    hostname: str
    repo: str
    url: str = ""


class RenderRequest(BaseModel):
    hostname: str
    ticket_number: str
    rendered: str
    vars_snapshot: str
    template_version: str
    metadata: str


@router.get("/config/repos", response_model=list[ConfigRepoResponse])
async def list_device_repos(
    request: Request, _user: CurrentUser = Depends(require_role("viewer"))
) -> list[ConfigRepoResponse]:
    # Device repos are discovered from NetBox (source of truth for devices),
    # not by listing the Git provider — the GitProvider protocol has no list API.
    netbox = request.app.state.adapters.get("netbox")
    if netbox is None:
        raise HTTPException(status_code=503, detail="netbox adapter not configured")
    devices = await netbox.list_devices()
    return [ConfigRepoResponse(hostname=d.name, repo=device_repo(d.name)) for d in devices]


@router.get("/config/repos/{hostname}", response_model=ConfigRepoResponse)
async def get_device_repo(
    hostname: str, request: Request, _user: CurrentUser = Depends(require_role("viewer"))
) -> ConfigRepoResponse:
    return ConfigRepoResponse(hostname=hostname, repo=device_repo(hostname))


@router.post("/config/repos/{hostname}/render", status_code=202)
async def commit_rendered(
    hostname: str,
    payload: RenderRequest,
    request: Request,
    _user: CurrentUser = Depends(require_role("operator")),
) -> dict[str, str]:
    manager = request.app.state.device_repo_manager
    if manager is None:
        raise HTTPException(status_code=503, detail="device repo manager not configured")
    try:
        url = await manager.commit_rendered(
            hostname=hostname,
            ticket_number=payload.ticket_number,
            rendered=payload.rendered,
            vars_snapshot=payload.vars_snapshot,
            template_version=payload.template_version,
            metadata=payload.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"hostname": hostname, "repo": device_repo(hostname), "mr_url": url}


@router.get("/config/repos/{hostname}/history")
async def history(
    hostname: str, request: Request, _user: CurrentUser = Depends(require_role("viewer"))
) -> dict[str, Any]:
    manager = request.app.state.device_repo_manager
    if manager is None:
        raise HTTPException(status_code=503, detail="device repo manager not configured")
    return {
        "hostname": hostname,
        "repo": device_repo(hostname),
        "commits": await manager.history(hostname),
    }


@router.get("/config/devices/{hostname}/history")
async def device_config_history(
    hostname: str, request: Request, _user: CurrentUser = Depends(require_role("viewer"))
) -> dict[str, Any]:
    manager = request.app.state.device_repo_manager
    if manager is None:
        raise HTTPException(status_code=503, detail="device repo manager not configured")
    return {"hostname": hostname, "versions": await manager.history(hostname)}


@router.get("/config/devices/{hostname}/history/compare")
async def compare_device_config_history(
    hostname: str,
    request: Request,
    v_from: int = Query(ge=1),
    v_to: int = Query(ge=1),
    _user: CurrentUser = Depends(require_role("viewer")),
) -> dict[str, Any]:
    manager = request.app.state.device_repo_manager
    if manager is None:
        raise HTTPException(status_code=503, detail="device repo manager not configured")
    try:
        diff = await manager.compare_history(hostname, v_from, v_to)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"hostname": hostname, "from_version": v_from, "to_version": v_to, **diff}


@router.get("/config/devices/{hostname}/commits")
async def config_commits(
    hostname: str,
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    _user: CurrentUser = Depends(require_role("viewer")),
) -> dict[str, Any]:
    """Git history of this device's config on main/master, newest first.

    Used to pick a rollback target: the head is the approved running
    config; any older commit can be restored explicitly.
    """
    manager = getattr(request.app.state, "device_repo_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="device repo manager not configured")
    try:
        commits = await manager.commits(hostname, limit=limit)
    except Exception as exc:  # noqa: BLE001 — Git unreachable / repo missing: say so
        raise HTTPException(status_code=502, detail=f"git history unavailable: {exc}") from exc
    return {
        "hostname": hostname,
        "repo": manager.layout.repo_for(hostname),
        "path": manager.layout.running_path(hostname),
        "branch": manager.layout.branch,
        "commits": [
            {"sha": c.sha, "message": c.message, "author": c.author, "date": c.date}
            for c in commits
        ],
    }
