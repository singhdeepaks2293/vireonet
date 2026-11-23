from __future__ import annotations

from vireonet.temporal.client import FakeTemporalClient
from vireonet.temporal.workflow import ChangeWorkflowInput


async def test_fake_client_idempotent_start() -> None:
    c = FakeTemporalClient()
    inp = ChangeWorkflowInput(change_id="1", ticket_number="RITM1", intent={}, requester="alice")
    assert await c.start_change(inp) == "RITM1"
    assert await c.start_change(inp) == "RITM1"
    assert len(c.starts) == 1  # duplicate start is a no-op


async def test_fake_client_records_signals() -> None:
    c = FakeTemporalClient()
    await c.signal_approve("RITM1", "looks good")
    await c.signal_ci("RITM1", True)
    await c.signal_merge("RITM1")
    assert ("RITM1", "approve", "looks good") in c.signals
    assert ("RITM1", "ci_passed", "") in c.signals
    assert ("RITM1", "merge_completed", "") in c.signals
