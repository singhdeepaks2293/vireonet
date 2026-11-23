from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from vireonet.adapters.protocols import AwxProtocol, NetBoxProtocol
from vireonet.intent import ChangeIntent

CheckStatus = Literal["pass", "fail", "skip"]


@dataclass
class CheckContext:
    intent: ChangeIntent
    ticket_number: str
    netbox: NetBoxProtocol
    awx: AwxProtocol
    backups: dict[str, int] = field(default_factory=dict)  # device -> AWX backup job id
    baseline: dict[str, object] = field(default_factory=dict)
    locks_held_by: str = ""  # ticket holding the locks, for the lock check


@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    message: str = ""
    evidence: dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in {"pass", "skip"}


class Check(Protocol):
    name: str

    async def run(self, ctx: CheckContext) -> CheckResult: ...


@dataclass
class CheckEngine:
    """Runs a declared list of checks, in order. Stops at the first failure
    only if fail_fast is set; otherwise collects all evidence."""

    fail_fast: bool = False

    async def run(self, checks: list[Check], ctx: CheckContext) -> list[CheckResult]:
        results: list[CheckResult] = []
        for check in checks:
            result = await check.run(ctx)
            results.append(result)
            if result.status == "fail" and self.fail_fast:
                break
        return results

    @staticmethod
    def all_pass(results: list[CheckResult]) -> bool:
        return all(r.ok for r in results)
