from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from vireonet.excel_push.window import parse_when
    from vireonet.temporal.activities import (
        AbortInput,
        ApplyInput,
        CheckInput,
        CloseInput,
        PlanInput,
        ReconcileInput,
        RollbackInput,
        TransitionInput,
        ValidateInput,
        WindowCheckInput,
    )
    from vireonet.temporal.retry import retry_policy


@dataclass
class ChangeWorkflowInput:
    change_id: str
    ticket_number: str
    intent: dict[str, Any]
    requester: str
    ci_timeout_seconds: int = 86400
    approval_timeout_seconds: int = 86400
    # Soak: after post-check passes, wait this long before reconciling so an
    # operator can still request a rollback. 0 (default) skips the wait.
    soak_seconds: int = 0
    # Gates. skip_ci: no CI to wait for (Excel pushes have no merge request;
    # the dry-run on every device is the gate). pre_approved: the approval
    # already happened outside VireoNet (ITSM state, e.g. ServiceNow
    # "Implement" after CAB) — end-to-end automation. Both are recorded.
    skip_ci: bool = False
    pre_approved: bool = False
    approval_note: str = ""
    # Planned window (ISO 8601). Wait for the start before pre-check; never
    # start after the end; the apply step stops starting waves at the end.
    window_start: str = ""
    window_end: str = ""
    emergency: bool = False


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
        self._rollback_requested = False
        self._rollback_reason = ""
        self._backups: dict[str, int] = {}
        self._git_refs: dict[str, str] = {}
        # Where the workflow is, for the get_state query and for the API to
        # tell an operator whether abort / rollback can still take effect.
        self._phase = "starting"

    @workflow.run
    async def run(self, inp: ChangeWorkflowInput) -> str:
        log = workflow.logger
        # 1. Validate (re-check; M2 already validated at intake, this is the durable record).
        val = await workflow.execute_activity(
            "validate_change",
            ValidateInput(change_id=inp.change_id, intent=inp.intent),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=retry_policy(),
        )
        if not val["ok"]:
            await self._transition(inp, "rejected", "validation failed in workflow", val)
            return "rejected"
        # 2. Plan.
        plan = await workflow.execute_activity(
            "plan_change",
            PlanInput(change_id=inp.change_id, intent=inp.intent),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=retry_policy(),
        )
        await self._transition(inp, "planned", "plan ready", plan)
        # 3. Wait for CI. An operator abort (reject signal) also ends the wait.
        self._phase = "awaiting_ci"
        if inp.skip_ci:
            self._ci_passed = True
            self._ci_reason = (
                "CI not applicable: raw config push; the dry-run on every device is the gate"
            )
        try:
            await workflow.wait_condition(
                lambda: self._ci_passed or self._ci_failed or self._rejected,
                timeout=timedelta(seconds=inp.ci_timeout_seconds),
            )
        except TimeoutError:
            self._ci_failed, self._ci_reason = True, "CI signal timed out"
        if self._rejected:
            return await self._abort(inp, locks_held=False)
        if self._ci_failed:
            await self._transition(inp, "ci_failed", self._ci_reason or "CI failed", {})
            return "ci_failed"
        await self._transition(
            inp, "ci_passed", self._ci_reason if inp.skip_ci else "CI passed", {}
        )
        # 4. Wait for approval AND merge, in any order.
        self._phase = "awaiting_approval"
        if inp.pre_approved and not self._rejected:
            self._approved = self._merged = True
            self._approval_reason = inp.approval_note or "approved outside VireoNet"
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
        await self._transition(
            inp,
            "approved",
            self._approval_reason if inp.pre_approved else "approved and merged",
            {"pre_approved": inp.pre_approved} if inp.pre_approved else {},
        )
        # Abort checkpoint: an abort sent after approval still stops the change.
        if self._rejected:
            return await self._abort(inp, locks_held=False)
        # Planned window: nothing touches a device before its start.
        if inp.window_start or inp.window_end:
            outcome = await self._wait_for_window(inp)
            if outcome is not None:
                return outcome
        # 5. Pre-check.
        self._phase = "pre_check"
        pre = await workflow.execute_activity(
            "pre_check_change",
            CheckInput(change_id=inp.change_id, intent=inp.intent, kind="pre"),
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=retry_policy(),
        )
        if not pre["ok"]:
            await self._transition(inp, "pre_check_failed", "pre-check failed", pre)
            return "pre_check_failed"
        # Keep the backup job ids in workflow state (durable in Temporal
        # history), not in one worker's memory, so rollback works anywhere.
        self._backups = dict((pre.get("evidence") or {}).get("backups") or {})
        self._git_refs = dict((pre.get("evidence") or {}).get("git_refs") or {})
        await self._transition(inp, "pre_checked", "pre-check passed", pre)
        # Last abort checkpoint: after this, devices are touched.
        if self._rejected:
            return await self._abort(inp, locks_held=True)
        # Policy change window, re-checked right before apply. Versioned with
        # patched() so workflows started by older code replay unchanged.
        if workflow.patched("policy-window-before-apply"):
            win = await workflow.execute_activity(
                "policy_window_check",
                WindowCheckInput(
                    change_id=inp.change_id, intent=inp.intent, emergency=inp.emergency
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry_policy(),
            )
            if not win.get("ok", True):
                return await self._abort(
                    inp,
                    locks_held=True,
                    reason=f"{win.get('reason')} at apply time; nothing was pushed",
                )
        window_end = parse_when(inp.window_end)
        if window_end is not None and workflow.now() >= window_end:
            return await self._abort(
                inp,
                locks_held=True,
                reason=f"planned window ended at {window_end.isoformat()} before apply; nothing was pushed",
            )
        # 6. Apply. From here on, an operator stop means rollback, not abort.
        self._phase = "applying"
        try:
            applied = await workflow.execute_activity(
                "apply_change",
                ApplyInput(change_id=inp.change_id, intent=inp.intent, window_end=inp.window_end),
                start_to_close_timeout=timedelta(minutes=30),
                retry_policy=retry_policy(maximum_attempts=3),
            )
        except Exception as exc:  # noqa: BLE001 — apply failure must roll back, not crash the workflow
            log.warning("apply failed", error=str(exc))
            return await self._rollback(inp, {"error": str(exc)})
        if not applied["ok"]:
            return await self._rollback(inp, applied)
        await self._transition(inp, "applied", "apply succeeded", applied)
        if self._rollback_requested:
            return await self._rollback(inp, self._manual_rollback_evidence())
        # 7. Post-check. Failure triggers rollback.
        self._phase = "post_check"
        post = await workflow.execute_activity(
            "post_check_change",
            CheckInput(
                change_id=inp.change_id, intent=inp.intent, kind="post", backups=self._backups
            ),
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=retry_policy(),
        )
        if not post["ok"]:
            return await self._rollback(inp, post)
        await self._transition(inp, "post_checked", "post-check passed", post)
        # Soak: give operators a window to watch the network and request a
        # rollback before the change is reconciled and closed.
        if inp.soak_seconds > 0 and not self._rollback_requested:
            self._phase = "soak"
            with contextlib.suppress(TimeoutError):
                await workflow.wait_condition(
                    lambda: self._rollback_requested,
                    timeout=timedelta(seconds=inp.soak_seconds),
                )
        if self._rollback_requested:
            return await self._rollback(inp, self._manual_rollback_evidence())
        self._phase = "finalizing"
        # 8. Reconcile (E3): Git + NetBox must match the device. A failed
        # post_check above rolled back instead, so reconcile never runs
        # for a failed change and writes nothing for one.
        await self._transition(inp, "reconciling", "reconciling Git/NetBox", {})
        try:
            recon = await workflow.execute_activity(
                "reconcile_change",
                ReconcileInput(change_id=inp.change_id, intent=inp.intent),
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=retry_policy(),
            )
        except Exception as exc:  # noqa: BLE001 — an unexpected reconcile error fails the change, it does not close it
            log.warning("reconcile failed", error=str(exc))
            await self._transition(inp, "failed", f"reconcile failed: {exc}", {})
            return "failed"
        if recon.get("status") == "reconciliation_pending":
            await self._transition(inp, "reconciliation_pending", "reconciliation pending", recon)
            return "reconciliation_pending"
        if not recon.get("ok", False):
            await self._transition(inp, "failed", "reconcile failed", recon)
            return "failed"
        await self._transition(inp, "reconciled", "reconciled", recon)
        # 9. Close.
        closed = await workflow.execute_activity(
            "close_change",
            CloseInput(change_id=inp.change_id, intent=inp.intent),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=retry_policy(),
        )
        if not closed.get("ok", True):
            # Close refuses a reconciliation_pending change; the workflow
            # only reaches here from reconciled, so this is a safety net.
            await self._transition(inp, "failed", "close refused", closed)
            return "failed"
        await self._transition(inp, "closed", "change closed", closed)
        self._phase = "done"
        return "closed"

    async def _transition(
        self, inp: ChangeWorkflowInput, to_state: str, reason: str, evidence: dict[str, Any]
    ) -> None:
        await workflow.execute_activity(
            "transition_change",
            TransitionInput(
                change_id=inp.change_id,
                to_state=to_state,
                actor="temporal",
                reason=reason,
                evidence=evidence,
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=retry_policy(),
        )

    async def _rollback(self, inp: ChangeWorkflowInput, evidence: dict[str, Any]) -> str:
        """Run the rollback activity and record what really happened.

        A failed or impossible rollback ends in rollback_failed, never in
        rolled_back: reporting a failed restore as a success is the worst
        thing an orchestrator can do."""
        self._phase = "rolling_back"
        try:
            res = await workflow.execute_activity(
                "rollback_change",
                RollbackInput(
                    change_id=inp.change_id,
                    intent=inp.intent,
                    evidence=evidence,
                    backups=self._backups,
                    git_refs=self._git_refs,
                ),
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=retry_policy(maximum_attempts=3),
            )
        except Exception as exc:  # noqa: BLE001 — a crashed rollback must still be recorded
            res = {"ok": False, "failures": [f"rollback activity failed: {exc}"]}
        self._phase = "done"
        if not res.get("ok"):
            await self._transition(
                inp,
                "rollback_failed",
                "ROLLBACK FAILED: devices may be partially changed; manual action needed",
                {"trigger": evidence, "rollback": res},
            )
            return "rollback_failed"
        await self._transition(inp, "rolled_back", "rolled back after failure", evidence)
        return "rolled_back"

    async def _wait_for_window(self, inp: ChangeWorkflowInput) -> str | None:
        """Hold until the planned start (durable timer; an abort still works);
        refuse once the planned end has passed. None = go ahead."""
        start = parse_when(inp.window_start)
        end = parse_when(inp.window_end)
        if end is not None and workflow.now() >= end:
            return await self._abort(
                inp,
                locks_held=False,
                reason=f"planned window ended at {end.isoformat()} before the change could start; nothing was pushed",
            )
        if start is not None and workflow.now() < start:
            self._phase = "waiting_for_window"
            with contextlib.suppress(TimeoutError):
                await workflow.wait_condition(
                    lambda: self._rejected, timeout=start - workflow.now()
                )
            if self._rejected:
                return await self._abort(inp, locks_held=False)
        if end is not None and workflow.now() >= end:
            return await self._abort(
                inp,
                locks_held=False,
                reason=f"planned window ended at {end.isoformat()}; nothing was pushed",
            )
        return None

    async def _abort(self, inp: ChangeWorkflowInput, *, locks_held: bool, reason: str = "") -> str:
        """Operator abort before apply. Nothing was pushed to any device."""
        if locks_held:
            await workflow.execute_activity(
                "abort_change",
                AbortInput(change_id=inp.change_id, intent=inp.intent),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry_policy(),
            )
        await self._transition(
            inp,
            "rejected",
            reason or self._approval_reason or "aborted by operator",
            {"aborted": True, **({"window": True} if reason else {})},
        )
        self._phase = "done"
        return "rejected"

    def _manual_rollback_evidence(self) -> dict[str, Any]:
        return {"manual": True, "reason": self._rollback_reason or "manual rollback requested"}

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

    @workflow.signal
    def request_rollback(self, reason: str = "") -> None:
        """Operator rollback. Takes effect after apply finishes, after
        post-check, or during the soak window; ignored once reconcile starts."""
        self._rollback_requested, self._rollback_reason = True, reason

    @workflow.query
    def get_state(self) -> dict[str, Any]:
        return {
            "ci_passed": self._ci_passed,
            "ci_failed": self._ci_failed,
            "approved": self._approved,
            "rejected": self._rejected,
            "merged": self._merged,
            "rollback_requested": self._rollback_requested,
            "phase": self._phase,
        }
