"""Workflow builder: flows, versions and runs."""

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def _ts(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=None if nullable else sa.func.now(),
    )


def upgrade() -> None:
    op.create_table(
        "flows",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("trigger_type", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("latest_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("published_version", sa.Integer(), nullable=True),
        sa.Column("webhook_token_hash", sa.String(128), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(128), nullable=False, server_default=""),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_index("ix_flows_trigger_type", "flows", ["trigger_type"])
    op.create_table(
        "flow_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("flow_id", sa.Uuid(), sa.ForeignKey("flows.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.JSON(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(128), nullable=False, server_default=""),
        _ts("created_at"),
        sa.UniqueConstraint("flow_id", "version", name="uq_flow_versions_flow_version"),
    )
    op.create_index("ix_flow_versions_flow_id", "flow_versions", ["flow_id"])
    op.create_table(
        "flow_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("flow_id", sa.Uuid(), sa.ForeignKey("flows.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("parent_run_id", sa.Uuid(), nullable=True),
        sa.Column("trigger_type", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("trigger_event", sa.JSON(), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("dedup_key", sa.String(255), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("log", sa.JSON(), nullable=False),
        sa.Column("variables", sa.JSON(), nullable=False),
        sa.Column("outputs", sa.JSON(), nullable=False),
        sa.Column("waiting", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("started_by", sa.String(128), nullable=False, server_default=""),
        _ts("created_at"),
        _ts("finished_at", nullable=True),
        sa.UniqueConstraint("flow_id", "dedup_key", name="uq_flow_runs_dedup"),
    )
    op.create_index("ix_flow_runs_flow_id", "flow_runs", ["flow_id"])
    op.create_index("ix_flow_runs_status", "flow_runs", ["status"])


def downgrade() -> None:
    op.drop_table("flow_runs")
    op.drop_table("flow_versions")
    op.drop_table("flows")
