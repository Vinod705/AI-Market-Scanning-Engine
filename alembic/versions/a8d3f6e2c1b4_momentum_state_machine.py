"""momentum state machine tables

Revision ID: a8d3f6e2c1b4
Revises: f2b6c9d4e1a7
Create Date: 2026-08-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8d3f6e2c1b4"
down_revision: str | None = "f2b6c9d4e1a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "momentum_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "symbol_id",
            sa.Integer(),
            sa.ForeignKey("symbols.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("score", sa.Numeric(6, 2), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_momentum_states_symbol_id", "momentum_states", ["symbol_id"], unique=True
    )

    op.create_table(
        "momentum_state_transitions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "symbol_id",
            sa.Integer(),
            sa.ForeignKey("symbols.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_state", sa.String(length=16), nullable=True),
        sa.Column("to_state", sa.String(length=16), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=False),
        sa.Column("score", sa.Numeric(6, 2), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_momentum_state_transitions_symbol_ts",
        "momentum_state_transitions",
        ["symbol_id", "timestamp"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_momentum_state_transitions_symbol_ts", table_name="momentum_state_transitions"
    )
    op.drop_table("momentum_state_transitions")
    op.drop_index("ix_momentum_states_symbol_id", table_name="momentum_states")
    op.drop_table("momentum_states")
