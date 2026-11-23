from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from vireonet.api.security import CurrentUser, require_role
from vireonet.setup.gitea_setup import GiteaSetupService

router = APIRouter()

MANUAL_STEPS = [
    "1. Install Docker and Docker Compose v2 on the host.",
    "2. Set GITEA_DB_PASSWORD (strong, 16+ chars) and GITEA_ROOT_URL (e.g. http://localhost:3000/).",
    "3. Run: GITEA_DB_PASSWORD='<password>' ./scripts/install_gitea.sh",
    "4. Open http://localhost:3000/ and create the admin user.",
    "5. Create the repo netops/intent.",
    "6. Create an API token (Settings -> Applications -> Generate New Token, scope repo + admin).",
    "7. Store the token in Vault: vault kv put secret/git token='<token>'.",
    "8. Set VIREONET_GIT_PROVIDER=gitea, VIREONET_GIT_BASE_URL=http://localhost:3000, VIREONET_GIT_REPO=netops/intent.",
    "9. Restart VireoNet API + worker, then verify with GET /adapters/health.",
    "10. Add the webhook: repo Settings -> Webhooks, URL https://vireonet.example.com/webhooks/gitea, events pull_request + workflow_run.",
]


class InstallRequest(BaseModel):
    admin_user: str = "vireonet-admin"
    repo: str = "netops/intent"
    base_url: str = "http://localhost:3000"


class JobResponse(BaseModel):
    id: str
    status: str
    steps: list[dict[str, str]] = []
    error: str = ""


def _svc(request: Request) -> GiteaSetupService:
    svc = getattr(request.app.state, "gitea_setup_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="gitea setup service not configured")
    return cast(GiteaSetupService, svc)


@router.get("/setup/gitea/manual")
async def manual_steps(_admin: CurrentUser = Depends(require_role("admin"))) -> dict[str, Any]:
    return {"steps": MANUAL_STEPS}


@router.post("/setup/gitea/install", status_code=202, response_model=JobResponse)
async def install(
    payload: InstallRequest, request: Request, _admin: CurrentUser = Depends(require_role("admin"))
) -> JobResponse:
    job = _svc(request).start(
        admin_user=payload.admin_user, repo=payload.repo, base_url=payload.base_url
    )
    return JobResponse(id=job.id, status=job.status, steps=job.steps)


@router.get("/setup/gitea/status/{job_id}", response_model=JobResponse)
async def status(
    job_id: str, request: Request, _admin: CurrentUser = Depends(require_role("admin"))
) -> JobResponse:
    job = _svc(request).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="setup job not found")
    return JobResponse(id=job.id, status=job.status, steps=job.steps, error=job.error)
