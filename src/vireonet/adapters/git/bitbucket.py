from __future__ import annotations

import asyncio
import re

import httpx

from vireonet.adapters.base import AdapterConfig, AdapterHealth
from vireonet.adapters.errors import AdapterValidationError
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

_SHA_RE = re.compile(r"[0-9a-fA-F]{40}")


def _status(raw: str) -> PipelineStatus:
    """Bitbucket build states: SUCCESSFUL / FAILED / INPROGRESS / STOPPED."""
    low = raw.lower()
    if low == "inprogress":
        return "running"
    if low == "stopped":
        return "canceled"
    return normalise_pipeline_status(low)


class BitbucketAdapter(GitBaseAdapter):
    """Bitbucket Cloud REST 2.0. Token is an OAuth access token (Bearer), not an
    app password — app passwords use Basic auth and are not supported here."""

    merge_poll_attempts = 3
    merge_poll_interval = 1.0

    def __init__(self, config: AdapterConfig, *, token: str, repo: str) -> None:
        super().__init__(config, token=token)
        self._token = token
        workspace, _, name = repo.partition("/")
        if not workspace or not name:
            raise AdapterValidationError(
                self.name, "config", "GIT_REPO must be workspace/repo for Bitbucket"
            )
        self.workspace, self.repo = workspace, name

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    async def health_check(self) -> AdapterHealth:
        return await self._health(
            f"/repositories/{self.workspace}/{self.repo}", headers=self._headers()
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
        # /src/{ref}/{path} serves the raw file. Always use the text: a .json or
        # .yaml file must not be parsed into a dict.
        resp = await self._send(
            "GET",
            f"/repositories/{self.workspace}/{self.repo}/src/{ref}/{path}",
            headers=self._headers(),
            follow_redirects=True,
            idempotent=True,
        )
        return GitFile(path=path, content=resp.text)

    async def list_commits(self, path: str, ref: str, limit: int = 20) -> list[GitCommit]:
        data = await self._get(
            f"/repositories/{self.workspace}/{self.repo}/commits/{ref}?path={path}&pagelen={limit}",
            headers=self._headers(),
        )
        values = data.get("values", []) if isinstance(data, dict) else []
        return [
            GitCommit(
                sha=str(c.get("hash", "")),
                message=str(c.get("message", "")),
                author=str((c.get("author") or {}).get("raw", "")),
                date=str(c.get("date", "")),
            )
            for c in values[:limit]
        ]

    async def create_branch(self, branch: str, from_ref: str) -> str:
        # POST /refs/branches needs target.hash to be a commit hash, so resolve a
        # branch name via GET /refs/branches/{name} -> target.hash first.
        if _SHA_RE.fullmatch(from_ref):
            sha = from_ref
        else:
            ref = await self._get(
                f"/repositories/{self.workspace}/{self.repo}/refs/branches/{from_ref}",
                headers=self._headers(),
            )
            sha = str(ref["target"]["hash"])
        await self._post(
            f"/repositories/{self.workspace}/{self.repo}/refs/branches",
            json={"name": branch, "target": {"hash": sha}},
            headers=self._headers(),
        )
        return branch

    async def commit_file(
        self, branch: str, path: str, content: str, message: str
    ) -> GitCommitResult:
        # POST /src as multipart/form-data: the field NAME is the file path and the
        # VALUE is the raw content, plus "message" and "branch" fields. Response is
        # 201 with the new commit URL in the Location header (.../commit/{hash}).
        resp = await self._send(
            "POST",
            f"/repositories/{self.workspace}/{self.repo}/src",
            data={"message": message, "branch": branch},
            files={path: (path.rsplit("/", 1)[-1], content.encode())},
            headers=self._headers(),
            idempotent=False,
        )
        location = resp.headers.get("location", "")
        sha = location.rstrip("/").rsplit("/", 1)[-1] if "/commit/" in location else ""
        return GitCommitResult(sha=sha, branch=branch, path=path)

    async def open_mr(
        self, *, source_branch: str, target_branch: str, title: str, description: str = ""
    ) -> GitMR:
        data = await self._post(
            f"/repositories/{self.workspace}/{self.repo}/pullrequests",
            json={
                "title": title,
                "source": {"branch": {"name": source_branch}},
                "destination": {"branch": {"name": target_branch}},
                "description": description,
            },
            headers=self._headers(),
        )
        return GitMR(
            id=int(data["id"]),
            iid=int(data["id"]),
            title=str(data["title"]),
            source_branch=source_branch,
            target_branch=target_branch,
            url=str(data["links"]["html"]["href"]),
            state=str(data["state"]).lower(),
        )

    async def get_mr(self, mr_id: int) -> GitMR:
        data = await self._get(
            f"/repositories/{self.workspace}/{self.repo}/pullrequests/{mr_id}",
            headers=self._headers(),
        )
        return GitMR(
            id=int(data["id"]),
            iid=int(data["id"]),
            title=str(data["title"]),
            source_branch=str(data["source"]["branch"]["name"]),
            target_branch=str(data["destination"]["branch"]["name"]),
            url=str(data["links"]["html"]["href"]),
            state=str(data["state"]).lower(),
        )

    async def get_pipeline(self, mr_id: int) -> GitPipeline:
        pr = await self.get_mr(mr_id)
        data = await self._get(
            f"/repositories/{self.workspace}/{self.repo}/pullrequests/{mr_id}/statuses",
            headers=self._headers(),
        )
        values = data.get("values", []) if isinstance(data, dict) else []
        if not values:
            return GitPipeline(id=str(mr_id), status="unknown", url=pr.url, ref=pr.source_branch)
        latest = values[0]
        return GitPipeline(
            id=str(latest.get("key", mr_id)),
            status=_status(str(latest.get("state", ""))),
            url=str(latest.get("url", pr.url)),
            ref=pr.source_branch,
        )

    async def merge_mr(self, mr_id: int) -> GitMR:
        # 200 = merged synchronously. 202 = merge queued as an async task; the
        # task-status URL is in Location. Poll it a few times; if the task is still
        # PENDING, return the PR with state "pending" so the caller can re-check later.
        resp = await self._send(
            "POST",
            f"/repositories/{self.workspace}/{self.repo}/pullrequests/{mr_id}/merge",
            json_body={"merge_strategy": "squash"},
            headers=self._headers(),
            idempotent=False,
        )
        if resp.status_code == 202:
            location = resp.headers.get("location", "")
            if not self._same_host(location):
                mr = await self.get_mr(mr_id)
                mr.state = "pending"
                return mr
            for attempt in range(self.merge_poll_attempts):
                if attempt:
                    await asyncio.sleep(self.merge_poll_interval)
                task = await self._get(location, headers=self._headers())
                task_status = str(task.get("task_status", "") if isinstance(task, dict) else "")
                if task_status.upper() == "SUCCESS":
                    break
            else:
                mr = await self.get_mr(mr_id)
                mr.state = "pending"
                return mr
        return await self.get_mr(mr_id)

    def _same_host(self, url: str) -> bool:
        # Only send the token to the configured API host.
        if not url:
            return False
        target = httpx.URL(url)
        if not target.is_absolute_url:
            return True
        return target.host == httpx.URL(self.config.base_url or "").host

    async def add_comment(self, mr_id: int, body: str) -> None:
        await self._post(
            f"/repositories/{self.workspace}/{self.repo}/pullrequests/{mr_id}/comments",
            json={"content": {"raw": body}},
            headers=self._headers(),
        )

    async def get_mr_diff(self, mr_id: int) -> str:
        # /pullrequests/{id}/diff answers 302 -> /diff/{spec}; follow it.
        resp = await self._send(
            "GET",
            f"/repositories/{self.workspace}/{self.repo}/pullrequests/{mr_id}/diff",
            headers=self._headers(),
            follow_redirects=True,
            idempotent=True,
        )
        return resp.text
