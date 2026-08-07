"""feature tables: daily_features, session_features

Revision ID: b88081472870
Revises: 686f8df2789c
Create Date: 2026-08-07

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b88081472870"
down_revision: str | None = "686f8df2789c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "daily_features",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol_id", sa.Integer(), sa.ForeignKey("symbols.id", ondelete="CASCADE"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        # Trend
        sa.Column("ema20", sa.Numeric(14, 4), nullable=True),
        sa.Column("ema50", sa.Numeric(14, 4), nullable=True),
        sa.Column("ema200", sa.Numeric(14, 4), nullable=True),
        sa.Column("sma20", sa.Numeric(14, 4), nullable=True),
        sa.Column("trend_direction", sa.String(16), nullable=True),
        sa.Column("trend_strength", sa.Numeric(6, 2), nullable=True),
        sa.Column("golden_cross", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("death_cross", sa.Boolean(), nullable=False, server_default=sa.false()),
        # Momentum
        sa.Column("rsi14", sa.Numeric(6, 2), nullable=True),
        sa.Column("macd_line", sa.Numeric(14, 4), nullable=True),
        sa.Column("macd_signal", sa.Numeric(14, 4), nullable=True),
        sa.Column("macd_histogram", sa.Numeric(14, 4), nullable=True),
        sa.Column("adx14", sa.Numeric(6, 2), nullable=True),
        sa.Column("plus_di14", sa.Numeric(6, 2), nullable=True),
        sa.Column("minus_di14", sa.Numeric(6, 2), nullable=True),
        sa.Column("momentum_score", sa.Numeric(6, 2), nullable=True),
        # Volatility
        sa.Column("atr14", sa.Numeric(14, 4), nullable=True),
        sa.Column("atr_expansion", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("atr_contraction", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("bb_upper", sa.Numeric(14, 4), nullable=True),
        sa.Column("bb_middle", sa.Numeric(14, 4), nullable=True),
        sa.Column("bb_lower", sa.Numeric(14, 4), nullable=True),
        sa.Column("bb_width", sa.Numeric(8, 4), nullable=True),
        sa.Column("kc_upper", sa.Numeric(14, 4), nullable=True),
        sa.Column("kc_middle", sa.Numeric(14, 4), nullable=True),
        sa.Column("kc_lower", sa.Numeric(14, 4), nullable=True),
        sa.Column("volatility_squeeze", sa.Boolean(), nullable=False, server_default=sa.false()),
        # Volume
        sa.Column("volume_ma20", sa.BigInteger(), nullable=True),
        sa.Column("relative_volume", sa.Numeric(8, 4), nullable=True),
        sa.Column("obv", sa.Numeric(20, 4), nullable=True),
        sa.Column("volume_spike", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("volume_dry_up", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("accumulation_score", sa.Numeric(6, 2), nullable=True),
        sa.Column("distribution_score", sa.Numeric(6, 2), nullable=True),
        # Price action
        sa.Column("higher_high", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("higher_low", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("lower_high", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("lower_low", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("break_of_structure", sa.String(16), nullable=True),
        sa.Column("inside_bar", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("outside_bar", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("gap_up", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("gap_down", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("nr4", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("nr7", sa.Boolean(), nullable=False, server_default=sa.false()),
        # Market structure
        sa.Column("swing_high", sa.Numeric(14, 4), nullable=True),
        sa.Column("swing_low", sa.Numeric(14, 4), nullable=True),
        sa.Column("trend_channel_upper", sa.Numeric(14, 4), nullable=True),
        sa.Column("trend_channel_lower", sa.Numeric(14, 4), nullable=True),
        sa.Column("is_range", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_consolidation", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("base_length_days", sa.Integer(), nullable=True),
        sa.Column("range_width_pct", sa.Numeric(8, 4), nullable=True),
        # Support / resistance
        sa.Column("support_level", sa.Numeric(14, 4), nullable=True),
        sa.Column("resistance_level", sa.Numeric(14, 4), nullable=True),
        sa.Column("pivot_point", sa.Numeric(14, 4), nullable=True),
        sa.Column("breakout_level", sa.Numeric(14, 4), nullable=True),
        sa.Column("pullback_zone_low", sa.Numeric(14, 4), nullable=True),
        sa.Column("pullback_zone_high", sa.Numeric(14, 4), nullable=True),
        # Patterns
        sa.Column("pattern_triangle", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pattern_bull_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pattern_bear_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pattern_flat_base", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pattern_ipo_base", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pattern_rectangle", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pattern_cup_handle", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pattern_vcp", sa.Boolean(), nullable=False, server_default=sa.false()),
        # Relative strength
        sa.Column("rs_vs_nifty", sa.Numeric(8, 4), nullable=True),
        sa.Column("rs_vs_sector", sa.Numeric(8, 4), nullable=True),
        sa.Column("sector_rank", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("symbol_id", "date", name="uq_daily_features_symbol_date"),
    )
    op.create_index("ix_daily_features_symbol_id", "daily_features", ["symbol_id"])
    op.create_index("ix_daily_features_date", "daily_features", ["date"])

    op.create_table(
        "session_features",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol_id", sa.Integer(), sa.ForeignKey("symbols.id", ondelete="CASCADE"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("opening_range_high", sa.Numeric(14, 4), nullable=True),
        sa.Column("opening_range_low", sa.Numeric(14, 4), nullable=True),
        sa.Column("initial_balance_high", sa.Numeric(14, 4), nullable=True),
        sa.Column("initial_balance_low", sa.Numeric(14, 4), nullable=True),
        sa.Column("day_high", sa.Numeric(14, 4), nullable=True),
        sa.Column("day_low", sa.Numeric(14, 4), nullable=True),
        sa.Column("prev_day_high", sa.Numeric(14, 4), nullable=True),
        sa.Column("prev_day_low", sa.Numeric(14, 4), nullable=True),
        sa.Column("session_vwap", sa.Numeric(14, 4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("symbol_id", "date", name="uq_session_features_symbol_date"),
    )
    op.create_index("ix_session_features_symbol_id", "session_features", ["symbol_id"])
    op.create_index("ix_session_features_date", "session_features", ["date"])


def downgrade() -> None:
    op.drop_index("ix_session_features_date", table_name="session_features")
    op.drop_index("ix_session_features_symbol_id", table_name="session_features")
    op.drop_table("session_features")
    op.drop_index("ix_daily_features_date", table_name="daily_features")
    op.drop_index("ix_daily_features_symbol_id", table_name="daily_features")
    op.drop_table("daily_features")
