from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from vireonet.intent import parse_intent
from vireonet.models import AuditLog, Change
from vireonet.state_machine import ChangeState, record_transition

BASE = {
    "devices": ["leaf-01"],
    "site": "dc1",
    "ticket_number": "RITM0001234",
    "requested_by": "alice",
}


async def test_change_and_audit_persisted(session: AsyncSession) -> None:
    intent = parse_intent({**BASE, "change_type": "vlan_add", "vlan_id": 100, "vlan_name": "web"})
    change = Change(
        ticket_number="RITM0001234",
        change_type=intent.change_type,
        state=ChangeState.RECEIVED,
        requester="alice",
        intent=intent.model_dump(mode="json"),
        devices=["leaf-01"],
    )
    session.add(change)
    await session.flush()
    await record_transition(
        session,
        change,
        ChangeState.VALIDATED,
        actor="vireonet",
        reason="NetBox objects exist",
        evidence={"device": "leaf-01"},
    )
    await session.commit()

    rows = (await session.execute(select(AuditLog))).scalars().all()
    assert len(rows) == 1
    assert rows[0].from_state == "received" and rows[0].to_state == "validated"
    assert rows[0].actor == "vireonet"
    assert change.state == ChangeState.VALIDATED


async def test_ticket_number_unique(session: AsyncSession) -> None:
    session.add(
        Change(
            ticket_number="RITM1",
            change_type="vlan_add",
            state=ChangeState.RECEIVED,
            intent={},
            devices=[],
        )
    )
    await session.flush()
    session.add(
        Change(
            ticket_number="RITM1",
            change_type="vlan_add",
            state=ChangeState.RECEIVED,
            intent={},
            devices=[],
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()
