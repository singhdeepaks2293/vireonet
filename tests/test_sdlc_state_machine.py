"""SDLC — Requirements R1: change state machine (M1).

Every legal transition in the spec, every illegal transition rejected,
and one append-only audit row per transition. Uses a real SQLite DB
via the conftest ``engine`` fixture; no network, no Temporal.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vireonet.change_state import ChangeState
from vireonet.models import AuditLog, Change
from vireonet.state_machine import (
    LEGAL_TRANSITIONS,
    TERMINAL_STATES,
    IllegalTransitionError,
    assert_legal,
    is_legal,
    record_transition,
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


def _change(state: ChangeState = ChangeState.RECEIVED) -> Change:
    return Change(
        ticket_number="RITM0001234",
        change_type="vlan_add",
        state=state,
        requester="alice",
        intent={},
        devices=["leaf-01"],
    )


@pytest.mark.parametrize("frm,to", HAPPY_PATH)
def test_happy_path_all_legal(frm: ChangeState, to: ChangeState) -> None:
    assert is_legal(frm, to)
    assert_legal(frm, to)  # does not raise


@pytest.mark.parametrize("frm", list(ChangeState))
def test_every_state_has_defined_transitions(frm: ChangeState) -> None:
    assert frm in LEGAL_TRANSITIONS
    assert frm not in LEGAL_TRANSITIONS[frm]  # no self-transitions


@pytest.mark.parametrize("frm,to", HAPPY_PATH)
def test_happy_path_skips_are_illegal(frm: ChangeState, to: ChangeState) -> None:
    # Skipping one step ahead (e.g. received -> planned) is never legal.
    idx = HAPPY_PATH.index((frm, to))
    if idx + 1 < len(HAPPY_PATH):
        skipped = HAPPY_PATH[idx + 1][1]
        assert not is_legal(frm, skipped)
        with pytest.raises(IllegalTransitionError):
            assert_legal(frm, skipped)


def test_all_failure_exits_reachable_and_terminal() -> None:
    # Each failure exit is reachable from at least one state and is terminal.
    for failure in (
        ChangeState.REJECTED,
        ChangeState.CI_FAILED,
        ChangeState.PRE_CHECK_FAILED,
        ChangeState.ROLLED_BACK,
        ChangeState.FAILED,
    ):
        assert failure in TERMINAL_STATES
        assert LEGAL_TRANSITIONS[failure] == frozenset()
        assert any(failure in targets for targets in LEGAL_TRANSITIONS.values())


def test_terminal_states_have_no_outgoing() -> None:
    for state in TERMINAL_STATES:
        for other in ChangeState:
            assert not is_legal(state, other)


def test_illegal_transition_error_carries_states() -> None:
    err = IllegalTransitionError(ChangeState.RECEIVED, ChangeState.CLOSED)
    assert err.from_state is ChangeState.RECEIVED
    assert err.to_state is ChangeState.CLOSED
    assert "received -> closed" in str(err)


async def _persist(session: AsyncSession, change: Change) -> Change:
    session.add(change)
    await session.flush()
    return change


async def test_record_transition_moves_state_and_writes_audit_row(engine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        change = await _persist(session, _change())
        entry = await record_transition(
            session,
            change,
            ChangeState.VALIDATED,
            actor="vireonet",
            reason="NetBox + policy passed",
            evidence={"devices_checked": ["leaf-01"]},
        )
        await session.commit()
        assert change.state == ChangeState.VALIDATED
        assert entry.from_state == "received"
        assert entry.to_state == "validated"
        assert entry.actor == "vireonet"
        assert entry.evidence == {"devices_checked": ["leaf-01"]}
        rows = (await session.execute(select(AuditLog))).scalars().all()
        assert len(rows) == 1
        assert rows[0].change_id == change.id


async def test_record_transition_illegal_writes_no_audit_row(engine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        change = await _persist(session, _change())
        with pytest.raises(IllegalTransitionError):
            await record_transition(session, change, ChangeState.CLOSED, actor="vireonet")
        await session.rollback()
        assert change.state == ChangeState.RECEIVED
        rows = (await session.execute(select(AuditLog))).scalars().all()
        assert rows == []


async def test_full_happy_path_produces_one_audit_row_per_step(engine) -> None:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        change = await _persist(session, _change())
        for _, to in HAPPY_PATH:
            await record_transition(session, change, to, actor="workflow")
        await session.commit()
        assert change.state == ChangeState.CLOSED
        rows = (
            (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.change_id == change.id)
                    .order_by(AuditLog.created_at)
                )
            )
            .scalars()
            .all()
        )
        assert [r.to_state for r in rows] == [t.value for _, t in HAPPY_PATH]
        assert [r.from_state for r in rows] == [f.value for f, _ in HAPPY_PATH]
