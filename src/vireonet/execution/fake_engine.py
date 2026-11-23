"""Fake execution engine (E1/E2/E3 tests).

Deterministic: every step succeeds with job_id 1000 + number of calls so
far, unless its step is in ``fail_steps``. Playbook jobs return an empty
declared_changes list; python jobs return ``python_declared_changes``
unchanged for every step, so a dry-run and its apply hash identically
(which is what ScriptRunner's approved-hash check needs).
"""

from __future__ import annotations

from typing import Any

from vireonet.adapters.base import AdapterHealth
from vireonet.adapters.errors import AdapterValidationError
from vireonet.execution.types import (
    DeclaredChange,
    StepRequest,
    StepResult,
    StepStatus,
)

_STEP_NAMES = ("backup", "pre_check", "dry_run", "apply", "post_check", "rollback", "collect_state")


class FakeExecutionEngine:
    """In-memory engine for tests. ``calls`` records every StepRequest."""

    def __init__(
        self,
        *,
        fail_steps: set[str] | None = None,
        python_ok: bool = False,
        python_declared_changes: list[DeclaredChange] | None = None,
    ) -> None:
        self.fail_steps: set[str] = set(fail_steps or set())
        self.calls: list[StepRequest] = []
        self.python_ok = python_ok
        self.python_declared_changes: list[DeclaredChange] = list(python_declared_changes or [])

    async def backup(self, req: StepRequest) -> StepResult:
        return self._result(req.model_copy(update={"step": "backup"}))

    async def pre_check(self, req: StepRequest) -> StepResult:
        return self._result(req.model_copy(update={"step": "pre_check"}))

    async def dry_run(self, req: StepRequest) -> StepResult:
        return self._result(req.model_copy(update={"step": "dry_run", "dry_run": True}))

    async def apply(self, req: StepRequest) -> StepResult:
        return self._result(req.model_copy(update={"step": "apply"}))

    async def post_check(self, req: StepRequest) -> StepResult:
        return self._result(req.model_copy(update={"step": "post_check"}))

    async def rollback(self, req: StepRequest) -> StepResult:
        return self._result(req.model_copy(update={"step": "rollback"}))

    async def collect_state(self, req: StepRequest) -> StepResult:
        return self._result(req.model_copy(update={"step": "collect_state"}))

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
