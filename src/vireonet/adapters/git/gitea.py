from __future__ import annotations

import base64
from typing import Any

from vireonet.adapters.base import AdapterConfig, AdapterHealth
from vireonet.adapters.errors import AdapterNotFound, AdapterValidationError
from vireonet.adapters.git.base import (
    GitBaseAdapter,
    GitCommit,
    GitCommitResult,
    GitFile,
    GitMR,
    GitPipeline,
    PipelineStatus,
    normalise_pipeline_status,
)


def _run_status(run: dict[str, Any]) -> PipelineStatus:
    """Map a Gitea Actions run: completed -> its conclusion; in-flight states ->
    running/pending; anything else through the shared normaliser."""
    status = str(run.get("status", "")).lower()
    conclusion = str(run.get("conclusion", "") or "").lower()
    if status == "completed":
        return normalise_pipeline_status(conclusion) if conclusion else "unknown"
    if status == "in_progress":
        return "running"
    if status in {"queued", "waiting", "requested", "blocked"}:
        return "pending"
    return normalise_pipeline_status(status or conclusion)


class GiteaAdapter(GitBaseAdapter):
    """Gitea API v1. Self-hosted only — there is no Gitea cloud, so base_url is required."""

    def __init__(self, config: AdapterConfig, *, token: str, repo: str) -> None:
        super().__init__(config, token=token)
        if not config.base_url:
            raise AdapterValidationError(
                self.name,
                "config",
                "GIT_BASE_URL is required for Gitea (self-hosted, no cloud default)",
            )
        self._token = token
        owner, _, name = repo.partition("/")
        if not owner or not name:
            raise AdapterValidationError(
                self.name, "config", "GIT_REPO must be owner/repo for Gitea"
            )
        self.owner, self.repo = owner, name

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"token {self._token}", "Accept": "application/json"}

    def _url(self, path: str) -> str:
        return f"/api/v1{path}"

    async def health_check(self) -> AdapterHealth:
        return await self._health(
            self._url(f"/repos/{self.owner}/{self.repo}"), headers=self._headers()
        )

    def capabilities(self) -> list[str]:
        return [
            "create_branch",
            "commit_file",
            "open_mr",
            "get_pipeline",
            "merge_mr",
            "add_comment",
            "get_file",
        ]

    async def get_file(self, path: str, ref: str) -> GitFile:
        data = await self._get(
            self._url(f"/repos/{self.owner}/{self.repo}/contents/{path}?ref={ref}"),
            headers=self._headers(),
        )
        if isinstance(data, dict) and data.get("type") == "file":
            content = base64.b64decode(data.get("content", "")).decode()
            return GitFile(path=path, content=content, sha=str(data.get("sha", "")))
        raise AdapterNotFound(self.name, "get_file", f"file {path} not found at {ref}")

    async def list_commits(self, path: str, ref: str, limit: int = 20) -> list[GitCommit]:
        data = await self._get(
            self._url(
                f"/repos/{self.owner}/{self.repo}/commits?sha={ref}&path={path}&limit={limit}"
            ),
            headers=self._headers(),
        )
        return [
            GitCommit(
                sha=str(c.get("sha", "")),
                message=str((c.get("commit") or {}).get("message", "")),
                author=str(((c.get("commit") or {}).get("author") or {}).get("name", "")),
                date=str(((c.get("commit") or {}).get("author") or {}).get("date", "")),
            )
            for c in (data or [])[:limit]
        ]

    async def create_branch(self, branch: str, from_ref: str) -> str:
        # Gitea has no POST /git/refs; branches are created via
        # POST /repos/{owner}/{repo}/branches (docs.gitea.com/api/1.24).
        # old_ref_name (>=1.21) accepts a branch, tag or commit; old_branch_name is
        # sent too for older servers. 409 (already exists) surfaces as AdapterConflict.
        await self._post(
            self._url(f"/repos/{self.owner}/{self.repo}/branches"),
            json={
                "new_branch_name": branch,
                "old_ref_name": from_ref,
                "old_branch_name": from_ref,
            },
            headers=self._headers(),
        )
        return branch

    async def commit_file(
        self, branch: str, path: str, content: str, message: str
    ) -> GitCommitResult:
        body: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content.encode()).decode(),
            "branch": branch,
        }
        try:
            existing = await self.get_file(path, branch)
            body["sha"] = existing.sha
            data = await self._put(
                self._url(f"/repos/{self.owner}/{self.repo}/contents/{path}"),
                json=body,
                headers=self._headers(),
            )
        except AdapterNotFound:
            data = await self._post(
                self._url(f"/repos/{self.owner}/{self.repo}/contents/{path}"),
                json=body,
                headers=self._headers(),
            )
        sha = str(data.get("commit", {}).get("sha", "") or data.get("sha", ""))
        return GitCommitResult(sha=sha, branch=branch, path=path)

    async def open_mr(
        self, *, source_branch: str, target_branch: str, title: str, description: str = ""
    ) -> GitMR:
        data = await self._post(
            self._url(f"/repos/{self.owner}/{self.repo}/pulls"),
            json={
                "title": title,
                "head": source_branch,
                "base": target_branch,
                "body": description,
            },
            headers=self._headers(),
        )
        return GitMR(
            id=int(data["number"]),
            iid=int(data["number"]),
            title=str(data["title"]),
            source_branch=source_branch,
            target_branch=target_branch,
            url=str(data["html_url"]),
            state=str(data["state"]),
        )

    async def get_mr(self, mr_id: int) -> GitMR:
        data = await self._get(
            self._url(f"/repos/{self.owner}/{self.repo}/pulls/{mr_id}"), headers=self._headers()
        )
        return self._to_mr(data)

    @staticmethod
    def _to_mr(data: dict[str, Any]) -> GitMR:
        state = str(data["state"])
        if state == "closed" and data.get("merged"):
            state = "merged"
        return GitMR(
            id=int(data["number"]),
            iid=int(data["number"]),
            title=str(data["title"]),
            source_branch=str(data["head"]["label"].split(":")[-1]),
            target_branch=str(data["base"]["label"].split(":")[-1]),
            url=str(data["html_url"]),
            state=state,
        )

    async def get_pipeline(self, mr_id: int) -> GitPipeline:
        # Gitea Actions: GET /repos/{owner}/{repo}/actions/runs?head_sha=<PR head sha>.
        # A 404 (Actions disabled / older Gitea) means "no pipeline info" -> unknown.
        pr_data = await self._get(
            self._url(f"/repos/{self.owner}/{self.repo}/pulls/{mr_id}"), headers=self._headers()
        )
        pr = self._to_mr(pr_data)
        head_sha = str((pr_data.get("head") or {}).get("sha", "") or "")
        path = f"/repos/{self.owner}/{self.repo}/actions/runs"
        if head_sha:
            path += f"?head_sha={head_sha}"
        try:
            data = await self._get(self._url(path), headers=self._headers())
        except AdapterNotFound:
            data = {}
        runs = data.get("workflow_runs", []) if isinstance(data, dict) else []
        if head_sha:
            # Defensive: older servers may ignore the head_sha filter.
            runs = [r for r in runs if str(r.get("head_sha", head_sha)) == head_sha]
        if not runs:
            return GitPipeline(id=str(mr_id), status="unknown", url=pr.url, ref=pr.source_branch)
        latest = runs[0]
        return GitPipeline(
            id=str(latest.get("id", mr_id)),
            status=_run_status(latest),
            url=str(latest.get("html_url", pr.url)),
            ref=pr.source_branch,
        )

    async def merge_mr(self, mr_id: int) -> GitMR:
        await self._post(
            self._url(f"/repos/{self.owner}/{self.repo}/pulls/{mr_id}/merge"),
            json={"Do": "squash"},
            headers=self._headers(),
        )
        return await self.get_mr(mr_id)

    async def add_comment(self, mr_id: int, body: str) -> None:
        await self._post(
            self._url(f"/repos/{self.owner}/{self.repo}/issues/{mr_id}/comments"),
            json={"body": body},
            headers=self._headers(),
        )

    async def get_mr_diff(self, mr_id: int) -> str:
        data = await self._get(
            self._url(f"/repos/{self.owner}/{self.repo}/pulls/{mr_id}.diff"),
            headers=self._headers(),
        )
        return data if isinstance(data, str) else str(data)
