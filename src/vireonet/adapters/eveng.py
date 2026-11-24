from __future__ import annotations

import asyncio
import time
from typing import Any
from urllib.parse import quote, urlparse

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
from vireonet.adapters.lab import LabNode
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


# EVE-NG node status codes (GET /api/labs/{lab}.unl/nodes -> data.<id>.status).
_NODE_STATUS = {0: "stopped", 1: "starting", 2: "running", 3: "running"}


def lab_path(lab_id: str, folder: str = "/") -> str:
    """Normalise a lab id to ``/<folder>/<name>.unl`` (no URL encoding)."""
    raw = lab_id.strip()
    if not raw.startswith("/"):
        raw = folder.rstrip("/") + "/" + raw
    if not raw.endswith(".unl"):
        raw += ".unl"
    return "/" + raw.lstrip("/")


def _lab_url(lab_id: str) -> str:
    """REST path of a lab: /api/labs/<folder>/<name>.unl, slashes kept literal."""
    return "/api/labs" + quote(lab_path(lab_id), safe="/")


def _node_state(node: dict[str, Any]) -> str:
    try:
        return _NODE_STATUS.get(int(node.get("status", 0)), "starting")
    except (TypeError, ValueError):
        return str(node.get("status", ""))


class EveNgAdapter(BaseAdapter):
    """Real EVE-NG adapter. Cookie auth via /api/auth/login. Labs are always
    wiped in a finally by the caller (see scripts/eveng_smoke.py).

    Also implements the LabAdapter protocol (E4): deploy = start_lab +
    load_topology, wait_ready = wait_for_nodes, destroy = stop_lab +
    wipe_lab, exec = telnet to the node console.

    REST shapes (EVE-NG API): a lab is addressed as
    ``/api/labs/<folder>/<name>.unl`` (literal slashes); its nodes via
    ``GET /api/labs/<lab>.unl/nodes`` (``data`` keyed by node id, each with
    ``name``, ``status`` and console ``url`` = ``telnet://host:port``);
    all nodes start/stop with ``GET .../nodes/start`` / ``GET .../nodes/stop``.
    Lab ids returned here are ``/<folder>/<name>.unl``. The old method names are
    kept as the underlying implementation, so the pre-E4 EVE-NG contract
    (and its tests) is unchanged.
    """

    def __init__(
        self,
        config: AdapterConfig,
        *,
        username: str = "",
        password: str = "",
        folder: str = "/",
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(config, correlation_id=correlation_id)
        self.username = username
        self.password = password
        self.folder = "/" + folder.strip("/") if folder.strip("/") else "/"
        self._cookies: dict[str, str] = {}

    def capabilities(self) -> list[str]:
        return ["start_lab", "stop_lab", "wipe_lab", "load_topology", "wait_nodes", "console"]

    async def health_check(self) -> AdapterHealth:
        try:
            await self._ensure_auth()
            return AdapterHealth(name=self.name, status="ok", capabilities=self.capabilities())
        except Exception as exc:  # noqa: BLE001
            return AdapterHealth(
                name=self.name, status="down", detail=str(exc), capabilities=self.capabilities()
            )

    async def _ensure_auth(self) -> None:
        if self._cookies:
            return
        async with create_http_client(self.config, correlation_id=self.correlation_id) as client:
            try:
                resp = await client.post(
                    "/api/auth/login", json={"username": self.username, "password": self.password}
                )
            except httpx.TimeoutException as exc:
                raise AdapterTimeout(self.name, "login", str(exc)) from exc
            if resp.status_code >= 400:
                raise _map_status(resp.status_code, self.name, "login", resp.text[:500])
            self._cookies = {k: v for k, v in resp.cookies.items()}

    async def _request(
        self, method: str, path: str, *, json_body: dict[str, Any] | None = None, idempotent: bool
    ) -> Any:
        await self._ensure_auth()

        async def do() -> Any:
            headers = (
                {"Cookie": "; ".join(f"{k}={v}" for k, v in self._cookies.items())}
                if self._cookies
                else {}
            )
            if self.correlation_id:
                headers["X-Correlation-ID"] = self.correlation_id
            async with create_http_client(
                self.config, correlation_id=self.correlation_id
            ) as client:
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
        """Create the lab in ``self.folder`` and start its nodes.

        POST /api/labs answers only a status message (no lab path), so the
        lab id is built from folder + name, never read from the response.
        """
        await self._request(
            "POST",
            "/api/labs",
            json_body={"path": self.folder, "name": name, "version": "1"},
            idempotent=False,
        )
        lab_id = lab_path(name, self.folder)
        await self.start_nodes(lab_id)
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

    async def list_nodes(self, lab_id: str) -> list[dict[str, Any]]:
        """GET /api/labs/<lab>.unl/nodes -> node dicts (data keyed by node id)."""
        path = lab_path(lab_id, self.folder)
        data = await self._request("GET", f"{_lab_url(path)}/nodes", idempotent=True)
        nodes = data.get("data", {}) if isinstance(data, dict) else {}
        # PHP serialises an empty node map as [].
        items = list(nodes.items()) if isinstance(nodes, dict) else list(enumerate(nodes or []))
        return [{"id": n.get("id", k), **n} for k, n in items if isinstance(n, dict)]

    async def get_lab(self, lab_id: str) -> EveLab:
        path = lab_path(lab_id, self.folder)
        data = await self._request("GET", _lab_url(path), idempotent=True)
        info = data.get("data", data) if isinstance(data, dict) else {}
        info = info if isinstance(info, dict) else {}
        nodes = await self.list_nodes(path)
        states = [_node_state(n) for n in nodes]
        if states and all(st == "running" for st in states):
            status = "running"
        elif states and all(st == "stopped" for st in states):
            status = "stopped"
        else:
            status = "starting" if states else "empty"
        return EveLab(
            id=path,
            name=str(info.get("name", path.rsplit("/", 1)[-1].removesuffix(".unl"))),
            status=status,
            nodes=[str(n.get("name", "")) for n in nodes],
        )

    async def wait_for_nodes(self, lab_id: str, *, timeout_seconds: int = 600) -> EveLab:
        deadline = time.monotonic() + timeout_seconds
        while True:
            lab = await self.get_lab(lab_id)
            if lab.status == "running":
                return lab
            if time.monotonic() > deadline:
                raise AdapterTimeout(
                    self.name, "wait_for_nodes", f"lab {lab_id} did not start in {timeout_seconds}s"
                )
            await asyncio.sleep(10)

    async def start_nodes(self, lab_id: str) -> None:
        await self._request(
            "GET", f"{_lab_url(lab_path(lab_id, self.folder))}/nodes/start", idempotent=True
        )

    async def stop_lab(self, lab_id: str) -> None:
        await self._request(
            "GET", f"{_lab_url(lab_path(lab_id, self.folder))}/nodes/stop", idempotent=True
        )

    async def wipe_lab(self, lab_id: str) -> None:
        await self._request("DELETE", _lab_url(lab_path(lab_id, self.folder)), idempotent=True)

    async def get_console_details(self, lab_id: str, node: str) -> dict[str, str]:
        """Console of one node: its ``url`` field (``telnet://host:port``)."""
        path = lab_path(lab_id, self.folder)
        for n in await self.list_nodes(path):
            if str(n.get("name", "")) == node or str(n.get("id", "")) == node:
                url = str(n.get("url") or "")
                if not url:
                    raise AdapterUnavailable(
                        self.name, "console", f"node {node} has no console url (stopped?)"
                    )
                return {"lab": path, "node": node, "console": url}
        raise AdapterNotFound(self.name, "console", f"node {node} not in lab {path}")

    # -- LabAdapter (E4) -------------------------------------------------

    async def deploy(self, topology_path: str, name: str) -> str:
        lab = await self.start_lab(name, topology_path)
        await self.load_topology(lab.id, topology_path)
        return lab.id

    async def wait_ready(self, lab_id: str, *, timeout_seconds: int = 600) -> list[LabNode]:
        await self.wait_for_nodes(lab_id, timeout_seconds=timeout_seconds)
        return await self.inspect(lab_id)

    async def inspect(self, lab_id: str) -> list[LabNode]:
        # EVE-NG nodes expose no management IP, so mgmt_ip stays empty
        # rather than invented; kind is the node template.
        return [
            LabNode(
                name=str(n.get("name", "")),
                kind=str(n.get("template", "")),
                mgmt_ip="",
                state=_node_state(n),
            )
            for n in await self.list_nodes(lab_path(lab_id, self.folder))
        ]

    async def exec(self, lab_id: str, node: str, command: str) -> str:
        details = await self.get_console_details(lab_id, node)
        host, port = self._console_target(details.get("console", ""))
        return await self._telnet(host, port, command)

    async def save_config(self, lab_id: str, node: str) -> str:
        return await self.exec(lab_id, node, "show running-config")

    async def restore_config(self, lab_id: str, node: str, config: str) -> None:
        raise NotImplementedError(
            "EVE-NG restore is kind-specific: there is no generic way to push "
            "a saved config back through a telnet console on every node kind; "
            "restore through the node's own CLI instead"
        )

    async def destroy(self, lab_id: str) -> None:
        try:
            await self.stop_lab(lab_id)
        finally:
            await self.wipe_lab(lab_id)

    def _console_target(self, console_url: str) -> tuple[str, int]:
        """Telnet (host, port) from a node console url (``telnet://host:port``).

        Without a host in the url, fall back to the configured EVE-NG host
        on the telnet port (23).
        """
        parsed = urlparse(console_url)
        host = parsed.hostname or ""
        if host and host not in {"http", "https"}:
            return host, parsed.port or 23
        base_host = urlparse(self.config.base_url or "").hostname or "eveng"
        return base_host, 23

    async def _telnet(self, host: str, port: int, command: str, *, timeout: int = 30) -> str:
        """Send one command over a raw telnet connection; return its output.

        No credentials are involved (EVE-NG console access is unauthenticated
        telnet on the lab host); the host/port are the only things logged on
        failure, never the command output.
        """
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=10)
        except (TimeoutError, OSError) as exc:
            raise AdapterUnavailable(
                self.name, "exec", f"telnet to {host}:{port} failed: {exc}"
            ) from exc
        try:
            writer.write((command + "\n").encode())
            await writer.drain()
            chunks: list[bytes] = []
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    chunk = await asyncio.wait_for(reader.read(65536), timeout=5)
                except TimeoutError:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks).decode(errors="replace")
        finally:
            writer.close()
