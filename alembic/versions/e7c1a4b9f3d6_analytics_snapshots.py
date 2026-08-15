"""market regime + sector rrg snapshot tables (Phase 15)

Revision ID: e7c1a4b9f3d6
Revises: a8d3f6e2c1b4
Create Date: 2026-08-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7c1a4b9f3d6"
down_revision: str | None = "a8d3f6e2c1b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_regime_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("regime", sa.String(length=16), nullable=True),
        sa.Column("score", sa.Numeric(6, 2), nullable=True),
        sa.Column("index_symbol", sa.String(length=32), nullable=False),
        sa.Column("index_trend_direction", sa.String(length=16), nullable=True),
        sa.Column("index_trend_strength", sa.Numeric(6, 2), nullable=True),
        sa.Column("volatility_state", sa.String(length=16), nullable=True),
        sa.Column("sector_leading_pct", sa.Numeric(6, 2), nullable=True),
        sa.Column("evidence_sources_used", sa.JSON(), nullable=False),
        sa.Column("missing_evidence", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_market_regime_snapshots_computed_at", "market_regime_snapshots", ["computed_at"]
    )

    op.create_table(
        "sector_rrg_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sector_symbol", sa.String(length=32), nullable=False),
        sa.Column("benchmark_symbol", sa.String(length=32), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("rs_ratio", sa.Numeric(10, 4), nullable=True),
        sa.Column("rs_momentum", sa.Numeric(10, 4), nullable=True),
        sa.Column("rotation_state", sa.String(length=16), nullable=True),
        sa.Column("momentum_score", sa.Numeric(6, 2), nullable=True),
        sa.Column("trend_strength", sa.Numeric(6, 2), nullable=True),
        sa.Column("price_performance_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("score", sa.Numeric(6, 2), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_sector_rrg_snapshots_computed_at", "sector_rrg_snapshots", ["computed_at"]
    )
    op.create_index(
        "ix_sector_rrg_snapshots_sector_symbol", "sector_rrg_snapshots", ["sector_symbol"]
    )


def downgrade() -> None:
    op.drop_index("ix_sector_rrg_snapshots_sector_symbol", table_name="sector_rrg_snapshots")
    op.drop_index("ix_sector_rrg_snapshots_computed_at", table_name="sector_rrg_snapshots")
    op.drop_table("sector_rrg_snapshots")
    op.drop_index("ix_market_regime_snapshots_computed_at", table_name="market_regime_snapshots")
    op.drop_table("market_regime_snapshots")
