"""Excel config-push workbooks (original and updated-with-backups copies)."""

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "excel_workbooks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("ticket_number", sa.String(64), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="original"),
        sa.Column("source", sa.String(32), nullable=False, server_default="upload"),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_excel_workbooks_ticket_number", "excel_workbooks", ["ticket_number"])


def downgrade() -> None:
    op.drop_index("ix_excel_workbooks_ticket_number", table_name="excel_workbooks")
    op.drop_table("excel_workbooks")
