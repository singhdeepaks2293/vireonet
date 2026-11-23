from __future__ import annotations

import base64
from typing import Any

import httpx

from vireonet.adapters.base import AdapterConfig, AdapterHealth, BaseAdapter, create_http_client
from vireonet.adapters.errors import (
    AdapterAuthError,
    AdapterConflict,
    AdapterNotFound,
    AdapterRateLimited,
    AdapterTimeout,
    AdapterUnavailable,
    AdapterValidationError,
)
from vireonet.adapters.protocols import GitLabMR


def _map_status(status: int, adapter: str, operation: str, body: str = "") -> Exception:
    if status == 401:
        return AdapterAuthError(adapter, operation, "bad GitLab token (401)")
    if status == 403:
        return AdapterAuthError(adapter, operation, "GitLab token lacks permission (403)")
    if status == 404:
        return AdapterNotFound(adapter, operation, body or "not found")
    if status == 409:
        return AdapterConflict(adapter, operation, body or "conflict")
    if status == 429:
        return AdapterRateLimited(adapter, operation, "rate limited")
    if status in {400, 422}:
        return AdapterValidationError(adapter, operation, body or "validation error")
    if status >= 500:
        return AdapterUnavailable(adapter, operation, f"server error {status}")
    return AdapterUnavailable(adapter, operation, f"unexpected status {status}")


