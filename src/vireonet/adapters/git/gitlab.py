from __future__ import annotations

from vireonet.adapters.base import AdapterConfig, AdapterHealth
from vireonet.adapters.git.base import (
    GitBaseAdapter,
    GitCommitResult,
    GitFile,
    GitMR,
    GitPipeline,
    normalise_pipeline_status,
)


class GitLabAdapter(GitBaseAdapter):
    """GitLab REST v4, implementing GitProvider. This is the M5 adapter, moved
    unchanged in behaviour into adapters/git/ so the registry can select it."""

    def __init__(self, config: AdapterConfig, *, token: str, project: str) -> None:
        super().__init__(config, token=token)
        self._token = token
        self.project = project  # URL-encoded group/project, e.g. "netops%2Fintent"

    def _headers(self) -> dict[str, str]:
        return {"PRIVATE-TOKEN": self._token}

    async def health_check(self) -> AdapterHealth:
        return await self._health(f"/projects/{self.project}", headers=self._headers())

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
        import base64

        encoded = path.replace("/", "%2F")
        data = await self._get(
            f"/projects/{self.project}/repository/files/{encoded}?ref={ref}",
            headers=self._headers(),
        )
        content = base64.b64decode(data["content"]).decode()
        return GitFile(path=path, content=content, sha=str(data.get("blob_id", "")))

    async def create_branch(self, branch: str, from_ref: str) -> str:
        await self._post(
            f"/projects/{self.project}/repository/branches",
            json={"branch": branch, "ref": from_ref},
            headers=self._headers(),
        )
        return branch

    async def commit_file(
        self, branch: str, path: str, content: str, message: str
    ) -> GitCommitResult:
        data = await self._post(
            f"/projects/{self.project}/repository/commits",
            json={
                "branch": branch,
                "commit_message": message,
                "actions": [{"action": "update", "file_path": path, "content": content}],
            },
            headers=self._headers(),
        )
        return GitCommitResult(sha=str(data["id"]), branch=branch, path=path)

    async def open_mr(
        self, *, source_branch: str, target_branch: str, title: str, description: str = ""
    ) -> GitMR:
        data = await self._post(
            f"/projects/{self.project}/merge_requests",
            json={
                "source_branch": source_branch,
                "target_branch": target_branch,
                "title": title,
                "description": description,
            },
            headers=self._headers(),
        )
        return GitMR(
            id=int(data["id"]),
            iid=int(data["iid"]),
            title=str(data["title"]),
            source_branch=source_branch,
            target_branch=target_branch,
            url=str(data["web_url"]),
            state=str(data["state"]),
        )

    async def get_mr(self, mr_id: int) -> GitMR:
        data = await self._get(
            f"/projects/{self.project}/merge_requests/{mr_id}", headers=self._headers()
        )
        return GitMR(
            id=int(data["id"]),
            iid=int(data["iid"]),
            title=str(data["title"]),
            source_branch=str(data["source_branch"]),
            target_branch=str(data["target_branch"]),
            url=str(data["web_url"]),
            state=str(data["state"]),
        )

    async def get_pipeline(self, mr_id: int) -> GitPipeline:
        data = await self._get(
            f"/projects/{self.project}/merge_requests/{mr_id}/pipelines", headers=self._headers()
        )
        pipelines = data if isinstance(data, list) else []
        if not pipelines:
            return GitPipeline(id=str(mr_id), status="unknown", url="", ref="")
        latest = pipelines[0]
        return GitPipeline(
            id=str(latest["id"]),
            status=normalise_pipeline_status(str(latest["status"])),
            url=str(latest.get("web_url", "")),
            ref=str(latest.get("ref", "")),
        )

    async def merge_mr(self, mr_id: int) -> GitMR:
        await self._put(
            f"/projects/{self.project}/merge_requests/{mr_id}/merge",
            json={"squash": True},
            headers=self._headers(),
        )
        return await self.get_mr(mr_id)

    async def add_comment(self, mr_id: int, body: str) -> None:
        await self._post(
            f"/projects/{self.project}/merge_requests/{mr_id}/notes",
            json={"body": body},
            headers=self._headers(),
        )

    async def get_mr_diff(self, mr_id: int) -> str:
        data = await self._get(
            f"/projects/{self.project}/merge_requests/{mr_id}/changes", headers=self._headers()
        )
        diffs = data.get("changes", []) if isinstance(data, dict) else []
        return "\n".join(str(d.get("diff", "")) for d in diffs)
