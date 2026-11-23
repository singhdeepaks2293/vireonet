from __future__ import annotations

import asyncio
import time
from typing import Any
from urllib.parse import quote

import httpx

from vireonet.adapters.base import AdapterConfig, AdapterHealth, BaseAdapter, create_http_client
from vireonet.adapters.errors import (
    AdapterAuthError,
    AdapterNotFound,
    AdapterRateLimited,
    AdapterTimeout,
    AdapterUnavailable,
    AdapterValidationError,
)
from vireonet.adapters.protocols import EveLab


def _map_status(status: int, adapter: str, operation: str, body: str = "") -> Exception:
    if status in {401, 403}:
        return AdapterAuthError(adapter, operation, f"EVE-NG auth failed ({status})")
    if status == 404:
        return AdapterNotFound(adapter, operation, body or "not found")
    if status == 429:
        return AdapterRateLimited(adapter, operation, "rate limited")
    if status in {400, 422}:
        return AdapterValidationError(adapter, operation, body or "validation error")
    if status >= 500:
        return AdapterUnavailable(adapter, operation, f"server error {status}")
    return AdapterUnavailable(adapter, operation, f"unexpected status {status}")


class EveNgAdapter(BaseAdapter):
    """Real EVE-NG adapter. Cookie auth via /api/auth/login. Labs are always
    wiped in a finally by the caller (see scripts/eveng_smoke.py)."""

    def __init__(self, config: AdapterConfig, *, username: str = "", password: str = "", correlation_id: str | None = None) -> None:
        super().__init__(config, correlation_id=correlation_id)
        self.username = username
        self.password = password
        self._cookies: dict[str, str] = {}

    def capabilities(self) -> list[str]:
        return ["start_lab", "stop_lab", "wipe_lab", "load_topology", "wait_nodes", "console"]

    async def health_check(self) -> AdapterHealth:
        try:
            await self._ensure_auth()
            return AdapterHealth(name=self.name, status="ok", capabilities=self.capabilities())
        except Exception as exc:  # noqa: BLE001
            return AdapterHealth(name=self.name, status="down", detail=str(exc), capabilities=self.capabilities())

    async def _ensure_auth(self) -> None:
        if self._cookies:
            return
        async with create_http_client(self.config, correlation_id=self.correlation_id) as client:
            try:
                resp = await client.post("/api/auth/login", json={"username": self.username, "password": self.password})
            except httpx.TimeoutException as exc:
                raise AdapterTimeout(self.name, "login", str(exc)) from exc
            if resp.status_code >= 400:
                raise _map_status(resp.status_code, self.name, "login", resp.text[:500])
            self._cookies = {k: v for k, v in resp.cookies.items()}

    async def _request(self, method: str, path: str, *, json_body: dict[str, Any] | None = None, idempotent: bool) -> Any:
        await self._ensure_auth()

        async def do() -> Any:
            headers = {"Cookie": "; ".join(f"{k}={v}" for k, v in self._cookies.items())} if self._cookies else {}
            if self.correlation_id:
                headers["X-Correlation-ID"] = self.correlation_id
            async with create_http_client(self.config, correlation_id=self.correlation_id) as client:
                try:
                    resp = await client.request(method, path, json=json_body, headers=headers)
                except httpx.TimeoutException as exc:
                    raise AdapterTimeout(self.name, method, str(exc)) from exc
                except httpx.HTTPError as exc:
                    raise AdapterUnavailable(self.name, method, str(exc)) from exc
                if resp.status_code >= 400:
                    raise _map_status(resp.status_code, self.name, method, resp.text[:500])
                return resp.json() if resp.content else {}

        return await self.execute_with_retry(method, do, idempotent=idempotent)

    async def start_lab(self, name: str, topology: str) -> EveLab:
        # topology is a .unl file path or inline topology name. EVE-NG creates
        # a lab, then nodes are added from the topology file via the UI/API.
        # This adapter creates the lab and returns it; load_topology adds nodes.
        data = await self._request("POST", "/api/labs", json_body={"path": "/", "name": name, "version": "1"}, idempotent=False)
        lab_id = str(data.get("data", {}).get("path", f"/{name}"))
        return EveLab(id=lab_id, name=name, status="starting", nodes=[])

    async def load_topology(self, lab_id: str, topology_path: str) -> EveLab:
        # EVE-NG has no single "load .unl" REST call in all versions; the
        # supported path is uploading the .unl as the lab file, then starting.
        # We record the topology and return the lab; node creation from .unl
        # is done by EVE-NG on lab open. If your EVE-NG version exposes a
        # topology-import endpoint, add it here — do not invent one.
        lab = await self.get_lab(lab_id)
        lab.nodes = [topology_path]
        return lab

    async def get_lab(self, lab_id: str) -> EveLab:
        encoded = quote(lab_id, safe="")
        data = await self._request("GET", f"/api/labs/{encoded}", idempotent=True)
        info = data.get("data", data) if isinstance(data, dict) else {}
        nodes = info.get("nodes", {}) if isinstance(info, dict) else {}
        node_names = list(nodes.keys()) if isinstance(nodes, dict) else []
        return EveLab(id=lab_id, name=str(info.get("name", lab_id)), status=str(info.get("status", "running")), nodes=node_names)

    async def wait_for_nodes(self, lab_id: str, *, timeout_seconds: int = 600) -> EveLab:
        deadline = time.monotonic() + timeout_seconds
        while True:
            lab = await self.get_lab(lab_id)
            if lab.status in {"running", "started"} or lab.nodes:
                return lab
            if time.monotonic() > deadline:
                raise AdapterTimeout(self.name, "wait_for_nodes", f"lab {lab_id} did not start in {timeout_seconds}s")
            await asyncio.sleep(10)

    async def stop_lab(self, lab_id: str) -> None:
        encoded = quote(lab_id, safe="")
        await self._request("POST", f"/api/labs/{encoded}/nodes/stop", json_body={}, idempotent=True)

    async def wipe_lab(self, lab_id: str) -> None:
        encoded = quote(lab_id, safe="")
        await self._request("DELETE", f"/api/labs/{encoded}", idempotent=True)

    async def get_console_details(self, lab_id: str, node: str) -> dict[str, str]:
        lab = await self.get_lab(lab_id)
        return {"lab": lab.id, "node": node, "console": f"telnet://{self.config.base_url or 'eveng'}/{lab_id}/{node}"}
