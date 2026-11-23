"""AWX execution engine (E1 playbooks, E2 python runners).

One StepRequest in, one StepResult out. The engine picks the AWX job
template from the ExecutionMapping, launches it, polls to a terminal
status (when the adapter supports polling), and reads the stdout tail.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from vireonet.adapters.base import AdapterHealth
from vireonet.adapters.errors import AdapterValidationError
from vireonet.adapters.protocols import AwxProtocol
from vireonet.execution.mapping import ExecutionMapping
from vireonet.execution.set_stats import (
    parse_result_from_artifacts,
    parse_result_from_stdout,
)
from vireonet.execution.types import (
    RunnerResult,
    StepRequest,
    StepResult,
    StepStatus,
)

_TERMINAL_STATUSES = {"successful", "failed", "canceled"}
_STATUS_MAP = {
    "successful": StepStatus.SUCCESSFUL,
    "failed": StepStatus.FAILED,
    "canceled": StepStatus.CANCELED,
    "cancelled": StepStatus.CANCELED,
}
_STEP_NAMES = ("backup", "pre_check", "dry_run", "apply", "post_check", "rollback", "collect_state")


class AwxEngine:
    """ExecutionEngine backed by an AWX adapter (AwxProtocol)."""

    def __init__(self, awx: AwxProtocol, mapping: ExecutionMapping, base_url: str = "") -> None:
        self._awx = awx
        self._mapping = mapping
        self._base_url = base_url.rstrip("/")

    # -- ExecutionEngine -------------------------------------------------

    async def backup(self, req: StepRequest) -> StepResult:
        return await self._run(req.model_copy(update={"step": "backup"}))

    async def pre_check(self, req: StepRequest) -> StepResult:
        return await self._run(req.model_copy(update={"step": "pre_check"}))

    async def dry_run(self, req: StepRequest) -> StepResult:
        # A dry-run never applies, whatever the caller asked for.
        return await self._run(req.model_copy(update={"step": "dry_run", "dry_run": True}))

    async def apply(self, req: StepRequest) -> StepResult:
        # Apply keeps the caller's dry_run flag: an apply step with
        # dry_run=True is a second dry-run, not a real apply.
        return await self._run(req.model_copy(update={"step": "apply"}))

    async def post_check(self, req: StepRequest) -> StepResult:
        return await self._run(req.model_copy(update={"step": "post_check"}))

    async def rollback(self, req: StepRequest) -> StepResult:
        return await self._run(req.model_copy(update={"step": "rollback"}))

    async def collect_state(self, req: StepRequest) -> StepResult:
        return await self._run(req.model_copy(update={"step": "collect_state"}))

    async def health_check(self) -> AdapterHealth:
        return await self._awx.health_check()

    def capabilities(self) -> list[str]:
        return ["playbook", *_STEP_NAMES]

    # -- template validation ---------------------------------------------

    async def validate_templates(self) -> list[str]:
        """Names of mapped templates the AWX adapter cannot find.

        Adapters without ``template_exists`` are treated as having every
        template — validate_templates must not crash on a partial adapter.
        """
        template_exists = getattr(self._awx, "template_exists", None)
        if template_exists is None:
            return []
        missing: list[str] = []
        for template in sorted(self._mapping.all_templates()):
            if not await template_exists(template):
                missing.append(template)
        return missing

    # -- internals ---------------------------------------------------------

    def _log_ref(self, job_id: int) -> str:
        if self._base_url:
            return f"{self._base_url}/#/jobs/playbook/{job_id}"
        return f"awx-job:{job_id}"

    async def _launch_and_wait(
        self, template: str, extra_vars: dict[str, str], req: StepRequest
    ) -> tuple[Any, str, float]:
        """Launch + poll to a terminal status; return (job, stdout, duration)."""
        started = time.monotonic()
        job = await self._awx.launch_job(template, extra_vars, limit=req.device.name)
        poll = getattr(self._awx, "poll_job", None)
        if poll is not None and job.status not in _TERMINAL_STATUSES:
            job = await poll(job.id, timeout_seconds=req.timeout_seconds)
        stdout = ""
        get_stdout = getattr(self._awx, "get_job_stdout", None)
        if get_stdout is not None:
            try:
                stdout = await get_stdout(job.id)
            except Exception:
                # Stdout is evidence, not the verdict: a missing stdout must
                # not turn a successful job into a failed step.
                stdout = ""
        duration = time.monotonic() - started
        return job, stdout, duration

    async def _run(self, req: StepRequest) -> StepResult:
        if req.job_type == "python":
            return await self._run_python(req)
        template, override_vars = self._mapping.template_for(
            req.step, req.change_type, req.device.platform, job_type="playbook"
        )
        extra_vars = {
            "ticket": req.ticket_number,
            "device": req.device.name,
            "dry_run": "true" if req.dry_run else "false",
            **override_vars,
            **req.extra_vars,
        }
        job, stdout, duration = await self._launch_and_wait(template, extra_vars, req)
        status = _STATUS_MAP.get(job.status, StepStatus.FAILED)
        return StepResult(
            status=status,
            step=req.step,
            device=req.device.name,
            job_id=job.id,
            log_ref=self._log_ref(job.id),
            duration_seconds=duration,
            output={"stdout": stdout, "stdout_tail": stdout[-4000:], "status": job.status},
            declared_changes=[],
            evidence={
                "template": template,
                "ticket": req.ticket_number,
                "correlation_id": req.correlation_id,
            },
        )

    async def _run_python(self, req: StepRequest) -> StepResult:
        template, override_vars = self._mapping.template_for(
            "run_python", req.change_type, req.device.platform, job_type="python"
        )
        extra_vars = {
            "device": req.device.name,
            "step": req.step,
            "request_json": req.model_dump_json(),
            **override_vars,
            **req.extra_vars,
        }
        job, stdout, duration = await self._launch_and_wait(template, extra_vars, req)
        artifacts = await self._job_artifacts(job.id)
        parsed = parse_result_from_artifacts(artifacts) or parse_result_from_stdout(stdout)
        if parsed is None:
            raise AdapterValidationError(
                "awx",
                "run_python",
                f"job {job.id} produced no VIREONET-RESULT block in stdout or set_stats",
            )
        try:
            result = RunnerResult(**parsed)
        except PydanticValidationError as exc:
            raise AdapterValidationError(
                "awx", "run_python", f"VIREONET-RESULT block failed validation: {exc}"
            ) from exc
        status = StepStatus.SUCCESSFUL if result.status == "successful" else StepStatus.FAILED
        return StepResult(
            status=status,
            step=req.step,
            device=req.device.name,
            job_id=job.id,
            log_ref=self._log_ref(job.id),
            duration_seconds=duration,
            output=dict(result.output),
            declared_changes=list(result.declared_changes),
            evidence={
                "template": template,
                "ticket": req.ticket_number,
                "correlation_id": req.correlation_id,
                "runner_error": result.error,
                "runner_duration_seconds": result.duration_seconds,
            },
        )

    async def _job_artifacts(self, job_id: int) -> dict[str, Any]:
        """Best-effort set_stats harvest from job events.

        UNCERTAIN how AWX exposes set_stats via its API: the run_python
        playbook calls set_stats with ``vireonet_result``, and the values
        may surface in job event ``event_data``. Anything missing here
        simply falls back to the stdout markers.
        """
        get_events = getattr(self._awx, "get_job_events", None)
        if get_events is None:
            return {}
        try:
            events = await get_events(job_id)
        except Exception:
            return {}
        for event in events or []:
            if not isinstance(event, dict):
                continue
            data = event.get("event_data")
            if isinstance(data, dict) and "vireonet_result" in data:
                return {"vireonet_result": data["vireonet_result"]}
        return {}
