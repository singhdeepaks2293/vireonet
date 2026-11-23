"""ZTP devices for M6."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ztp_devices",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("serial", sa.String(255), nullable=False),
        sa.Column("hostname", sa.String(255), nullable=False),
        sa.Column("mac_address", sa.String(32), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("site", sa.String(255), nullable=False),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("version", sa.String(128), nullable=True),
        sa.Column("stage", sa.String(64), nullable=False, server_default="registered"),
        sa.Column("ticket_number", sa.String(64), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_used", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("callback_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("serial", name="uq_ztp_devices_serial"),
    )
    op.create_index("ix_ztp_devices_serial", "ztp_devices", ["serial"])
    op.create_index("ix_ztp_devices_ticket_number", "ztp_devices", ["ticket_number"])


def downgrade() -> None:
    op.drop_table("ztp_devices")
