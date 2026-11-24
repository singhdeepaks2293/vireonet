"""Execution engine types (E1/E2).

StepRequest is the single request shape every engine step takes; StepResult
is the single result shape every engine step returns. The E2 python runners
mirror RunnerResult/DeclaredChange with identical field names (see
ansible/python_runners/vireonet_runners/types.py); this module defines the
server-side copies because the runner package is not installed in the API.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, model_validator

StepName = Literal[
    "backup",
    "pre_check",
    "dry_run",
    "apply",
    "post_check",
    "rollback",
    "collect_state",
    "run_commands",
]
JobType = Literal["playbook", "python"]

# Any extra_vars key containing one of these substrings is treated as a
# secret and rejected at the model boundary, before it can reach AWX.
_SECRET_SUBSTRINGS = ("secret", "token", "password", "private_key", "api_key")


def _reject_secret_keys(extra_vars: dict[str, str]) -> None:
    bad = [k for k in extra_vars if any(s in k.lower() for s in _SECRET_SUBSTRINGS)]
    if bad:
        raise ValueError(f"extra_vars must not contain secrets: {bad}")


class StepStatus(StrEnum):
    SUCCESSFUL = "successful"
    FAILED = "failed"
    CANCELED = "canceled"
    TIMEOUT = "timeout"


class DeviceRef(BaseModel):
    name: str
    platform: str = ""
    mgmt_ip: str | None = None


class StepRequest(BaseModel):
    ticket_number: str
    device: DeviceRef
    step: StepName
    change_type: str = ""
    job_type: JobType = "playbook"
    intent: dict[str, Any] = {}
    rendered_config: str = ""
    diff: str = ""
    extra_vars: dict[str, str] = {}
    dry_run: bool = True
    timeout_seconds: int = 600
    correlation_id: str = ""

    @model_validator(mode="after")
    def _no_secret_extra_vars(self) -> StepRequest:
        _reject_secret_keys(self.extra_vars)
        return self


class DeclaredChange(BaseModel):
    """One change a runner declares it made (or would make on dry-run).

    Field names are identical to the runner package's DeclaredChange.
    """

    object_type: str
    identifier: str
    field: str
    old_value: str = ""
    new_value: str = ""
    config_lines: list[str] = []


class RunnerResult(BaseModel):
    """Server-side copy of the runner package's RunnerResult (E2).

    Field names are identical so the AWX engine can validate a parsed
    VIREONET-RESULT block without importing the runner package.
    """

    status: Literal["successful", "failed"]
    step: str
    device: str
    output: dict[str, Any] = {}
    declared_changes: list[DeclaredChange] = []
    error: str | None = None
    duration_seconds: float = 0.0


class StepResult(BaseModel):
    status: StepStatus
    step: str
    device: str
    job_id: int | None = None
    log_ref: str = ""
    duration_seconds: float = 0.0
    output: dict[str, Any] = {}
    declared_changes: list[DeclaredChange] = []
    evidence: dict[str, Any] = {}

    @property
    def ok(self) -> bool:
        return self.status == StepStatus.SUCCESSFUL
