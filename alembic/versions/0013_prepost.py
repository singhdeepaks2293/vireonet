"""Pre/Post check profiles and snapshots."""

import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prepost_profiles",
        sa.Column("name", sa.String(80), primary_key=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_by", sa.String(128), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "prepost_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("profile", sa.String(80), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("label", sa.String(16), nullable=False, server_default="adhoc"),
        sa.Column("ticket", sa.String(64), nullable=False, server_default=""),
        sa.Column("devices", sa.JSON(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_prepost_snapshots_profile", "prepost_snapshots", ["profile"])
    op.create_index("ix_prepost_snapshots_ticket", "prepost_snapshots", ["ticket"])


def downgrade() -> None:
    op.drop_table("prepost_snapshots")
    op.drop_table("prepost_profiles")
