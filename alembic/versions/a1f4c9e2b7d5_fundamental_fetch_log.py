"""fundamental fetch log table

Revision ID: a1f4c9e2b7d5
Revises: e7c9a2b4f1d3
Create Date: 2026-08-09

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1f4c9e2b7d5"
down_revision: str | None = "e7c9a2b4f1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fundamental_fetch_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "symbol_id",
            sa.Integer(),
            sa.ForeignKey("symbols.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_fundamental_fetch_log_requested_at", "fundamental_fetch_log", ["requested_at"]
    )
    op.create_index(
        "ix_fundamental_fetch_log_symbol_requested",
        "fundamental_fetch_log",
        ["symbol_id", "requested_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_fundamental_fetch_log_symbol_requested", table_name="fundamental_fetch_log")
    op.drop_index("ix_fundamental_fetch_log_requested_at", table_name="fundamental_fetch_log")
    op.drop_table("fundamental_fetch_log")
