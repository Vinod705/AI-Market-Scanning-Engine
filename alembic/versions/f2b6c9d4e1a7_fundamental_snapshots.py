"""fundamental snapshots table

Revision ID: f2b6c9d4e1a7
Revises: d4a7e1f3c8b6
Create Date: 2026-08-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2b6c9d4e1a7"
down_revision: str | None = "d4a7e1f3c8b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fundamental_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "symbol_id",
            sa.Integer(),
            sa.ForeignKey("symbols.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("source", sa.String(length=32), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("as_of", sa.Date(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_checked_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_fundamental_snapshots_symbol_id", "fundamental_snapshots", ["symbol_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_fundamental_snapshots_symbol_id", table_name="fundamental_snapshots")
    op.drop_table("fundamental_snapshots")
