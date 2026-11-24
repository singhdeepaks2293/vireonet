"""Change states.

Split out of state_machine.py so models.py can use ChangeState without a
circular import (state_machine imports the models for record_transition).
"""

from __future__ import annotations

from enum import StrEnum


class ChangeState(StrEnum):
    RECEIVED = "received"
    VALIDATED = "validated"
    PLANNED = "planned"
    CI_PASSED = "ci_passed"
    APPROVED = "approved"
    PRE_CHECKED = "pre_checked"
    APPLIED = "applied"
    POST_CHECKED = "post_checked"
    RECONCILING = "reconciling"
    RECONCILIATION_PENDING = "reconciliation_pending"
    RECONCILED = "reconciled"
    CLOSED = "closed"
    # Failure exits (terminal)
    REJECTED = "rejected"
    CI_FAILED = "ci_failed"
    PRE_CHECK_FAILED = "pre_check_failed"
    ROLLED_BACK = "rolled_back"
    # The rollback itself failed: devices may be half-changed and need a
    # human. Terminal, and deliberately distinct from rolled_back so a
    # failed restore is never reported as a successful one.
    ROLLBACK_FAILED = "rollback_failed"
    FAILED = "failed"
