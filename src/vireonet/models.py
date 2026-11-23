from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from vireonet.change_state import ChangeState
from vireonet.db import Base


class Change(Base):
    __tablename__ = "changes"
    __table_args__ = (UniqueConstraint("ticket_number", name="uq_changes_ticket_number"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ticket_number: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    change_type: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[ChangeState] = mapped_column(
        String(32), nullable=False, default=ChangeState.RECEIVED
    )
    requester: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    intent: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    devices: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    transitions: Mapped[list[AuditLog]] = relationship(
        "AuditLog", back_populates="change", order_by="AuditLog.created_at"
    )


class DeviceConfigVersion(Base):
    """One stored running-config version for a device (F1: real diff source)."""

    __tablename__ = "device_config_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    device: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    version: Mapped[int] = mapped_column(nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ticket_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="awx-backup")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChangeDiff(Base):
    """Stored running-vs-candidate diff for one device on one change (F1)."""

    __tablename__ = "change_diffs"
    __table_args__ = (
        UniqueConstraint("change_id", "device", name="uq_change_diffs_change_device"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    change_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("changes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    device: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    running_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("device_config_versions.id"), nullable=True
    )
    candidate_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    diff_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    diff_lines: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    added: Mapped[int] = mapped_column(nullable=False, default=0)
    removed: Mapped[int] = mapped_column(nullable=False, default=0)
    is_empty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DriftRecord(Base):
    """One detected drift between the baseline and a device's running config (E3).

    D1 will extend this table; E3 only needs the fields below.
    """

    __tablename__ = "drift_records"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    device: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False, default="medium")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="detected")
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    baseline_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    baseline_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    running_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    diff: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attributed_actor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # D1: when the actor was attributed (None when attribution is unknown).
    attributed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    linked_ticket: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    change_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("changes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_state: Mapped[str] = mapped_column(String(32), nullable=False)
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    change: Mapped[Change] = relationship("Change", back_populates="transitions")


class DeviceLock(Base):
    __tablename__ = "device_locks"
    __table_args__ = (UniqueConstraint("device", name="uq_device_locks_device"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    device: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    ticket_number: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    holder: Mapped[str] = mapped_column(String(255), nullable=False, default="temporal")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ZtpDevice(Base):
    """One ZTP device. Token is stored as a SHA-256 hash only, never plaintext."""

    __tablename__ = "ztp_devices"
    __table_args__ = (UniqueConstraint("serial", name="uq_ztp_devices_serial"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    serial: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    mac_address: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)  # ios | ios-xe | nxos
    site: Mapped[str] = mapped_column(String(255), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    stage: Mapped[str] = mapped_column(String(64), nullable=False, default="registered")
    ticket_number: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    token_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    token_used: Mapped[bool] = mapped_column(default=False)
    callback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="viewer", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Token version: bumped by UserService.set_role/set_active; JWTs carry
    # the version they were minted with ("tv" claim) and are rejected once
    # the stored version moves past it.
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    auth_provider: Mapped[str] = mapped_column(String(32), nullable=False, default="local")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
