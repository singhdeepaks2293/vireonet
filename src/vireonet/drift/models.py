"""Drift data model (D1).

DriftRecord itself lives in vireonet.models (E3 created it there, and D1
extends it with ``attributed_at``). The tables defined here are new in D1:
one row per scan run, one row per operator decision on a record, one row
per ignore rule. All three are append-only in practice: rows are created
and read, never updated in place (a decision supersedes by adding a row;
a rule is retired by deleting it, which is an operator action, not an edit).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from vireonet.db import Base


class DriftScan(Base):
    """One drift scan run over a site (or a single device)."""

    __tablename__ = "drift_scans"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    site: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=lambda: datetime.now(UTC)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    devices_scanned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    devices_with_drift: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    devices_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DriftDecision(Base):
    """One operator decision on a drift record (rollback / merge / exception).

    Append-only: a later decision does not rewrite this row; the record's
    status is moved by drift.state_machine.transition_drift, which writes
    a DriftAuditLog row for the move.
    """

    __tablename__ = "drift_decisions"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    record_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("drift_records.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ticket_ref: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DriftIgnoreRule(Base):
    """One operator-defined ignore rule (regex, per platform / scope)."""

    __tablename__ = "drift_ignore_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pattern: Mapped[str] = mapped_column(String(1024), nullable=False)
    platform: Mapped[str] = mapped_column(String(64), nullable=False, default="*")
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="global")
    target: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DriftAuditLog(Base):
    """Append-only audit trail for drift-record state moves.

    Mirrors vireonet.models.AuditLog, but AuditLog requires a change_id FK
    to changes — a drift record has no change — so drift moves get their
    own table with the same shape (from/to/actor/reason/created_at).
    """

    __tablename__ = "drift_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    record_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("drift_records.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_state: Mapped[str] = mapped_column(String(32), nullable=False)
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
