from __future__ import annotations

import asyncio
import time
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
from vireonet.adapters.protocols import AwxJob

_SECRET_KEYS = ("secret", "token", "password", "private_key", "api_key")

# AWX job status -> our status. Unknown statuses pass through unchanged.
AWX_STATUS_MAP = {
    "new": "pending",
    "pending": "pending",
    "waiting": "pending",
    "running": "running",
    "successful": "successful",
    "failed": "failed",
    "error": "failed",
    "canceled": "canceled",
    "cancelled": "canceled",
}


def _map_status(status: int, adapter: str, operation: str, body: str = "") -> Exception:
    if status == 401:
        return AdapterAuthError(adapter, operation, "bad AWX token (401)")
    if status == 403:
        return AdapterAuthError(adapter, operation, "AWX token lacks permission (403)")
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


def _check_extra_vars(extra_vars: dict[str, str]) -> None:
    bad = [k for k in extra_vars if any(s in k.lower() for s in _SECRET_KEYS)]
    if bad:
        raise AdapterValidationError(
            "awx", "launch_job", f"extra_vars must not contain secrets: {bad}"
        )


class AwxAdapter(BaseAdapter):
    """Real AWX / Ansible Controller REST API v2. Token auth. No secrets in extra_vars."""

    def __init__(
        self, config: AdapterConfig, *, token: str = "", correlation_id: str | None = None
    ) -> None:
        super().__init__(config, correlation_id=correlation_id)
        self.token = token
        self._template_ids: dict[str, int] = {}

    def capabilities(self) -> list[str]:
        return [
            "launch_job",
            "launch_workflow",
            "poll_job",
            "job_events",
            "cancel",
            "relaunch",
            "templates",
            "approvals",
            "sync",
        ]

    async def health_check(self) -> AdapterHealth:
        try:
            await self._request("GET", "/api/v2/ping/", idempotent=True)
            return AdapterHealth(name=self.name, status="ok", capabilities=self.capabilities())
        except Exception as exc:  # noqa: BLE001 — health must not raise
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
            headers = {"Authorization": f"Bearer {self.token}"}
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

    async def _get_all(
        self, path: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            data = await self._request(
                "GET", path, params={**(params or {}), "page": page}, idempotent=True
            )
            out.extend(data.get("results", []))
            if not data.get("next"):
                return out
            page += 1

    def _to_job(self, row: dict[str, Any], template: str = "") -> AwxJob:
        return AwxJob(
            id=int(row.get("id", 0)),
            template=template or str(row.get("name", "")),
            status=AWX_STATUS_MAP.get(str(row.get("status", "")), str(row.get("status", ""))),
            extra_vars={},
        )

    async def template_exists(self, name: str) -> bool:
        rows = await self._get_all("/api/v2/job_templates/", {"name": name})
        if rows:
            self._template_ids[name] = int(rows[0]["id"])
        return bool(rows)

    async def _template_id(self, name: str) -> int:
        if name in self._template_ids:
            return self._template_ids[name]
        if not await self.template_exists(name):
            raise AdapterNotFound(self.name, "launch_job", f"job template {name}")
        return self._template_ids[name]

    async def launch_job(
        self, template: str, extra_vars: dict[str, str], limit: str | None = None
    ) -> AwxJob:
        _check_extra_vars(extra_vars)
        tid = await self._template_id(template)
        body: dict[str, Any] = {"extra_vars": extra_vars}
        if limit:
            body["limit"] = limit
        data = await self._request(
            "POST", f"/api/v2/job_templates/{tid}/launch/", json_body=body, idempotent=False
        )
        job_id = int(data.get("job", data.get("id", 0)))
        return (
            await self.get_job(job_id)
            if job_id
            else AwxJob(id=0, template=template, status="pending", extra_vars=dict(extra_vars))
        )

    async def launch_workflow(self, template: str, extra_vars: dict[str, str]) -> AwxJob:
        _check_extra_vars(extra_vars)
        rows = await self._get_all("/api/v2/workflow_job_templates/", {"name": template})
        if not rows:
            raise AdapterNotFound(self.name, "launch_workflow", template)
        data = await self._request(
            "POST",
            f"/api/v2/workflow_job_templates/{rows[0]['id']}/launch/",
            json_body={"extra_vars": extra_vars},
            idempotent=False,
        )
        return AwxJob(
            id=int(data.get("workflow_job", 0)),
            template=template,
            status="pending",
            extra_vars=dict(extra_vars),
        )

    async def get_job(self, job_id: int) -> AwxJob:
        data = await self._request("GET", f"/api/v2/jobs/{job_id}/", idempotent=True)
        return self._to_job(data)

    async def poll_job(
        self, job_id: int, *, timeout_seconds: int = 600, interval_seconds: float = 5.0
    ) -> AwxJob:
        deadline = time.monotonic() + timeout_seconds
        while True:
            job = await self.get_job(job_id)
            if job.status in {"successful", "failed", "canceled"}:
                return job
            if time.monotonic() > deadline:
                raise AdapterTimeout(
                    self.name, "poll_job", f"job {job_id} did not finish in {timeout_seconds}s"
                )
            await asyncio.sleep(interval_seconds)

    async def get_job_events(self, job_id: int) -> list[dict[str, Any]]:
        return await self._get_all(f"/api/v2/jobs/{job_id}/job_events/")

    async def get_job_stdout(self, job_id: int) -> str:
        async with create_http_client(self.config, correlation_id=self.correlation_id) as client:
            resp = await client.get(
                f"/api/v2/jobs/{job_id}/stdout/", headers={"Authorization": f"Bearer {self.token}"}
            )
            if resp.status_code >= 400:
                raise _map_status(resp.status_code, self.name, "get_job_stdout", resp.text[:500])
            return resp.text

    async def cancel_job(self, job_id: int) -> AwxJob:
        await self._request("POST", f"/api/v2/jobs/{job_id}/cancel/", json_body={}, idempotent=True)
        return await self.get_job(job_id)

    async def relaunch_job(self, job_id: int) -> AwxJob:
        data = await self._request(
            "POST", f"/api/v2/jobs/{job_id}/relaunch/", json_body={}, idempotent=False
        )
        return AwxJob(id=int(data.get("job", 0)), template="", status="pending")

    async def list_templates(self) -> list[str]:
        return [str(r.get("name", "")) for r in await self._get_all("/api/v2/job_templates/")]

    async def list_workflow_approval_nodes(self, workflow_job_id: int) -> list[dict[str, Any]]:
        return await self._get_all(
            f"/api/v2/workflow_jobs/{workflow_job_id}/workflow_nodes/",
            {"job__type": "workflow_approval"},
        )

    async def approve_workflow_node(self, node_id: int) -> None:
        await self._request(
            "POST", f"/api/v2/workflow_approvals/{node_id}/approve/", json_body={}, idempotent=True
        )

    async def sync_inventory(self, inventory_id: int) -> int:
        data = await self._request(
            "POST", f"/api/v2/inventories/{inventory_id}/update/", json_body={}, idempotent=False
        )
        return int(data.get("inventory_update", 0))

    async def sync_project(self, project_id: int) -> int:
        data = await self._request(
            "POST", f"/api/v2/projects/{project_id}/update/", json_body={}, idempotent=False
        )
        return int(data.get("project_update", 0))
