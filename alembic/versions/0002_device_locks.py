"""Device locks for M4."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_locks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("device", sa.String(255), nullable=False),
        sa.Column("ticket_number", sa.String(64), nullable=False),
        sa.Column("holder", sa.String(255), nullable=False, server_default="temporal"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("device", name="uq_device_locks_device"),
    )
    op.create_index("ix_device_locks_device", "device_locks", ["device"])
    op.create_index("ix_device_locks_ticket_number", "device_locks", ["ticket_number"])


def downgrade() -> None:
    op.drop_table("device_locks")
