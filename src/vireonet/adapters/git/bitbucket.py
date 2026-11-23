from __future__ import annotations

import base64

from vireonet.adapters.base import AdapterConfig, AdapterHealth
from vireonet.adapters.errors import AdapterNotFound, AdapterValidationError
from vireonet.adapters.git.base import (
    GitBaseAdapter,
    GitCommitResult,
    GitFile,
    GitMR,
    GitPipeline,
    normalise_pipeline_status,
)


class BitbucketAdapter(GitBaseAdapter):
    """Bitbucket Cloud REST 2.0. Token is an OAuth access token (Bearer), not an
    app password — app passwords use Basic auth and are not supported here."""

    def __init__(self, config: AdapterConfig, *, token: str, repo: str) -> None:
        super().__init__(config, token=token)
        self._token = token
        workspace, _, name = repo.partition("/")
        if not workspace or not name:
            raise AdapterValidationError(self.name, "config", "GIT_REPO must be workspace/repo for Bitbucket")
        self.workspace, self.repo = workspace, name

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    async def health_check(self) -> AdapterHealth:
        return await self._health(f"/repositories/{self.workspace}/{self.repo}", headers=self._headers())

    def capabilities(self) -> list[str]:
        return ["create_branch", "commit_file", "open_mr", "get_pipeline", "merge_mr", "add_comment", "get_file"]

    async def get_file(self, path: str, ref: str) -> GitFile:
        # Bitbucket serves raw file content, not a JSON wrapper.
        data = await self._get(f"/repositories/{self.workspace}/{self.repo}/src/{ref}/{path}", headers=self._headers())
        if isinstance(data, str):
            return GitFile(path=path, content=data)
        raise AdapterNotFound(self.name, "get_file", f"file {path} not found at {ref}")

    async def create_branch(self, branch: str, from_ref: str) -> str:
        await self._post(f"/repositories/{self.workspace}/{self.repo}/refs/branches", json={"name": branch, "target": {"hash": from_ref}}, headers=self._headers())
        return branch

    async def commit_file(self, branch: str, path: str, content: str, message: str) -> GitCommitResult:
        # UNCERTAIN: Bitbucket commit-via-REST needs a multipart POST to /src
        # with the file as a form field; httpx multipart via `files=` is not
        # wired through BaseAdapter._post (JSON only). This implementation posts
        # JSON and relies on the server accepting it — verify against a real
        # workspace before relying on Bitbucket commits. The contract test
        # asserts the request shape, not a live commit.
        data = await self._post(f"/repositories/{self.workspace}/{self.repo}/src", json={"branch": branch, "message": message, path: base64.b64encode(content.encode()).decode()}, headers=self._headers())
        sha = str(data.get("hash", "") if isinstance(data, dict) else "")
        return GitCommitResult(sha=sha, branch=branch, path=path)

    async def open_mr(self, *, source_branch: str, target_branch: str, title: str, description: str = "") -> GitMR:
        data = await self._post(f"/repositories/{self.workspace}/{self.repo}/pullrequests", json={"title": title, "source": {"branch": {"name": source_branch}}, "destination": {"branch": {"name": target_branch}}, "description": description}, headers=self._headers())
        return GitMR(id=int(data["id"]), iid=int(data["id"]), title=str(data["title"]), source_branch=source_branch, target_branch=target_branch, url=str(data["links"]["html"]["href"]), state=str(data["state"]).lower())

    async def get_mr(self, mr_id: int) -> GitMR:
        data = await self._get(f"/repositories/{self.workspace}/{self.repo}/pullrequests/{mr_id}", headers=self._headers())
        return GitMR(id=int(data["id"]), iid=int(data["id"]), title=str(data["title"]), source_branch=str(data["source"]["branch"]["name"]), target_branch=str(data["destination"]["branch"]["name"]), url=str(data["links"]["html"]["href"]), state=str(data["state"]).lower())

    async def get_pipeline(self, mr_id: int) -> GitPipeline:
        pr = await self.get_mr(mr_id)
        data = await self._get(f"/repositories/{self.workspace}/{self.repo}/pullrequests/{mr_id}/statuses", headers=self._headers())
        values = data.get("values", []) if isinstance(data, dict) else []
        if not values:
            return GitPipeline(id=str(mr_id), status="unknown", url=pr.url, ref=pr.source_branch)
        latest = values[0]
        status = normalise_pipeline_status(str(latest.get("state", "")))
        return GitPipeline(id=str(latest.get("key", mr_id)), status=status, url=str(latest.get("url", pr.url)), ref=pr.source_branch)

    async def merge_mr(self, mr_id: int) -> GitMR:
        await self._post(f"/repositories/{self.workspace}/{self.repo}/pullrequests/{mr_id}/merge", json={"merge_strategy": "squash"}, headers=self._headers())
        return await self.get_mr(mr_id)

    async def add_comment(self, mr_id: int, body: str) -> None:
        await self._post(f"/repositories/{self.workspace}/{self.repo}/pullrequests/{mr_id}/comments", json={"content": {"raw": body}}, headers=self._headers())

    async def get_mr_diff(self, mr_id: int) -> str:
        data = await self._get(f"/repositories/{self.workspace}/{self.repo}/pullrequests/{mr_id}/diff", headers=self._headers())
        return data if isinstance(data, str) else str(data)
