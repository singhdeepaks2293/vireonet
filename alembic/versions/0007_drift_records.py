"""Drift records for reconciliation (E3)."""

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "drift_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("device", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(32), nullable=False, server_default="detected"),
        sa.Column(
            "first_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "last_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("baseline_ref", sa.String(255), nullable=True),
        sa.Column("baseline_hash", sa.String(64), nullable=True),
        sa.Column("running_hash", sa.String(64), nullable=True),
        sa.Column("diff", sa.Text(), nullable=False, server_default=""),
        sa.Column("attributed_actor", sa.String(255), nullable=True),
        sa.Column("linked_ticket", sa.String(64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_drift_records_device", "drift_records", ["device"])
    op.create_index("ix_drift_records_linked_ticket", "drift_records", ["linked_ticket"])


def downgrade() -> None:
    op.drop_index("ix_drift_records_linked_ticket", table_name="drift_records")
    op.drop_index("ix_drift_records_device", table_name="drift_records")
    op.drop_table("drift_records")
