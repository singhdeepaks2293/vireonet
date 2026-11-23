from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from vireonet.temporal.activities import (
        ApplyInput,
        CheckInput,
        CloseInput,
        PlanInput,
        RollbackInput,
        TransitionInput,
        ValidateInput,
    )
    from vireonet.temporal.retry import retry_policy


@dataclass
class ChangeWorkflowInput:
    change_id: str
    ticket_number: str
    intent: dict
    requester: str
    ci_timeout_seconds: int = 86400
    approval_timeout_seconds: int = 86400


@workflow.defn
class ChangeWorkflow:
    """Durable change flow. Workflow code must stay deterministic:
    no I/O, no datetime.now(), no random — only activities and signals."""

    def __init__(self) -> None:
        self._ci_passed = False
        self._ci_failed = False
        self._ci_reason = ""
        self._approved = False
        self._rejected = False
        self._approval_reason = ""
        self._merged = False

    @workflow.run
    async def run(self, inp: ChangeWorkflowInput) -> str:
        log = workflow.logger
        # 1. Validate (re-check; M2 already validated at intake, this is the durable record).
        val = await workflow.execute_activity(
            "validate_change", ValidateInput(change_id=inp.change_id, intent=inp.intent),
            start_to_close_timeout=timedelta(minutes=5), retry_policy=retry_policy(),
        )
        if not val["ok"]:
            await self._transition(inp, "rejected", "validation failed in workflow", val)
            return "rejected"
        # 2. Plan.
        plan = await workflow.execute_activity(
            "plan_change", PlanInput(change_id=inp.change_id, intent=inp.intent),
            start_to_close_timeout=timedelta(minutes=5), retry_policy=retry_policy(),
        )
        await self._transition(inp, "planned", "plan ready", plan)
        # 3. Wait for CI.
        try:
            await workflow.wait_condition(
                lambda: self._ci_passed or self._ci_failed,
                timeout=timedelta(seconds=inp.ci_timeout_seconds),
            )
        except TimeoutError:
            self._ci_failed, self._ci_reason = True, "CI signal timed out"
        if self._ci_failed:
            await self._transition(inp, "ci_failed", self._ci_reason or "CI failed", {})
            return "ci_failed"
        await self._transition(inp, "ci_passed", "CI passed", {})
        # 4. Wait for approval AND merge, in any order.
        try:
            await workflow.wait_condition(
                lambda: (self._approved and self._merged) or self._rejected,
                timeout=timedelta(seconds=inp.approval_timeout_seconds),
            )
        except TimeoutError:
            self._rejected, self._approval_reason = True, "approval/merge timed out"
        if self._rejected:
            await self._transition(inp, "rejected", self._approval_reason or "rejected", {})
            return "rejected"
        await self._transition(inp, "approved", "approved and merged", {})
        # 5. Pre-check.
        pre = await workflow.execute_activity(
            "pre_check_change", CheckInput(change_id=inp.change_id, intent=inp.intent, kind="pre"),
            start_to_close_timeout=timedelta(minutes=10), retry_policy=retry_policy(),
        )
        if not pre["ok"]:
            await self._transition(inp, "pre_check_failed", "pre-check failed", pre)
            return "pre_check_failed"
        await self._transition(inp, "pre_checked", "pre-check passed", pre)
        # 6. Apply (M3: AWX dry-run only — see activities.py).
        try:
            applied = await workflow.execute_activity(
                "apply_change", ApplyInput(change_id=inp.change_id, intent=inp.intent),
                start_to_close_timeout=timedelta(minutes=30), retry_policy=retry_policy(maximum_attempts=3),
            )
        except Exception as exc:  # noqa: BLE001 — apply failure must roll back, not crash the workflow
            log.warning("apply failed", error=str(exc))
            await self._rollback(inp, {"error": str(exc)})
            return "rolled_back"
        if not applied["ok"]:
            await self._rollback(inp, applied)
            return "rolled_back"
        await self._transition(inp, "applied", "apply succeeded", applied)
        # 7. Post-check. Failure triggers rollback.
        post = await workflow.execute_activity(
            "post_check_change", CheckInput(change_id=inp.change_id, intent=inp.intent, kind="post"),
            start_to_close_timeout=timedelta(minutes=10), retry_policy=retry_policy(),
        )
        if not post["ok"]:
            await self._rollback(inp, post)
            return "rolled_back"
        await self._transition(inp, "post_checked", "post-check passed", post)
        # 8. Close.
        closed = await workflow.execute_activity(
            "close_change", CloseInput(change_id=inp.change_id, intent=inp.intent),
            start_to_close_timeout=timedelta(minutes=5), retry_policy=retry_policy(),
        )
        await self._transition(inp, "closed", "change closed", closed)
        return "closed"

    async def _transition(self, inp: ChangeWorkflowInput, to_state: str, reason: str, evidence: dict) -> None:
        await workflow.execute_activity(
            "transition_change",
            TransitionInput(change_id=inp.change_id, to_state=to_state, actor="temporal", reason=reason, evidence=evidence),
            start_to_close_timeout=timedelta(minutes=2), retry_policy=retry_policy(),
        )

    async def _rollback(self, inp: ChangeWorkflowInput, evidence: dict) -> None:
        await workflow.execute_activity(
            "rollback_change", RollbackInput(change_id=inp.change_id, intent=inp.intent, evidence=evidence),
            start_to_close_timeout=timedelta(minutes=10), retry_policy=retry_policy(maximum_attempts=3),
        )
        await self._transition(inp, "rolled_back", "rolled back after failure", evidence)

    @workflow.signal
    def approve(self, reason: str = "") -> None:
        self._approved, self._approval_reason = True, reason

    @workflow.signal
    def reject(self, reason: str = "") -> None:
        self._rejected, self._approval_reason = True, reason

    @workflow.signal
    def ci_passed(self) -> None:
        self._ci_passed = True

    @workflow.signal
    def ci_failed(self, reason: str = "") -> None:
        self._ci_failed, self._ci_reason = True, reason

    @workflow.signal
    def merge_completed(self) -> None:
        self._merged = True

    @workflow.query
    def get_state(self) -> dict:
        return {
            "ci_passed": self._ci_passed, "ci_failed": self._ci_failed,
            "approved": self._approved, "rejected": self._rejected, "merged": self._merged,
        }
