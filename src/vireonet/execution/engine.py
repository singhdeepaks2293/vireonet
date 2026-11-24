"""Execution engine protocol and registry (E1)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from vireonet.adapters.base import AdapterHealth
from vireonet.execution.types import StepRequest, StepResult


@runtime_checkable
class ExecutionEngine(Protocol):
    """One async method per engine step, all taking a StepRequest."""

    async def backup(self, req: StepRequest) -> StepResult: ...
    async def pre_check(self, req: StepRequest) -> StepResult: ...
    async def dry_run(self, req: StepRequest) -> StepResult: ...
    async def apply(self, req: StepRequest) -> StepResult: ...
    async def post_check(self, req: StepRequest) -> StepResult: ...
    async def rollback(self, req: StepRequest) -> StepResult: ...
    async def collect_state(self, req: StepRequest) -> StepResult: ...
    # Read-only commands for template checks. extra_vars["commands"] is a JSON
    # list of command strings; output["command_outputs"] is the matching list.
    async def run_commands(self, req: StepRequest) -> StepResult: ...
    async def health_check(self) -> AdapterHealth: ...
    def capabilities(self) -> list[str]: ...


class EngineRegistry:
    """Named engines; "awx" is the default. Unknown names are a ValueError."""

    def __init__(self) -> None:
        self._engines: dict[str, ExecutionEngine] = {}

    def register(self, name: str, engine: ExecutionEngine) -> None:
        self._engines[name] = engine

    def get(self, name: str = "awx") -> ExecutionEngine:
        try:
            return self._engines[name]
        except KeyError:
            raise ValueError(f"unknown execution engine: {name}") from None
