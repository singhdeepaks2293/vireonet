"""Initial: changes + append-only audit_log."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "changes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("ticket_number", sa.String(64), nullable=False),
        sa.Column("change_type", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="received"),
        sa.Column("requester", sa.String(255), nullable=False, server_default=""),
        sa.Column("intent", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("devices", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("ticket_number", name="uq_changes_ticket_number"),
    )
    op.create_index("ix_changes_ticket_number", "changes", ["ticket_number"])
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("change_id", sa.Uuid(), sa.ForeignKey("changes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_state", sa.String(32), nullable=False),
        sa.Column("to_state", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("reason", sa.String(1024), nullable=False, server_default=""),
        sa.Column("evidence", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_audit_log_change_id", "audit_log", ["change_id"])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("changes")
