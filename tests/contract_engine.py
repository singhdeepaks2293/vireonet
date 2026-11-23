"""Shared contract for every ExecutionEngine (E1).

An engine passes when: its capabilities name "backup", its health check
is ok, backup() succeeds with a job id, a log ref and non-empty output,
and dry_run() succeeds.

``expect_python_error``: an engine without python support (the Fake with
python_ok=False) must reject an apply with job_type="python" with
AdapterValidationError. Engines with a working python branch (AwxEngine
after E2) pass False instead — the E2 python branch supersedes the E1
rejection for them.
"""

from __future__ import annotations

import pytest

from vireonet.adapters.errors import AdapterValidationError
from vireonet.execution.engine import ExecutionEngine
from vireonet.execution.types import DeviceRef, StepRequest


def _request(**overrides: object) -> StepRequest:
    base: dict[str, object] = {
        "ticket_number": "RITM1",
        "device": DeviceRef(name="leaf-01", platform="iosxe"),
        "step": "backup",
    }
    base.update(overrides)
    return StepRequest(**base)  # type: ignore[arg-type]


async def check_engine(engine: ExecutionEngine, *, expect_python_error: bool = True) -> None:
    assert "backup" in engine.capabilities()
    health = await engine.health_check()
    assert health.status == "ok"

    backup = await engine.backup(_request())
    assert backup.ok, backup
    assert backup.job_id is not None
    assert backup.log_ref
    assert backup.output

    dry = await engine.dry_run(_request(step="dry_run"))
    assert dry.ok, dry

    if expect_python_error:
        with pytest.raises(AdapterValidationError):
            await engine.apply(_request(step="apply", job_type="python", dry_run=False))
