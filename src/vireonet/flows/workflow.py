"""FlowWorkflow: runs one flow run durably in Temporal.

Waits are Temporal timers, approvals are signals, a "Change: push config"
step is a child ChangeWorkflow (the full change pipeline), a sub-flow is a
child FlowWorkflow. A worker restart resumes the run where it was.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from vireonet.flows.activities import (
        FlowActionInput,
        FlowChangeInput,
        FlowFinishInput,
        FlowLoadInput,
        FlowRecordInput,
        FlowScheduledRunInput,
        FlowSubrunInput,
    )
    from vireonet.flows.engine import FlowCancelled, Interpreter, StepFailed
    from vireonet.temporal.workflow import ChangeWorkflow, ChangeWorkflowInput

ONCE = RetryPolicy(maximum_attempts=1)


def _root(exc: BaseException) -> str:
    while exc.__cause__ is not None:
        exc = exc.__cause__
    return str(getattr(exc, "message", "") or exc)


BOOKKEEPING = RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=10)


@dataclass
class FlowWorkflowInput:
    run_id: str
    depth: int = 0


def flow_workflow_id(run_id: str) -> str:
    return f"flow-run-{run_id}"


@workflow.defn
class FlowWorkflow:
    def __init__(self) -> None:
        self._decisions: dict[str, dict[str, Any]] = {}
        self._waiting: dict[str, dict[str, Any]] = {}
        self._cancel = False
        self._current = ""
        self._run_id = ""
        self._flow_name = ""
        self._depth = 0

    # --- Runtime protocol -----------------------------------------------------------------

    async def action(
        self, step_type: str, params: dict[str, Any], *, step_id: str, timeout: int
    ) -> Any:
        self._current = step_id
        if step_type == "rollback_change":
            ticket = str(params.get("ticket_number"))
            handle = workflow.get_external_workflow_handle(ticket)
            try:
                await handle.signal(
                    "request_rollback",
                    str(params.get("reason") or f"requested by flow {self._flow_name}"),
                )
            except Exception as exc:
                raise StepFailed(
                    f"{step_id}: could not signal change {ticket}: {exc}", step_id
                ) from exc
            return {"ticket_number": ticket, "rollback_requested": True}
        return await workflow.execute_activity(
            "flow_action",
            FlowActionInput(
                run_id=self._run_id,
                step_id=step_id,
                step_type=step_type,
                params=params,
                timeout=timeout,
            ),
            start_to_close_timeout=timedelta(seconds=timeout or 600),
            retry_policy=ONCE,
        )

    async def sleep(self, seconds: float) -> None:
        await workflow.sleep(timedelta(seconds=seconds))

    async def approval(self, step_id: str, params: dict[str, Any]) -> dict[str, Any]:
        self._waiting[step_id] = {
            "step_id": step_id,
            "message": params.get("message", ""),
            "role": params.get("role", "approver"),
            "since": workflow.now().isoformat(),
            "timeout_minutes": params.get("timeout_minutes", 60),
        }
        await self._record([], status="waiting", waiting=dict(self._waiting))
        minutes = float(params.get("timeout_minutes") or 60)
        try:
            await workflow.wait_condition(
                lambda: step_id in self._decisions or self._cancel,
                timeout=timedelta(minutes=minutes),
            )
            if self._cancel and step_id not in self._decisions:
                self._waiting.pop(step_id, None)
                raise FlowCancelled()
            decision = self._decisions[step_id]
        except TimeoutError:
            decision = {
                "approved": params.get("on_timeout") == "approve",
                "by": "timeout",
                "comment": f"no decision within {minutes:g} minutes",
            }
        self._waiting.pop(step_id, None)
        await self._record([], status="running", waiting=dict(self._waiting))
        return decision

    async def config_push(self, step_id: str, params: dict[str, Any]) -> dict[str, Any]:
        self._current = step_id
        made = await self._step_activity(
            step_id,
            "flow_create_change",
            FlowChangeInput(
                run_id=self._run_id, flow_name=self._flow_name, step_id=step_id, params=params
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=ONCE,
        )
        ticket, state = made["ticket_number"], made["state"]
        out = {"change_id": made["change_id"], "ticket_number": ticket, "created": made["created"]}
        if state == "rejected":
            info = await self._change_state(made["change_id"])
            raise StepFailed(
                f"{step_id}: change {ticket} was rejected: {info.get('reason')}", step_id
            )
        if not made["created"]:
            info = await self._change_state(made["change_id"])
            return {
                **out,
                "result": info["state"],
                "note": "a change for this ticket already existed",
            }
        g = made["gates"]
        handle = await workflow.start_child_workflow(
            ChangeWorkflow.run,
            ChangeWorkflowInput(
                change_id=made["change_id"],
                ticket_number=ticket,
                intent=made["intent"],
                requester=made["requester"],
                soak_seconds=made["soak_seconds"],
                skip_ci=bool(g["skip_ci"]),
                pre_approved=bool(g["pre_approved"]),
                approval_note=g["approval_note"],
                window_start=g["window_start"],
                window_end=g["window_end"],
            ),
            id=ticket,
            task_queue=workflow.info().task_queue,
            parent_close_policy=workflow.ParentClosePolicy.ABANDON,
        )
        if not params.get("wait", True):
            return {**out, "result": "started"}
        try:
            result = str(await handle)
        except Exception as exc:  # the change workflow itself crashed
            raise StepFailed(
                f"{step_id}: change {ticket} workflow failed: {_root(exc)}", step_id
            ) from exc
        out["result"] = result
        if result != "closed":
            info = await self._change_state(made["change_id"])
            raise StepFailed(
                f"{step_id}: change {ticket} ended {result}: {info.get('reason')}", step_id
            )
        return out

    async def _step_activity(self, step_id: str, name: str, arg: Any, **kw: Any) -> Any:
        """An activity whose failure fails the *step* (so Try / continue-on-error
        can handle it) instead of the whole workflow."""
        try:
            return await workflow.execute_activity(name, arg, **kw)
        except ActivityError as exc:
            raise StepFailed(f"{step_id}: {_root(exc)}", step_id) from exc

    async def _change_state(self, change_id: str) -> dict[str, Any]:
        return await workflow.execute_activity(  # type: ignore[no-any-return]
            "flow_change_state",
            change_id,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=BOOKKEEPING,
        )

    async def run_flow(self, step_id: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._depth >= 5:
            raise StepFailed(f"{step_id}: sub-flows nested more than 5 deep", step_id)
        sub = await self._step_activity(
            step_id,
            "flow_prepare_subrun",
            FlowSubrunInput(
                parent_run_id=self._run_id,
                flow=str(params.get("flow")),
                inputs=dict(params.get("inputs") or {}),
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=ONCE,
        )
        result = await workflow.execute_child_workflow(
            FlowWorkflow.run,
            FlowWorkflowInput(run_id=sub["run_id"], depth=self._depth + 1),
            id=flow_workflow_id(sub["run_id"]),
            task_queue=workflow.info().task_queue,
        )
        if result.get("status") != "succeeded":
            raise StepFailed(
                f"{step_id}: sub-flow {params.get('flow')!r} {result.get('status')}: {result.get('error')}",
                step_id,
            )
        return {"run_id": sub["run_id"], **result}

    async def record(self, entry: dict[str, Any]) -> None:
        await self._record([entry])

    async def _record(
        self,
        entries: list[dict[str, Any]],
        *,
        status: str = "",
        waiting: dict[str, Any] | None = None,
    ) -> None:
        await workflow.execute_activity(
            "flow_record",
            FlowRecordInput(run_id=self._run_id, entries=entries, status=status, waiting=waiting),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=BOOKKEEPING,
        )

    def now_iso(self) -> str:
        return workflow.now().isoformat()

    def cancelled(self) -> bool:
        return self._cancel

    # --- workflow ------------------------------------------------------------------------------

    @workflow.run
    async def run(self, inp: FlowWorkflowInput) -> dict[str, Any]:
        self._run_id, self._depth = inp.run_id, inp.depth
        loaded = await workflow.execute_activity(
            "flow_load",
            FlowLoadInput(run_id=inp.run_id),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=BOOKKEEPING,
        )
        self._flow_name = loaded["flow_name"]
        result = await Interpreter(self).run(
            loaded["definition"],
            trigger=loaded["trigger"],
            inputs=loaded["inputs"],
            run={
                "id": inp.run_id,
                "flow": loaded["flow_name"],
                "version": loaded["version"],
                "started_by": loaded["started_by"],
                "started_at": workflow.now().isoformat(),
            },
        )
        await workflow.execute_activity(
            "flow_finish",
            FlowFinishInput(run_id=inp.run_id, result=result),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=BOOKKEEPING,
        )
        return result

    @workflow.signal
    def decide(self, step_id: str, approved: bool, by: str = "", comment: str = "") -> None:
        self._decisions[step_id] = {"approved": approved, "by": by, "comment": comment}

    @workflow.signal
    def cancel(self, by: str = "") -> None:
        self._cancel = True

    @workflow.query
    def get_state(self) -> dict[str, Any]:
        return {"current_step": self._current, "waiting": self._waiting, "cancelled": self._cancel}


@workflow.defn
class FlowScheduleWorkflow:
    """Started by a flow's Temporal schedule: create a run of the published
    version (if the flow is still enabled) and run it as a child."""

    @workflow.run
    async def run(self, flow_id: str) -> dict[str, Any]:
        made = await workflow.execute_activity(
            "flow_scheduled_run",
            FlowScheduledRunInput(flow_id=flow_id, scheduled_at=workflow.now().isoformat()),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=BOOKKEEPING,
        )
        if not made.get("run_id"):
            return {"status": "skipped", "reason": made.get("reason", "")}
        return await workflow.execute_child_workflow(
            FlowWorkflow.run,
            FlowWorkflowInput(run_id=made["run_id"]),
            id=flow_workflow_id(made["run_id"]),
            task_queue=workflow.info().task_queue,
        )
