"""Fake execution engine (E1/E2/E3 tests).

Deterministic: every step succeeds with job_id 1000 + number of calls so
far, unless its step is in ``fail_steps``. Playbook jobs return an empty
declared_changes list; python jobs return ``python_declared_changes``
unchanged for every step, so a dry-run and its apply hash identically
(which is what ScriptRunner's approved-hash check needs).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from vireonet.adapters.base import AdapterHealth
from vireonet.adapters.errors import AdapterValidationError
from vireonet.execution.fake_network import FakeNetwork
from vireonet.execution.types import (
    DeclaredChange,
    StepRequest,
    StepResult,
    StepStatus,
)

_STEP_NAMES = (
    "backup",
    "pre_check",
    "dry_run",
    "apply",
    "post_check",
    "rollback",
    "collect_state",
    "run_commands",
)

# Simulated device output for run_commands: (device, platform, command) -> text.
CommandResponder = Callable[[str, str, str], str]


def _devices(req: StepRequest) -> list[str]:
    """Wave steps carry several devices as "leaf-01,leaf-02"."""
    return [d for d in req.device.name.split(",") if d]


class FakeExecutionEngine:
    """In-memory engine for tests. ``calls`` records every StepRequest."""

    def __init__(
        self,
        *,
        fail_steps: set[str] | None = None,
        python_ok: bool = False,
        python_declared_changes: list[DeclaredChange] | None = None,
        command_responder: CommandResponder | None = None,
        network: FakeNetwork | None = None,
        state_store: Any = None,
    ) -> None:
        # Shares the simulated devices with other processes (NetworkStateStore).
        self.state_store = state_store
        self.fail_steps: set[str] = set(fail_steps or set())
        self.calls: list[StepRequest] = []
        self.python_ok = python_ok
        self.python_declared_changes: list[DeclaredChange] = list(python_declared_changes or [])
        # Simulated devices: apply/backup/rollback update it, show commands
        # read it. Without one, devices answer every command with "".
        self.network = network
        if command_responder is None:
            command_responder = network.respond if network is not None else (lambda *_: "")
        self.command_responder: CommandResponder = command_responder

    async def _load(self, req: StepRequest) -> None:
        if self.state_store is not None and self.network is not None:
            await self.state_store.load_into(self.network, _devices(req))

    async def _save(self, req: StepRequest) -> None:
        if self.state_store is not None and self.network is not None:
            await self.state_store.save_from(self.network, _devices(req))

    async def backup(self, req: StepRequest) -> StepResult:
        await self._load(req)
        result = self._result(req.model_copy(update={"step": "backup"}))
        if self.network is not None and result.ok:
            for device in _devices(req):
                self.network.snapshot(device, req.ticket_number)
            running = "\n".join(self.network.running_config(d) for d in _devices(req))
            # Same markers as ansible/playbooks/backup.yml.
            stdout = f"VIREONET-BACKUP-BEGIN\n{running}\nVIREONET-BACKUP-END"
            result = result.model_copy(update={"output": {**result.output, "stdout": stdout}})
        return result

    async def pre_check(self, req: StepRequest) -> StepResult:
        return self._result(req.model_copy(update={"step": "pre_check"}))

    async def dry_run(self, req: StepRequest) -> StepResult:
        return self._result(req.model_copy(update={"step": "dry_run", "dry_run": True}))

    async def apply(self, req: StepRequest) -> StepResult:
        result = self._result(req.model_copy(update={"step": "apply"}))
        if self.network is not None and result.ok and not req.dry_run and req.intent:
            await self._load(req)
            for device in _devices(req):
                self.network.apply(device, req.intent)
            await self._save(req)
        return result

    async def post_check(self, req: StepRequest) -> StepResult:
        return self._result(req.model_copy(update={"step": "post_check"}))

    async def rollback(self, req: StepRequest) -> StepResult:
        result = self._result(req.model_copy(update={"step": "rollback"}))
        if self.network is not None and result.ok:
            for device in _devices(req):
                self.network.restore(device, req.ticket_number)
            await self._save(req)
        return result

    async def collect_state(self, req: StepRequest) -> StepResult:
        return self._result(req.model_copy(update={"step": "collect_state"}))

    async def run_commands(self, req: StepRequest) -> StepResult:
        await self._load(req)
        result = self._result(req.model_copy(update={"step": "run_commands"}))
        commands = json.loads(req.extra_vars.get("commands", "[]"))
        outputs = [
            self.command_responder(req.device.name, req.device.platform, str(c)) for c in commands
        ]
        return result.model_copy(update={"output": {**result.output, "command_outputs": outputs}})

    async def health_check(self) -> AdapterHealth:
        return AdapterHealth(name="fake", status="ok", capabilities=self.capabilities())

    def capabilities(self) -> list[str]:
        return ["playbook", "python", *_STEP_NAMES]

    def _result(self, req: StepRequest) -> StepResult:
        if req.job_type == "python" and not self.python_ok:
            raise AdapterValidationError("fake", "run_python", "fake engine has python_ok=False")
        self.calls.append(req)
        status = StepStatus.FAILED if req.step in self.fail_steps else StepStatus.SUCCESSFUL
        if req.job_type == "python":
            output: dict[str, Any] = {
                "script_name": req.extra_vars.get("script_name", ""),
                "script_sha": req.extra_vars.get("script_sha", ""),
            }
            declared = list(self.python_declared_changes)
        else:
            output = {"fake": True, "step": req.step}
            declared = []
        return StepResult(
            status=status,
            step=req.step,
            device=req.device.name,
            job_id=1000 + len(self.calls),
            log_ref="fake-job",
            duration_seconds=0.0,
            output=output,
            declared_changes=declared,
            evidence={"ticket": req.ticket_number, "correlation_id": req.correlation_id},
        )
