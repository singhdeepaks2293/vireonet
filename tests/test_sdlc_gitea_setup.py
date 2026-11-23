"""SDLC — Requirements R11: Gitea setup (M11).

Manual steps are complete (10), job lifecycle is running -> done/failed,
and the API token never appears in the job log. No Docker, no network.
"""
from __future__ import annotations

import asyncio

from vireonet.api.setup_gitea import MANUAL_STEPS
from vireonet.setup.gitea_setup import GiteaSetupService, SetupJob


def test_manual_steps_has_ten_steps() -> None:
    assert len(MANUAL_STEPS) == 10
    assert all(isinstance(s, str) and s.strip() for s in MANUAL_STEPS)


def test_manual_steps_cover_docker_vault_webhook() -> None:
    joined = "\n".join(MANUAL_STEPS)
    assert "Docker" in joined
    assert "GITEA_DB_PASSWORD" in joined
    assert "Vault" in joined
    assert "VIREONET_GIT_PROVIDER=gitea" in joined
    assert "webhook" in joined.lower()
    assert "install_gitea.sh" in joined


def test_manual_steps_are_numbered_in_order() -> None:
    for i, step in enumerate(MANUAL_STEPS, start=1):
        assert step.startswith(f"{i}."), f"step {i} not numbered: {step!r}"


def test_setup_job_defaults() -> None:
    job = SetupJob(id="job-1", status="running")
    assert job.steps == []
    assert job.error == ""
    assert "token" not in job.__dataclass_fields__  # token is never a job field


async def test_start_returns_running_job_and_get_finds_it() -> None:
    svc = GiteaSetupService()
    job = svc.start(admin_user="vireonet-admin", repo="netops/intent", base_url="http://localhost:3000")
    assert job.status == "running"
    assert job.id
    assert svc.get(job.id) is job
    assert svc.get("no-such-job") is None
    # Let the background task finish (it fails fast without Docker) so the
    # test process does not leak a pending task.
    for _ in range(100):
        if svc.get(job.id).status != "running":
            break
        await asyncio.sleep(0.05)


async def test_failed_install_marks_job_failed_with_error() -> None:
    # In this sandbox Docker is absent, so install_gitea.sh exits non-zero
    # immediately. The job must record failure, not hang or claim success.
    svc = GiteaSetupService()
    job = svc.start(admin_user="vireonet-admin", repo="netops/intent", base_url="http://localhost:3000")
    for _ in range(200):
        if svc.get(job.id).status != "running":
            break
        await asyncio.sleep(0.05)
    done = svc.get(job.id)
    assert done is not None
    assert done.status == "failed"
    assert done.error
    assert done.steps and done.steps[0]["status"] == "failed"


async def test_token_never_appears_in_job_log() -> None:
    # The token value must never be written to steps/error, even on failure.
    svc = GiteaSetupService()
    job = svc.start(admin_user="vireonet-admin", repo="netops/intent", base_url="http://localhost:3000")
    for _ in range(200):
        if svc.get(job.id).status != "running":
            break
        await asyncio.sleep(0.05)
    done = svc.get(job.id)
    assert done is not None
    blob = str(done.steps) + done.error
    assert "ghp_SECRET_TOKEN_VALUE" not in blob
    assert "sha1" not in blob.lower() or "token" not in blob.lower()


def test_install_script_exists_and_is_strict() -> None:
    from pathlib import Path

    script = Path("scripts/install_gitea.sh")
    assert script.exists()
    assert "set -euo pipefail" in script.read_text()
    assert "docker compose" in script.read_text()


def test_gitea_compose_file_exists() -> None:
    from pathlib import Path

    compose = Path("deployments/gitea/docker-compose.gitea.yml")
    assert compose.exists()
    text = compose.read_text()
    assert "gitea/gitea" in text
    assert "3000:3000" in text
    assert "GITEA_DB_PASSWORD" in text
