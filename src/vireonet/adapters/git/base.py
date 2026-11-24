from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

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

PipelineStatus = Literal["pending", "running", "success", "failed", "canceled", "unknown"]


@dataclass
class GitMR:
    id: int
    iid: int
    title: str
    source_branch: str
    target_branch: str
    url: str
    state: str  # opened | merged | closed


@dataclass
class GitPipeline:
    id: str
    status: PipelineStatus
    url: str
    ref: str = ""


@dataclass
class GitCommitResult:
    sha: str
    branch: str
    path: str


@dataclass
class GitCommit:
    """One commit that touched a path, newest first in list_commits()."""

    sha: str
    message: str = ""
    author: str = ""
    date: str = ""


@dataclass
class GitFile:
    path: str
    content: str
    sha: str = ""


class GitProvider(Protocol):
    """One interface for GitLab, GitHub, Bitbucket and Gitea. The workflow uses
    only these methods; provider-native field names never leak past the adapter."""

    @property
    def name(self) -> str: ...
    async def health_check(self) -> AdapterHealth: ...
    def capabilities(self) -> list[str]: ...
    async def get_file(self, path: str, ref: str) -> GitFile: ...
    # Commits on `ref` that touched `path`, newest first (version history).
    async def list_commits(self, path: str, ref: str, limit: int = 20) -> list[GitCommit]: ...
    async def create_branch(self, branch: str, from_ref: str) -> str: ...
    async def commit_file(
        self, branch: str, path: str, content: str, message: str
    ) -> GitCommitResult: ...
    async def open_mr(
        self, *, source_branch: str, target_branch: str, title: str, description: str = ""
    ) -> GitMR: ...
    async def get_mr(self, mr_id: int) -> GitMR: ...
    async def get_pipeline(self, mr_id: int) -> GitPipeline: ...
    async def merge_mr(self, mr_id: int) -> GitMR: ...
    async def add_comment(self, mr_id: int, body: str) -> None: ...
    async def get_mr_diff(self, mr_id: int) -> str: ...


def normalise_pipeline_status(raw: str) -> PipelineStatus:
    m = {
        "success": "success",
        "successful": "success",
        "passed": "success",
        "failed": "failed",
        "failure": "failed",
        "error": "failed",
        "running": "running",
        "pending": "pending",
        "created": "pending",
        "canceled": "canceled",
        "cancelled": "canceled",
        "skipped": "canceled",
    }
    return cast(PipelineStatus, m.get(raw.lower(), "unknown"))


def _map_status(status: int, adapter: str, operation: str, body: str = "") -> Exception:
    if status == 401:
        return AdapterAuthError(adapter, operation, "bad token (401)")
    if status == 403:
        return AdapterAuthError(adapter, operation, "token lacks permission (403)")
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


class GitBaseAdapter(BaseAdapter):
    """Shared HTTP for the Git providers, following the AWX/GitLab pattern:
    one _request() with retry (idempotent only), typed errors, correlation
    header, and JSON-or-text responses (diffs / raw files are text)."""

    def __init__(
        self, config: AdapterConfig, *, token: str = "", correlation_id: str | None = None
    ) -> None:
        super().__init__(config, correlation_id=correlation_id)
        self.token = token

    def _auth_headers(self) -> dict[str, str]:
        return {}

    async def _send(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        follow_redirects: bool = False,
        idempotent: bool,
    ) -> httpx.Response:
        """Low-level request returning the raw httpx.Response (non-2xx mapped to
        typed errors). Used where callers need headers (Location), form bodies
        (Bitbucket /src) or the raw text regardless of content type."""

        async def do() -> httpx.Response:
            hdrs = {**self._auth_headers(), **(headers or {})}
            if self.correlation_id:
                hdrs["X-Correlation-ID"] = self.correlation_id
            async with create_http_client(
                self.config, correlation_id=self.correlation_id
            ) as client:
                try:
                    resp = await client.request(
                        method,
                        path,
                        json=json_body,
                        data=data,
                        files=files,
                        headers=hdrs,
                        follow_redirects=follow_redirects,
                    )
                except httpx.TimeoutException as exc:
                    raise AdapterTimeout(self.name, method, str(exc)) from exc
                except httpx.HTTPError as exc:
                    raise AdapterUnavailable(self.name, method, str(exc)) from exc
                if resp.status_code >= 400:
                    raise _map_status(resp.status_code, self.name, method, resp.text[:500])
                return resp

        return await self.execute_with_retry(method, do, idempotent=idempotent)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        idempotent: bool,
    ) -> Any:
        resp = await self._send(
            method, path, json_body=json_body, headers=headers, idempotent=idempotent
        )
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return resp.text

    async def _get(self, path: str, *, headers: dict[str, str] | None = None) -> Any:
        return await self._request("GET", path, headers=headers, idempotent=True)

    async def _post(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return await self._request("POST", path, json_body=json, headers=headers, idempotent=False)

    async def _put(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return await self._request("PUT", path, json_body=json, headers=headers, idempotent=True)

    async def _health(self, path: str, *, headers: dict[str, str] | None = None) -> AdapterHealth:
        try:
            await self._get(path, headers=headers)
            return AdapterHealth(name=self.name, status="ok", capabilities=self.capabilities())
        except Exception as exc:  # noqa: BLE001 - health must not raise
            return AdapterHealth(
                name=self.name, status="down", detail=str(exc), capabilities=self.capabilities()
            )
