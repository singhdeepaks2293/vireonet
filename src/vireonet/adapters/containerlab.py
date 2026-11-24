"""Containerlab adapter (E4).

Drives the ``containerlab`` CLI on the lab host over SSH (asyncssh).
There is no stable Containerlab REST API known to this project, so the
CLI is the interface — ``_run_cli`` is the one place that knows about it,
so a future API-based implementation only has to replace that method.

UNCERTAIN: no stable REST API known; the CLI is the interface. Containerlab
``inspect --format json`` output is handled in the current shape
(``{"<labname>": [...]}``, keyed by lab name) and the older shapes
(``{"containers": [...]}`` and a top-level list); unknown fields are
ignored, never invented.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import time
from typing import Any

import asyncssh

from vireonet.adapters.base import AdapterConfig, AdapterHealth, BaseAdapter
from vireonet.adapters.errors import AdapterTimeout, AdapterUnavailable
from vireonet.adapters.lab import LabNode


def _parse_inspect(raw: str, lab_id: str) -> list[LabNode]:
    """Parse ``containerlab inspect --format json`` output.

    Handles the current ``{"<labname>": [...]}`` shape (entries carry
    ``ipv4_address`` and ``state``), plus the older ``{"containers": [...]}``
    and a top-level list. Each
    entry contributes name / kind / ipv4 -> mgmt_ip / state. Containerlab
    names containers ``clab-<lab>-<node>``; that prefix is stripped so the
    node name matches the topology. The CIDR suffix on an IPv4 address is
    stripped (``10.0.1.11/24`` -> ``10.0.1.11``).
    """
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AdapterUnavailable("containerlab", "inspect", "inspect output is not JSON") from exc
    if isinstance(data, dict):
        if "containers" in data:
            entries = data["containers"]
        elif lab_id in data:
            entries = data[lab_id]
        else:
            # Keyed by lab name: take every list value (e.g. --all, or a
            # lab whose name differs from lab_id only in the topology).
            entries = [e for v in data.values() if isinstance(v, list) for e in v]
    elif isinstance(data, list):
        entries = data
    else:
        raise AdapterUnavailable(
            "containerlab", "inspect", f"unexpected inspect shape: {type(data).__name__}"
        )
    if not isinstance(entries, list):
        raise AdapterUnavailable("containerlab", "inspect", "inspect containers are not a list")
    nodes: list[LabNode] = []
    prefix = f"clab-{lab_id}-"
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", ""))
        if name.startswith(prefix):
            name = name[len(prefix) :]
        ipv4 = str(entry.get("ipv4") or entry.get("ipv4_address") or entry.get("mgmt_ip") or "")
        mgmt_ip = ipv4.split("/")[0] if ipv4 else ""
        nodes.append(
            LabNode(
                name=name,
                kind=str(entry.get("kind", "")),
                mgmt_ip=mgmt_ip,
                state=str(entry.get("state", "")),
            )
        )
    return nodes


class ContainerlabAdapter(BaseAdapter):
    """Lab backend driving ``containerlab`` on a remote host over SSH."""

    def __init__(
        self,
        config: AdapterConfig | None = None,
        *,
        host: str,
        user: str,
        key_path: str | None = None,
        password: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(
            config or AdapterConfig(name="containerlab"), correlation_id=correlation_id
        )
        self.host = host
        self.user = user
        self.key_path = key_path or None
        self.password = password or None

    def capabilities(self) -> list[str]:
        return ["deploy", "inspect", "exec", "save_config", "destroy"]

    async def health_check(self) -> AdapterHealth:
        try:
            await self._run_cli("version")
            return AdapterHealth(name=self.name, status="ok", capabilities=self.capabilities())
        except Exception as exc:  # noqa: BLE001
            return AdapterHealth(
                name=self.name, status="down", detail=str(exc), capabilities=self.capabilities()
            )

    async def _run_cli(self, *args: str, timeout: int = 120) -> str:
        """Run one ``containerlab ...`` command on the lab host over SSH.

        Non-zero exit raises AdapterUnavailable with the first 500 chars of
        stderr. No secret is ever logged: the command contains only
        containerlab arguments, never credentials.
        """
        cmd = "containerlab " + " ".join(shlex.quote(a) for a in args)
        try:
            async with asyncssh.connect(
                self.host,
                username=self.user,
                client_keys=[self.key_path] if self.key_path else None,
                password=self.password,
            ) as conn:
                result = await asyncio.wait_for(conn.run(cmd), timeout=timeout)
        except (TimeoutError, asyncssh.Error, OSError) as exc:
            raise AdapterUnavailable(
                "containerlab", "cli", f"ssh to {self.host} failed: {exc}"
            ) from exc
        if result.exit_status != 0:
            stderr = result.stderr or ""
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors="replace")
            raise AdapterUnavailable("containerlab", "cli", stderr.strip()[:500])
        stdout = result.stdout or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        return stdout

    async def deploy(self, topology_path: str, name: str) -> str:
        await self._run_cli("deploy", "-t", topology_path)
        return name

    async def wait_ready(self, lab_id: str, *, timeout_seconds: int = 600) -> list[LabNode]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            nodes = await self.inspect(lab_id)
            if nodes and all(n.state == "running" for n in nodes):
                return nodes
            if time.monotonic() > deadline:
                raise AdapterTimeout(
                    self.name,
                    "wait_ready",
                    f"lab {lab_id} nodes did not reach running in {timeout_seconds}s",
                )
            await asyncio.sleep(5)

    async def inspect(self, lab_id: str) -> list[LabNode]:
        raw = await self._run_cli("inspect", "--name", lab_id, "--format", "json")
        return _parse_inspect(raw, lab_id)

    async def exec(self, lab_id: str, node: str, command: str) -> str:
        return await self._run_cli(
            "exec", "--name", lab_id, "--label", f"clab-node-name={node}", "--cmd", command
        )

    async def save_config(self, lab_id: str, node: str) -> str:
        return await self.exec(lab_id, node, "show running-config")

    async def restore_config(self, lab_id: str, node: str, config: str) -> None:
        raise NotImplementedError(
            "containerlab restore is kind-specific: there is no generic way to "
            "restore a saved config on every node kind (cisco_iol, srl, ceos, "
            "crpd all differ); restore through the node's own CLI instead"
        )

    async def destroy(self, lab_id: str) -> None:
        await self._run_cli("destroy", "--name", lab_id, "--cleanup")
