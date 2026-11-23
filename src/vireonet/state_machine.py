from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from vireonet.change_state import ChangeState
from vireonet.models import AuditLog, Change

__all__ = ["ChangeState"]



TERMINAL_STATES: frozenset[ChangeState] = frozenset(
    {
        ChangeState.CLOSED,
        ChangeState.REJECTED,
        ChangeState.CI_FAILED,
        ChangeState.PRE_CHECK_FAILED,
        ChangeState.ROLLED_BACK,
        ChangeState.FAILED,
    }
)

# Exactly the transitions in the spec. Anything else is a bug, not a state.
LEGAL_TRANSITIONS: dict[ChangeState, frozenset[ChangeState]] = {
    ChangeState.RECEIVED: frozenset({ChangeState.VALIDATED, ChangeState.REJECTED, ChangeState.FAILED}),
    ChangeState.VALIDATED: frozenset({ChangeState.PLANNED, ChangeState.REJECTED, ChangeState.FAILED}),
    ChangeState.PLANNED: frozenset(
        {ChangeState.CI_PASSED, ChangeState.CI_FAILED, ChangeState.REJECTED, ChangeState.FAILED}
    ),
    ChangeState.CI_PASSED: frozenset({ChangeState.APPROVED, ChangeState.REJECTED, ChangeState.FAILED}),
    ChangeState.APPROVED: frozenset({ChangeState.PRE_CHECKED, ChangeState.REJECTED, ChangeState.FAILED}),
    ChangeState.PRE_CHECKED: frozenset(
        {ChangeState.APPLIED, ChangeState.PRE_CHECK_FAILED, ChangeState.FAILED}
    ),
    ChangeState.APPLIED: frozenset(
        {ChangeState.POST_CHECKED, ChangeState.ROLLED_BACK, ChangeState.FAILED}
    ),
    ChangeState.POST_CHECKED: frozenset(
        {ChangeState.CLOSED, ChangeState.ROLLED_BACK, ChangeState.FAILED}
    ),
    ChangeState.CLOSED: frozenset(),
    ChangeState.REJECTED: frozenset(),
    ChangeState.CI_FAILED: frozenset(),
    ChangeState.PRE_CHECK_FAILED: frozenset(),
    ChangeState.ROLLED_BACK: frozenset(),
    ChangeState.FAILED: frozenset(),
}


class IllegalTransitionError(ValueError):
    def __init__(self, from_state: ChangeState, to_state: ChangeState) -> None:
        super().__init__(f"Illegal transition: {from_state.value} -> {to_state.value}")
        self.from_state = from_state
        self.to_state = to_state


def is_legal(from_state: ChangeState, to_state: ChangeState) -> bool:
    return to_state in LEGAL_TRANSITIONS[from_state]


def assert_legal(from_state: ChangeState, to_state: ChangeState) -> None:
    if not is_legal(from_state, to_state):
        raise IllegalTransitionError(from_state, to_state)


async def record_transition(
    session: AsyncSession,
    change: Change,
    to_state: ChangeState,
    *,
    actor: str,
    reason: str = "",
    evidence: dict[str, Any] | None = None,
) -> AuditLog:
    """Validate, move the change, and append one audit row. Caller commits.

    Why one function: a state change without an audit row must be impossible.
    """
    from_state = ChangeState(change.state) if isinstance(change.state, str) else change.state
    assert_legal(from_state, to_state)
    change.state = to_state
    entry = AuditLog(
        change_id=change.id,
        from_state=from_state.value,
        to_state=to_state.value,
        actor=actor,
        reason=reason,
        evidence=evidence or {},
    )
    session.add(entry)
    session.add(change)
    return entry