class GitLabAdapter(BaseAdapter):
    """Real GitLab REST API v4. Private-Token auth. Project ID from config."""

    def __init__(
        self,
        config: AdapterConfig,
        *,
        token: str = "",
        project_id: str = "",
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(config, correlation_id=correlation_id)
        self.token = token
        self.project_id = project_id

    def capabilities(self) -> list[str]:
        return ["branch", "commit", "mr", "pipeline", "merge", "comments", "artifacts", "read_file"]

    async def health_check(self) -> AdapterHealth:
        try:
            await self._request("GET", "/api/v4/projects", params={"per_page": 1}, idempotent=True)
            return AdapterHealth(name=self.name, status="ok", capabilities=self.capabilities())
        except Exception as exc:  # noqa: BLE001
            return AdapterHealth(
                name=self.name, status="down", detail=str(exc), capabilities=self.capabilities()
            )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        idempotent: bool,
    ) -> Any:
        async def do() -> Any:
            headers = {"PRIVATE-TOKEN": self.token}
            if self.correlation_id:
                headers["X-Correlation-ID"] = self.correlation_id
            async with create_http_client(
                self.config, correlation_id=self.correlation_id
            ) as client:
                try:
                    resp = await client.request(
                        method, path, params=params, json=json_body, headers=headers
                    )
                except httpx.TimeoutException as exc:
                    raise AdapterTimeout(self.name, method, str(exc)) from exc
                except httpx.HTTPError as exc:
                    raise AdapterUnavailable(self.name, method, str(exc)) from exc
                if resp.status_code >= 400:
                    raise _map_status(resp.status_code, self.name, method, resp.text[:500])
                return resp.json() if resp.content else {}

        return await self.execute_with_retry(method, do, idempotent=idempotent)

    def _project_path(self) -> str:
        if not self.project_id:
            raise AdapterValidationError(self.name, "project", "gitlab_project_id not configured")
        return f"/api/v4/projects/{self.project_id}"

    async def create_branch(self, branch: str, ref: str = "main") -> str:
        data = await self._request(
            "POST",
            f"{self._project_path()}/repository/branches",
            json_body={"branch": branch, "ref": ref},
            idempotent=False,
        )
        return str(data.get("name", branch))

    async def commit_files(self, branch: str, files: dict[str, str], message: str) -> str:
        actions = [
            {"action": "create", "file_path": path, "content": content}
            for path, content in files.items()
        ]
        data = await self._request(
            "POST",
            f"{self._project_path()}/repository/commits",
            json_body={"branch": branch, "commit_message": message, "actions": actions},
            idempotent=False,
        )
        return str(data.get("id", ""))

    @staticmethod
    def _to_mr(row: dict[str, Any]) -> GitLabMR:
        return GitLabMR(
            iid=int(row.get("iid", 0)),
            title=str(row.get("title", "")),
            state=str(row.get("state", "")),
            pipeline_status="unknown",
            url=str(row.get("web_url", "")),
        )

    async def create_mr(self, branch: str, title: str, files: dict[str, str]) -> GitLabMR:
        # M1/M2 signature kept for the contract suite: branch + title + files.
        # Files are committed on the branch first, then the MR is opened.
        await self.create_branch(branch)
        if files:
            await self.commit_files(branch, files, f"{title} (VireoNet)")
        data = await self._request(
            "POST",
            f"{self._project_path()}/merge_requests",
            json_body={"source_branch": branch, "target_branch": "main", "title": title},
            idempotent=False,
        )
        mr = self._to_mr(data)
        mr.pipeline_status = await self.get_pipeline_status(mr.iid)
        return mr

    async def get_mr(self, iid: int) -> GitLabMR:
        data = await self._request(
            "GET", f"{self._project_path()}/merge_requests/{iid}", idempotent=True
        )
        mr = self._to_mr(data)
        mr.pipeline_status = await self.get_pipeline_status(iid)
        return mr

    async def get_pipeline_status(self, iid: int) -> str:
        data = await self._request(
            "GET",
            f"{self._project_path()}/merge_requests/{iid}/pipelines",
            params={"per_page": 1},
            idempotent=True,
        )
        rows = data if isinstance(data, list) else data.get("results", [])
        if not rows:
            return "unknown"
        return str(rows[0].get("status", "unknown"))

    async def get_job_logs(self, pipeline_id: int, job_name: str) -> str:
        jobs = await self._request(
            "GET",
            f"{self._project_path()}/pipelines/{pipeline_id}/jobs",
            params={"per_page": 100},
            idempotent=True,
        )
        rows = jobs if isinstance(jobs, list) else jobs.get("results", [])
        for job in rows:
            if job.get("name") == job_name:
                async with create_http_client(
                    self.config, correlation_id=self.correlation_id
                ) as client:
                    resp = await client.get(
                        f"{self._project_path()}/jobs/{job['id']}/trace",
                        headers={"PRIVATE-TOKEN": self.token},
                    )
                    return resp.text
        raise AdapterNotFound(self.name, "get_job_logs", job_name)

    async def get_artifact(self, pipeline_id: int, job_name: str, artifact_path: str) -> bytes:
        jobs = await self._request(
            "GET",
            f"{self._project_path()}/pipelines/{pipeline_id}/jobs",
            params={"per_page": 100},
            idempotent=True,
        )
        rows = jobs if isinstance(jobs, list) else jobs.get("results", [])
        for job in rows:
            if job.get("name") == job_name:
                async with create_http_client(
                    self.config, correlation_id=self.correlation_id
                ) as client:
                    resp = await client.get(
                        f"{self._project_path()}/jobs/{job['id']}/artifacts/{artifact_path}",
                        headers={"PRIVATE-TOKEN": self.token},
                    )
                    if resp.status_code >= 400:
                        raise _map_status(
                            resp.status_code, self.name, "get_artifact", resp.text[:500]
                        )
                    return resp.content
        raise AdapterNotFound(self.name, "get_artifact", job_name)

    async def post_mr_comment(self, iid: int, body: str) -> int:
        data = await self._request(
            "POST",
            f"{self._project_path()}/merge_requests/{iid}/notes",
            json_body={"body": body},
            idempotent=False,
        )
        return int(data.get("id", 0))

    async def merge_mr(self, iid: int) -> GitLabMR:
        # Merge only when the MR is mergeable and the pipeline passed.
        # GitLab itself enforces approvals / merge checks; we check the status first
        # so a merge attempt on a failing pipeline is a clear error, not a 405.
        mr = await self.get_mr(iid)
        if mr.pipeline_status not in {"success", "unknown"}:
            raise AdapterValidationError(
                self.name, "merge_mr", f"pipeline is {mr.pipeline_status}, not success"
            )
        data = await self._request(
            "PUT",
            f"{self._project_path()}/merge_requests/{iid}/merge",
            json_body={},
            idempotent=True,
        )
        merged = self._to_mr(data)
        merged.pipeline_status = mr.pipeline_status
        return merged

    async def read_file(self, path: str, ref: str = "main") -> str:
        encoded = path.replace("/", "%2F")
        data = await self._request(
            "GET",
            f"{self._project_path()}/repository/files/{encoded}",
            params={"ref": ref},
            idempotent=True,
        )
        content = str(data.get("content", ""))
        return base64.b64decode(content).decode() if content else ""
