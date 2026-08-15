"""oi observations table

Revision ID: d4a7e1f3c8b6
Revises: c3e6f8a1d9b2
Create Date: 2026-08-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4a7e1f3c8b6"
down_revision: str | None = "c3e6f8a1d9b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oi_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "symbol_id",
            sa.Integer(),
            sa.ForeignKey("symbols.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("instrument_key", sa.String(length=64), nullable=False),
        sa.Column("instrument_type", sa.String(length=8), nullable=False),
        sa.Column("strike_price", sa.Numeric(14, 4), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price", sa.Numeric(14, 4), nullable=False),
        sa.Column("prev_price", sa.Numeric(14, 4), nullable=True),
        sa.Column("price_change_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("volume", sa.Integer(), nullable=False),
        sa.Column("oi", sa.Numeric(18, 2), nullable=False),
        sa.Column("prev_oi", sa.Numeric(18, 2), nullable=True),
        sa.Column("oi_change", sa.Numeric(18, 2), nullable=True),
        sa.Column("oi_change_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("classification", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_oi_observations_symbol_id", "oi_observations", ["symbol_id"])
    op.create_index("ix_oi_observations_instrument_key", "oi_observations", ["instrument_key"])
    op.create_index("ix_oi_observations_observed_at", "oi_observations", ["observed_at"])


def downgrade() -> None:
    op.drop_index("ix_oi_observations_observed_at", table_name="oi_observations")
    op.drop_index("ix_oi_observations_instrument_key", table_name="oi_observations")
    op.drop_index("ix_oi_observations_symbol_id", table_name="oi_observations")
    op.drop_table("oi_observations")
