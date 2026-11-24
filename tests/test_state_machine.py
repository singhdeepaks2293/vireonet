from __future__ import annotations

import pytest

from vireonet.state_machine import (
    TERMINAL_STATES,
    ChangeState,
    IllegalTransitionError,
    assert_legal,
    is_legal,
)

HAPPY_PATH = [
    (ChangeState.RECEIVED, ChangeState.VALIDATED),
    (ChangeState.VALIDATED, ChangeState.PLANNED),
    (ChangeState.PLANNED, ChangeState.CI_PASSED),
    (ChangeState.CI_PASSED, ChangeState.APPROVED),
    (ChangeState.APPROVED, ChangeState.PRE_CHECKED),
    (ChangeState.PRE_CHECKED, ChangeState.APPLIED),
    (ChangeState.APPLIED, ChangeState.POST_CHECKED),
    (ChangeState.POST_CHECKED, ChangeState.RECONCILING),
    (ChangeState.RECONCILING, ChangeState.RECONCILED),
    (ChangeState.RECONCILED, ChangeState.CLOSED),
]


@pytest.mark.parametrize("frm,to", HAPPY_PATH)
def test_happy_path_is_legal(frm: ChangeState, to: ChangeState) -> None:
    assert is_legal(frm, to)
    assert_legal(frm, to)


@pytest.mark.parametrize(
    "frm,to",
    [
        (ChangeState.RECEIVED, ChangeState.APPROVED),  # skipping validation/planning
        (ChangeState.RECEIVED, ChangeState.CLOSED),
        (ChangeState.CLOSED, ChangeState.RECEIVED),  # terminal is terminal
        (ChangeState.ROLLED_BACK, ChangeState.APPLIED),
        (ChangeState.PLANNED, ChangeState.APPLIED),  # CI gate skipped
        (ChangeState.APPROVED, ChangeState.APPLIED),  # pre-check skipped
    ],
)
def test_illegal_transitions_rejected(frm: ChangeState, to: ChangeState) -> None:
    assert not is_legal(frm, to)
    with pytest.raises(IllegalTransitionError):
        assert_legal(frm, to)


def test_failure_exits_are_terminal() -> None:
    assert {
        ChangeState.CLOSED,
        ChangeState.REJECTED,
        ChangeState.CI_FAILED,
        ChangeState.PRE_CHECK_FAILED,
        ChangeState.ROLLED_BACK,
        ChangeState.ROLLBACK_FAILED,
        ChangeState.FAILED,
    } == TERMINAL_STATES
    for s in TERMINAL_STATES:
        assert not is_legal(s, ChangeState.RECEIVED)


@pytest.mark.parametrize(
    "frm,to",
    [
        (ChangeState.PRE_CHECKED, ChangeState.REJECTED),  # operator abort before apply
        (ChangeState.PRE_CHECKED, ChangeState.ROLLED_BACK),  # apply failed part-way
        (ChangeState.PRE_CHECKED, ChangeState.ROLLBACK_FAILED),
        (ChangeState.APPLIED, ChangeState.ROLLBACK_FAILED),
        (ChangeState.POST_CHECKED, ChangeState.ROLLBACK_FAILED),
    ],
)
def test_abort_and_rollback_outcomes_are_legal(frm: ChangeState, to: ChangeState) -> None:
    assert is_legal(frm, to)


def test_rollback_failed_is_not_rolled_back() -> None:
    assert ChangeState.ROLLBACK_FAILED != ChangeState.ROLLED_BACK
    assert not is_legal(ChangeState.ROLLBACK_FAILED, ChangeState.ROLLED_BACK)
