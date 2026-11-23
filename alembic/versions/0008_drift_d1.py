"""Drift data model (D1): scan runs, decisions, ignore rules, audit log.

drift_records already exists from 0007 with every D1 column except
attributed_at, which is added here (guarded: the migration must apply
cleanly on a DB at 0007, and must not add a column twice).
"""

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(col["name"] == column for col in inspector.get_columns(table))


def upgrade() -> None:
    op.create_table(
        "drift_scans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("site", sa.String(255), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("devices_scanned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("devices_with_drift", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("devices_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_drift_scans_site", "drift_scans", ["site"])
    op.create_table(
        "drift_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("ticket_ref", sa.String(64), nullable=False, server_default=""),
        sa.Column("expiry", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["record_id"], ["drift_records.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_drift_decisions_record_id", "drift_decisions", ["record_id"])
    op.create_table(
        "drift_ignore_rules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("pattern", sa.String(1024), nullable=False),
        sa.Column("platform", sa.String(64), nullable=False, server_default="*"),
        sa.Column("scope", sa.String(32), nullable=False, server_default="global"),
        sa.Column("target", sa.String(255), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(255), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "drift_audit_log",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("from_state", sa.String(32), nullable=False),
        sa.Column("to_state", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("reason", sa.String(1024), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["record_id"], ["drift_records.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_drift_audit_log_record_id", "drift_audit_log", ["record_id"])
    if not _has_column("drift_records", "attributed_at"):
        op.add_column(
            "drift_records", sa.Column("attributed_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    op.drop_index("ix_drift_audit_log_record_id", table_name="drift_audit_log")
    op.drop_table("drift_audit_log")
    op.drop_table("drift_ignore_rules")
    op.drop_index("ix_drift_decisions_record_id", table_name="drift_decisions")
    op.drop_table("drift_decisions")
    op.drop_index("ix_drift_scans_site", table_name="drift_scans")
    op.drop_table("drift_scans")
    if _has_column("drift_records", "attributed_at"):
        op.drop_column("drift_records", "attributed_at")
