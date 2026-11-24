from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, NoReturn, Protocol

from temporalio.client import Client, WorkflowHandle
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from vireonet.config import Settings
from vireonet.temporal.workflow import ChangeWorkflow, ChangeWorkflowInput

if TYPE_CHECKING:
    from vireonet.drift.workflows import DriftScanInput
    from vireonet.temporal.ztp_workflow import CallbackPayload, ZtpWorkflowInput


class TemporalClientProtocol(Protocol):
    async def start_change(self, inp: ChangeWorkflowInput) -> str: ...
    async def signal_approve(self, ticket: str, reason: str = "") -> None: ...
    async def signal_reject(self, ticket: str, reason: str = "") -> None: ...
    async def signal_ci(self, ticket: str, passed: bool, reason: str = "") -> None: ...
    async def signal_merge(self, ticket: str) -> None: ...
    async def signal_rollback(self, ticket: str, reason: str = "") -> None: ...
    async def get_state(self, ticket: str) -> dict[str, Any]: ...
    async def start_ztp(self, inp: ZtpWorkflowInput) -> str: ...
    async def start_drift_scan(self, inp: DriftScanInput) -> str: ...
    async def signal_ztp_callback(self, serial: str, payload: CallbackPayload) -> None: ...
    async def start_flow(self, run_id: str) -> str: ...
    async def signal_flow(self, run_id: str, signal: str, *args: Any) -> None: ...
    async def upsert_flow_schedule(
        self, flow_id: str, trigger: dict[str, Any], enabled: bool
    ) -> str: ...


