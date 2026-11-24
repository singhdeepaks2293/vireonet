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
    LargeBinary,
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


class ExcelWorkbook(Base):
    """An Excel config-push workbook: the original as received, and the
    updated copy with pre-change running configs in its "backup" sheet."""

    __tablename__ = "excel_workbooks"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ticket_number: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="original")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="upload")
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Flow(Base):
    """A stored workflow (flow): trigger + steps. Every save is a new version;
    triggers run the published version."""

    __tablename__ = "flows"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    trigger_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="manual", index=True
    )
    latest_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    published_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    webhook_token_hash: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FlowVersion(Base):
    __tablename__ = "flow_versions"
    __table_args__ = (UniqueConstraint("flow_id", "version", name="uq_flow_versions_flow_version"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    flow_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("flows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FlowRun(Base):
    __tablename__ = "flow_runs"
    __table_args__ = (UniqueConstraint("flow_id", "dedup_key", name="uq_flow_runs_dedup"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    flow_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("flows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    trigger_event: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # NULL for runs that must never be de-duplicated (manual, schedule).
    dedup_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued", index=True)
    log: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    variables: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    outputs: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    waiting: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    started_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AdapterOverride(Base):
    """An adapter setting saved from the UI (overrides the environment at start).
    Secret values are stored encrypted ({"enc": …}); others as {"v": …}."""

    __tablename__ = "adapter_overrides"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    secret: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PrePostProfile(Base):
    """A saved Pre/Post check profile (commands, tests, diff rules)."""

    __tablename__ = "prepost_profiles"

    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PrePostSnapshot(Base):
    """Command outputs (raw + TextFSM rows) of a profile on devices at one moment."""

    __tablename__ = "prepost_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    profile: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    label: Mapped[str] = mapped_column(
        String(16), nullable=False, default="adhoc"
    )  # pre | post | adhoc
    ticket: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    devices: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    results: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DemoState(Base):
    """Fake/demo-mode state shared by the API and the worker (e.g. the fake
    ITSM's ticket comments and status). Never used with real adapters."""

    __tablename__ = "demo_state"

    namespace: Mapped[str] = mapped_column(String(32), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


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
