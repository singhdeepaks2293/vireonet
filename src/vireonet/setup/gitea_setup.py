from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field


@dataclass
class SetupJob:
    id: str
    status: str  # running | done | failed
    steps: list[dict[str, str]] = field(default_factory=list)
    error: str = ""

class GiteaSetupService:
    """Runs the Gitea install + integrate steps in the background, one job at a
    time. The API token is never stored in the job log."""
    def __init__(self) -> None:
        self._jobs: dict[str, SetupJob] = {}
        self._lock = asyncio.Lock()

    def start(self, *, admin_user: str, repo: str, base_url: str) -> SetupJob:
        job = SetupJob(id=str(uuid.uuid4()), status="running")
        self._jobs[job.id] = job
        asyncio.create_task(self._run(job, admin_user=admin_user, repo=repo, base_url=base_url))
        return job

    def get(self, job_id: str) -> SetupJob | None:
        return self._jobs.get(job_id)

    async def _run(self, job: SetupJob, *, admin_user: str, repo: str, base_url: str) -> None:
        async with self._lock:
            try:
                job.steps.append({"step": "install", "status": "running", "detail": "docker compose up gitea"})
                proc = await asyncio.create_subprocess_exec("./scripts/install_gitea.sh", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                _, stderr = await proc.communicate()
                if proc.returncode != 0:
                    raise RuntimeError(stderr.decode()[-500:] or "install_gitea.sh failed")
                job.steps[-1]["status"] = "done"
                job.steps.append({"step": "setup", "status": "running", "detail": "create admin, repo, token, Vault"})
                proc = await asyncio.create_subprocess_exec(
                    "python", "scripts/setup_gitea.py", "--auto", "--admin-user", admin_user, "--repo", repo,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    env={**__import__("os").environ, "GITEA_BASE_URL": base_url},
                )
                _, stderr = await proc.communicate()
                if proc.returncode != 0:
                    raise RuntimeError(stderr.decode()[-500:] or "setup_gitea.py --auto failed")
                job.steps[-1]["status"] = "done"
                job.status = "done"
            except Exception as exc:
                job.status = "failed"
                job.error = str(exc)
                if job.steps and job.steps[-1]["status"] == "running":
                    job.steps[-1]["status"] = "failed"
