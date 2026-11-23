"""Real diff: device config versions + per-change diffs (F1)."""

import sqlalchemy as sa

from alembic import op

# NOTE: this repo uses short numeric revisions ("0001".."0004"), not
# "<n>_<name>" — so this is "0005" with down_revision "0004".
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_config_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("device", sa.String(255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("ticket_number", sa.String(64), nullable=True),
        sa.Column("source", sa.String(64), nullable=False, server_default="awx-backup"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_device_config_versions_device", "device_config_versions", ["device"])
    op.create_index(
        "ix_device_config_versions_content_hash", "device_config_versions", ["content_hash"]
    )
    op.create_table(
        "change_diffs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "change_id", sa.Uuid(), sa.ForeignKey("changes.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("device", sa.String(255), nullable=False),
        sa.Column(
            "running_version_id",
            sa.Uuid(),
            sa.ForeignKey("device_config_versions.id"),
            nullable=True,
        ),
        sa.Column("candidate_hash", sa.String(64), nullable=False),
        sa.Column("diff_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("diff_lines", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("added", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("removed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_empty", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("change_id", "device", name="uq_change_diffs_change_device"),
    )
    op.create_index("ix_change_diffs_change_id", "change_diffs", ["change_id"])
    op.create_index("ix_change_diffs_device", "change_diffs", ["device"])


def downgrade() -> None:
    op.drop_index("ix_change_diffs_device", table_name="change_diffs")
    op.drop_index("ix_change_diffs_change_id", table_name="change_diffs")
    op.drop_table("change_diffs")
    op.drop_index("ix_device_config_versions_content_hash", table_name="device_config_versions")
    op.drop_index("ix_device_config_versions_device", table_name="device_config_versions")
    op.drop_table("device_config_versions")
