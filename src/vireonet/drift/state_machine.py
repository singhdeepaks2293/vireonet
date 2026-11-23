"""Drift-record state machine (D1).

Mirrors vireonet.state_machine: a dict of legal transitions plus one
helper that validates, moves, and appends an audit row. A state change
without an audit row must be impossible, so callers go through
transition_drift and commit afterwards.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from vireonet.drift.models import DriftAuditLog
from vireonet.models import DriftRecord

DRIFT_TRANSITIONS: dict[str, frozenset[str]] = {
    "detected": frozenset({"under_review", "resolved"}),
    "under_review": frozenset({"reverting", "merge_pending", "exception_active", "resolved"}),
    "reverting": frozenset({"resolved", "under_review"}),
    "merge_pending": frozenset({"resolved", "under_review"}),
    "exception_active": frozenset({"exception_expired", "resolved"}),
    "exception_expired": frozenset({"under_review"}),
    "resolved": frozenset(),
}


class IllegalDriftTransitionError(ValueError):
    def __init__(self, from_state: str, to_state: str) -> None:
        super().__init__(f"Illegal drift transition: {from_state} -> {to_state}")
        self.from_state = from_state
        self.to_state = to_state


def is_legal_drift_transition(from_state: str, to_state: str) -> bool:
    return to_state in DRIFT_TRANSITIONS.get(from_state, frozenset())


async def transition_drift(
    session: AsyncSession,
    record: DriftRecord,
    to_state: str,
    actor: str,
    reason: str = "",
) -> DriftAuditLog:
    """Validate, move the record, and append one audit row. Caller commits."""
    from_state = str(record.status)
    if not is_legal_drift_transition(from_state, to_state):
        raise IllegalDriftTransitionError(from_state, to_state)
    record.status = to_state
    record.last_seen = datetime.now(UTC)
    entry = DriftAuditLog(
        record_id=record.id,
        from_state=from_state,
        to_state=to_state,
        actor=actor,
        reason=reason,
    )
    session.add(entry)
    session.add(record)
    return entry
