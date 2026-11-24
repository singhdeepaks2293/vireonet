from __future__ import annotations

import base64
from typing import Any, cast

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
)


class GitHubAdapter(GitBaseAdapter):
    def __init__(self, config: AdapterConfig, *, token: str, repo: str) -> None:
        super().__init__(config, token=token)
        self._token = token
        owner, _, name = repo.partition("/")
        if not owner or not name:
            raise AdapterValidationError(
                self.name, "config", "GIT_REPO must be owner/repo for GitHub"
            )
        self.owner, self.repo = owner, name

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def health_check(self) -> AdapterHealth:
        return await self._health(f"/repos/{self.owner}/{self.repo}", headers=self._headers())

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
            f"/repos/{self.owner}/{self.repo}/contents/{path}?ref={ref}", headers=self._headers()
        )
        if isinstance(data, dict) and data.get("type") == "file":
            content = base64.b64decode(data.get("content", "")).decode()
            return GitFile(path=path, content=content, sha=str(data.get("sha", "")))
        raise AdapterNotFound(self.name, "get_file", f"file {path} not found at {ref}")

    async def list_commits(self, path: str, ref: str, limit: int = 20) -> list[GitCommit]:
        data = await self._get(
            f"/repos/{self.owner}/{self.repo}/commits?sha={ref}&path={path}&per_page={limit}",
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
        ref_data = await self._get(
            f"/repos/{self.owner}/{self.repo}/git/ref/heads/{from_ref}", headers=self._headers()
        )
        sha = str(ref_data["object"]["sha"])
        await self._post(
            f"/repos/{self.owner}/{self.repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": sha},
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
        except AdapterNotFound:
            pass
        data = await self._put(
            f"/repos/{self.owner}/{self.repo}/contents/{path}", json=body, headers=self._headers()
        )
        return GitCommitResult(sha=str(data["commit"]["sha"]), branch=branch, path=path)

    async def open_mr(
        self, *, source_branch: str, target_branch: str, title: str, description: str = ""
    ) -> GitMR:
        data = await self._post(
            f"/repos/{self.owner}/{self.repo}/pulls",
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
            f"/repos/{self.owner}/{self.repo}/pulls/{mr_id}", headers=self._headers()
        )
        return self._to_mr(data)

    @staticmethod
    def _to_mr(data: dict[str, Any]) -> GitMR:
        # GitHub reports a merged PR as state "closed" + merged true.
        state = str(data["state"])
        if state == "closed" and data.get("merged"):
            state = "merged"
        return GitMR(
            id=int(data["number"]),
            iid=int(data["number"]),
            title=str(data["title"]),
            source_branch=str(data["head"]["ref"]),
            target_branch=str(data["base"]["ref"]),
            url=str(data["html_url"]),
            state=state,
        )

    async def get_pipeline(self, mr_id: int) -> GitPipeline:
        # The PR's head sha comes from GET /pulls/{n} (head.sha); /pulls/{n}/commits
        # is paginated (max 250) so its last item is not reliably the head.
        data = await self._get(
            f"/repos/{self.owner}/{self.repo}/pulls/{mr_id}", headers=self._headers()
        )
        pr = self._to_mr(data)
        sha = str(data["head"]["sha"])
        runs = await self._get(
            f"/repos/{self.owner}/{self.repo}/commits/{sha}/check-runs", headers=self._headers()
        )
        # UNCERTAIN: check-runs is the Actions status surface; a repo using an
        # external CI (Jenkins, etc.) reports via commit statuses, not check-runs.
        # Map: any failure -> failed, any in-progress -> running, all completed+success -> success.
        check_runs = runs.get("check_runs", []) if isinstance(runs, dict) else []
        if not check_runs:
            return GitPipeline(id=sha, status="unknown", url=pr.url, ref=pr.source_branch)
        statuses = [str(c.get("status", "")) for c in check_runs]
        conclusions = [str(c.get("conclusion", "")) for c in check_runs]
        if any(c in {"failure", "cancelled", "timed_out"} for c in conclusions):
            status = "failed"
        elif any(s in {"in_progress", "queued"} for s in statuses):
            status = "running"
        elif all(c == "success" for c in conclusions):
            status = "success"
        else:
            status = "pending"
        return GitPipeline(
            id=sha, status=cast(PipelineStatus, status), url=pr.url, ref=pr.source_branch
        )

    async def merge_mr(self, mr_id: int) -> GitMR:
        await self._put(
            f"/repos/{self.owner}/{self.repo}/pulls/{mr_id}/merge",
            json={"merge_method": "squash"},
            headers=self._headers(),
        )
        return await self.get_mr(mr_id)

    async def add_comment(self, mr_id: int, body: str) -> None:
        await self._post(
            f"/repos/{self.owner}/{self.repo}/issues/{mr_id}/comments",
            json={"body": body},
            headers=self._headers(),
        )

    async def get_mr_diff(self, mr_id: int) -> str:
        data = await self._get(
            f"/repos/{self.owner}/{self.repo}/pulls/{mr_id}",
            headers={**self._headers(), "Accept": "application/vnd.github.diff"},
        )
        return data if isinstance(data, str) else str(data)
