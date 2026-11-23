from __future__ import annotations

from typing import TYPE_CHECKING, Any, NoReturn, Protocol

from temporalio.client import Client, WorkflowHandle
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from vireonet.config import Settings
from vireonet.temporal.workflow import ChangeWorkflow, ChangeWorkflowInput

if TYPE_CHECKING:
    from vireonet.temporal.ztp_workflow import CallbackPayload, ZtpWorkflowInput


class TemporalClientProtocol(Protocol):
    async def start_change(self, inp: ChangeWorkflowInput) -> str: ...
    async def signal_approve(self, ticket: str, reason: str = "") -> None: ...
    async def signal_reject(self, ticket: str, reason: str = "") -> None: ...
    async def signal_ci(self, ticket: str, passed: bool, reason: str = "") -> None: ...
    async def signal_merge(self, ticket: str) -> None: ...
    async def get_state(self, ticket: str) -> dict[str, Any]: ...
    async def start_ztp(self, inp: ZtpWorkflowInput) -> str: ...
    async def signal_ztp_callback(self, serial: str, payload: CallbackPayload) -> None: ...


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


class FakeTemporalClient:
    """In-memory stand-in for tests / dev without a Temporal server. Records starts + signals."""

    def __init__(self) -> None:
        self.starts: list[ChangeWorkflowInput | ZtpWorkflowInput] = []
        self.signals: list[tuple[str, str, str]] = []  # (ticket, signal, arg)

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

    async def get_state(self, ticket: str) -> dict[str, Any]:
        return {"ci_passed": False, "approved": False, "merged": False, "rejected": False}

    async def start_ztp(self, inp: ZtpWorkflowInput) -> str:
        self.starts.append(inp)
        return inp.ticket_number

    async def signal_ztp_callback(self, serial: str, payload: CallbackPayload) -> None:
        self.signals.append((f"ZTP-{serial}", "device_callback", str(payload.serial)))