class RealTemporalClient:
    """Idempotent start: Workflow ID = ticket. A duplicate start returns the existing handle."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Client | None = None

    async def _get_client(self) -> Client:
        if self._client is None:
            self._client = await Client.connect(
                self._settings.temporal_address, namespace=self._settings.temporal_namespace
            )
        return self._client

    async def start_change(self, inp: ChangeWorkflowInput) -> str:
        client = await self._get_client()
        try:
            handle = await client.start_workflow(
                ChangeWorkflow.run,
                inp,
                id=inp.ticket_number,
                task_queue=self._settings.temporal_task_queue,
                id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
            )
        except WorkflowAlreadyStartedError:
            handle = client.get_workflow_handle(inp.ticket_number)
        return str(handle.id)

    def _handle(self, client: Client, ticket: str) -> WorkflowHandle[Any, Any]:
        return client.get_workflow_handle(ticket)

    async def signal_approve(self, ticket: str, reason: str = "") -> None:
        await (
            (await self._get_client())
            .get_workflow_handle(ticket)
            .signal(ChangeWorkflow.approve, reason)
        )

    async def signal_reject(self, ticket: str, reason: str = "") -> None:
        await (
            (await self._get_client())
            .get_workflow_handle(ticket)
            .signal(ChangeWorkflow.reject, reason)
        )

    async def signal_ci(self, ticket: str, passed: bool, reason: str = "") -> None:
        handle = (await self._get_client()).get_workflow_handle(ticket)
        if passed:
            await handle.signal(ChangeWorkflow.ci_passed)
        else:
            await handle.signal(ChangeWorkflow.ci_failed, reason)

    async def signal_merge(self, ticket: str) -> None:
        await (
            (await self._get_client())
            .get_workflow_handle(ticket)
            .signal(ChangeWorkflow.merge_completed)
        )

    async def signal_rollback(self, ticket: str, reason: str = "") -> None:
        await (
            (await self._get_client())
            .get_workflow_handle(ticket)
            .signal(ChangeWorkflow.request_rollback, reason)
        )

    async def start_ztp(self, inp: ZtpWorkflowInput) -> str:
        from vireonet.temporal.ztp_workflow import ZtpWorkflow

        client = await self._get_client()
        try:
            handle = await client.start_workflow(
                ZtpWorkflow.run,
                inp,
                id=inp.ticket_number,
                task_queue=self._settings.temporal_task_queue,
                id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
            )
        except WorkflowAlreadyStartedError:
            handle = client.get_workflow_handle(inp.ticket_number)
        return str(handle.id)

    async def start_drift_scan(self, inp: DriftScanInput) -> str:
        """Start a DriftScanWorkflow. Workflow ID = drift-scan-<site>-<date>."""
        from vireonet.drift.workflows import DriftScanWorkflow

        client = await self._get_client()
        workflow_id = f"drift-scan-{inp.site}-{inp.date}"
        try:
            handle = await client.start_workflow(
                DriftScanWorkflow.run,
                inp,
                id=workflow_id,
                task_queue=self._settings.temporal_task_queue,
                id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
            )
        except WorkflowAlreadyStartedError:
            handle = client.get_workflow_handle(workflow_id)
        return str(handle.id)

    def get_ztp_handle(self, serial: str) -> NoReturn:
        # Caller must await _get_client first in real code; this helper is for
        # the API, which uses signal_ztp_callback below instead.
        raise NotImplementedError("use signal_ztp_callback")

    async def signal_ztp_callback(self, serial: str, payload: CallbackPayload) -> None:
        from vireonet.temporal.ztp_workflow import ZtpWorkflow

        handle = (await self._get_client()).get_workflow_handle(f"ZTP-{serial}")
        await handle.signal(ZtpWorkflow.device_callback, payload)

    async def get_state(self, ticket: str) -> dict[str, Any]:
        return dict(
            await (await self._get_client())
            .get_workflow_handle(ticket)
            .query(ChangeWorkflow.get_state)
        )

    async def start_flow(self, run_id: str) -> str:
        from vireonet.flows.workflow import FlowWorkflow, FlowWorkflowInput, flow_workflow_id

        client = await self._get_client()
        handle = await client.start_workflow(
            FlowWorkflow.run,
            FlowWorkflowInput(run_id=run_id),
            id=flow_workflow_id(run_id),
            task_queue=self._settings.temporal_task_queue,
        )
        return str(handle.id)

    async def signal_flow(self, run_id: str, signal: str, *args: Any) -> None:
        from vireonet.flows.workflow import flow_workflow_id

        client = await self._get_client()
        await client.get_workflow_handle(flow_workflow_id(run_id)).signal(signal, args=list(args))

    async def upsert_flow_schedule(
        self, flow_id: str, trigger: dict[str, Any], enabled: bool
    ) -> str:
        """Temporal schedule `flow-schedule-<id>` starting FlowScheduleWorkflow.
        Deleted when the flow is disabled or no longer schedule-triggered."""
        from datetime import timedelta

        from temporalio.client import (
            Schedule,
            ScheduleActionStartWorkflow,
            ScheduleIntervalSpec,
            ScheduleSpec,
            ScheduleUpdate,
            ScheduleUpdateInput,
        )
        from temporalio.service import RPCError

        from vireonet.flows.workflow import FlowScheduleWorkflow

        client = await self._get_client()
        sid = f"flow-schedule-{flow_id}"
        handle = client.get_schedule_handle(sid)
        if not enabled or trigger.get("type") != "schedule":
            try:
                await handle.delete()
                return "deleted"
            except RPCError:
                return "absent"
        spec = (
            ScheduleSpec(cron_expressions=[str(trigger["cron"])])
            if trigger.get("cron")
            else ScheduleSpec(
                intervals=[
                    ScheduleIntervalSpec(every=timedelta(minutes=int(trigger["every_minutes"])))
                ]
            )
        )
        schedule = Schedule(
            action=ScheduleActionStartWorkflow(
                FlowScheduleWorkflow.run,
                flow_id,
                id=f"flow-tick-{flow_id}",
                task_queue=self._settings.temporal_task_queue,
            ),
            spec=spec,
        )
        try:
            await client.create_schedule(sid, schedule)
            return "created"
        except Exception as exc:
            if "already" not in str(exc).lower():
                raise

            def update(_inp: ScheduleUpdateInput) -> ScheduleUpdate:
                return ScheduleUpdate(schedule=schedule)

            await handle.update(update)
            return "updated"


class FakeTemporalClient:
    """In-memory stand-in for tests / dev without a Temporal server. Records starts + signals."""

    def __init__(self) -> None:
        self.starts: list[ChangeWorkflowInput | ZtpWorkflowInput] = []
        self.drift_scans: list[DriftScanInput] = []
        self.signals: list[tuple[str, str, str]] = []  # (ticket, signal, arg)
        self.flow_starts: list[str] = []
        self.flow_schedules: dict[str, dict[str, Any]] = {}

    async def start_change(self, inp: ChangeWorkflowInput) -> str:
        if inp.ticket_number not in {s.ticket_number for s in self.starts}:
            self.starts.append(inp)
        return inp.ticket_number

    async def signal_approve(self, ticket: str, reason: str = "") -> None:
        self.signals.append((ticket, "approve", reason))

    async def signal_reject(self, ticket: str, reason: str = "") -> None:
        self.signals.append((ticket, "reject", reason))

    async def signal_ci(self, ticket: str, passed: bool, reason: str = "") -> None:
        self.signals.append((ticket, "ci_passed" if passed else "ci_failed", reason))

    async def signal_merge(self, ticket: str) -> None:
        self.signals.append((ticket, "merge_completed", ""))

    async def signal_rollback(self, ticket: str, reason: str = "") -> None:
        self.signals.append((ticket, "request_rollback", reason))

    async def get_state(self, ticket: str) -> dict[str, Any]:
        return {"ci_passed": False, "approved": False, "merged": False, "rejected": False}

    async def start_ztp(self, inp: ZtpWorkflowInput) -> str:
        self.starts.append(inp)
        return inp.ticket_number

    async def start_drift_scan(self, inp: DriftScanInput) -> str:
        self.drift_scans.append(inp)
        return f"drift-scan-{inp.site}-{inp.date}"

    async def signal_ztp_callback(self, serial: str, payload: CallbackPayload) -> None:
        self.signals.append((f"ZTP-{serial}", "device_callback", str(payload.serial)))

    async def start_flow(self, run_id: str) -> str:
        self.flow_starts.append(run_id)
        return f"flow-run-{run_id}"

    async def signal_flow(self, run_id: str, signal: str, *args: Any) -> None:
        self.signals.append((f"flow-run-{run_id}", signal, json.dumps(list(args), default=str)))

    async def upsert_flow_schedule(
        self, flow_id: str, trigger: dict[str, Any], enabled: bool
    ) -> str:
        self.flow_schedules[flow_id] = {"trigger": trigger, "enabled": enabled}
        return "recorded"
