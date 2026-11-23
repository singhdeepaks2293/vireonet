"""Lab adapter protocol (E4).

One interface for every lab backend (EVE-NG, Containerlab). A lab backend
deploys a topology, waits for its nodes, runs commands on nodes, and
destroys the lab. It never touches a production device: labs exist only
for validation and learning.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from vireonet.adapters.base import AdapterHealth


class LabNode(BaseModel):
    """One node in a deployed lab."""

    name: str
    kind: str
    mgmt_ip: str = ""
    state: str = ""


@runtime_checkable
class LabAdapter(Protocol):
    """Deploy / inspect / exec / destroy a lab topology."""

    async def deploy(self, topology_path: str, name: str) -> str: ...
    async def wait_ready(self, lab_id: str, *, timeout_seconds: int = 600) -> list[LabNode]: ...
    async def inspect(self, lab_id: str) -> list[LabNode]: ...
    async def exec(self, lab_id: str, node: str, command: str) -> str: ...
    async def save_config(self, lab_id: str, node: str) -> str: ...
    async def restore_config(self, lab_id: str, node: str, config: str) -> None: ...
    async def destroy(self, lab_id: str) -> None: ...
    async def health_check(self) -> AdapterHealth: ...
    def capabilities(self) -> list[str]: ...
