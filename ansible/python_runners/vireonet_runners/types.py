"""Runner request/result types (E2).

Field names are identical to the server-side copies in
src/vireonet/execution/types.py, so the AWX engine can validate a parsed
VIREONET-RESULT block without importing this package.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, model_validator

StepName = Literal[
    "backup", "pre_check", "dry_run", "apply", "post_check", "rollback", "collect_state"
]

_SECRET_SUBSTRINGS = ("secret", "token", "password", "private_key", "api_key")


class DeclaredChange(BaseModel):
    object_type: str
    identifier: str
    field: str
    old_value: str = ""
    new_value: str = ""
    config_lines: list[str] = []


class RunnerRequest(BaseModel):
    ticket_number: str
    device: str
    platform: str
    mgmt_ip: str = ""
    step: StepName
    change_type: str = ""
    rendered_config: str = ""
    diff: str = ""
    extra_vars: dict[str, str] = {}
    dry_run: bool = True
    timeout_seconds: int = 600

    @model_validator(mode="after")
    def _no_secret_extra_vars(self) -> RunnerRequest:
        bad = [k for k in self.extra_vars if any(s in k.lower() for s in _SECRET_SUBSTRINGS)]
        if bad:
            raise ValueError(f"extra_vars must not contain secrets: {bad}")
        return self


class RunnerResult(BaseModel):
    status: Literal["successful", "failed"]
    step: str
    device: str
    output: dict[str, Any] = {}
    declared_changes: list[DeclaredChange] = []
    error: str | None = None
    duration_seconds: float = 0.0
