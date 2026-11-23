"""Runner entry point (E2): stdin -> driver -> stdout markers.

Usage (from the run_python playbook):
    python -m vireonet_runners.runners <step>

Exit 0 only on success; any failure still emits a valid failed
RunnerResult JSON block so the AWX engine can parse it.
"""

from __future__ import annotations

import sys

from vireonet_runners.capabilities import CAPABILITIES
from vireonet_runners.common import emit, read_request, redact
from vireonet_runners.netmiko_runner import run_netmiko_step
from vireonet_runners.nornir_runner import run_nornir_step
from vireonet_runners.types import RunnerRequest, RunnerResult


def main(step: str) -> int:
    try:
        raw = read_request()
    except ValueError as exc:
        emit(
            RunnerResult(
                status="failed",
                step=step,
                device="",
                error=redact(str(exc)),
            ).model_dump()
        )
        return 1
    raw["step"] = step
    device = str(raw.get("device", ""))
    try:
        request = RunnerRequest(**raw)
    except Exception as exc:  # noqa: BLE001 — bad input is a failed result, not a crash
        emit(
            RunnerResult(
                status="failed",
                step=step,
                device=device,
                error=redact(str(exc)),
            ).model_dump()
        )
        return 1
    try:
        caps = CAPABILITIES.get(request.platform)
        if caps is None:
            raise ValueError(f"unknown platform {request.platform}")
        driver = str(caps["driver"])
        if driver == "netmiko":
            result = run_netmiko_step(request.model_dump())
        elif driver == "napalm":
            result = run_nornir_step(request.model_dump())
        else:
            raise ValueError(f"unknown driver {driver}")
        validated = RunnerResult(**result)
    except Exception as exc:  # noqa: BLE001 — any failure still emits valid JSON
        validated = RunnerResult(
            status="failed",
            step=step,
            device=request.device,
            error=redact(str(exc)),
        )
    emit(validated.model_dump())
    return 0 if validated.status == "successful" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]) if len(sys.argv) > 1 else 1)
