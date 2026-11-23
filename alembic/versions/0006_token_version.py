"""User token_version for JWT revocation on role/active change (F4)."""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"  # 0005_real_diff.py landed concurrently (F1); its revision id is "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("token_version", sa.Integer(), nullable=False, server_default="0")
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
