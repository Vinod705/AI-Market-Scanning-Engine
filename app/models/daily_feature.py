"""Daily feature-store ORM model.

One row per (symbol, date), computed once from that day's daily candle plus
trailing history. This is the "feature database" scanners will read from —
they should never need to recompute an indicator themselves.
"""

from datetime import date as date_
from datetime import datetime as datetime_
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class DailyFeature(Base):
    """Computed technical features for one symbol on one trading day."""

    __tablename__ = "daily_features"
    __table_args__ = (UniqueConstraint("symbol_id", "date", name="uq_daily_features_symbol_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol_id: Mapped[int] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE"), index=True, nullable=False
    )
    date: Mapped[date_] = mapped_column(Date, nullable=False, index=True)

    # --- Trend ---
    ema20: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    ema50: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    ema200: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    sma20: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    trend_direction: Mapped[str | None] = mapped_column(String(16))
    trend_strength: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    golden_cross: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    death_cross: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # --- Momentum ---
    rsi14: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    macd_line: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    macd_signal: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    macd_histogram: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    adx14: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    plus_di14: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    minus_di14: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    momentum_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))

    # --- Volatility ---
    atr14: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    atr_expansion: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    atr_contraction: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    bb_upper: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    bb_middle: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    bb_lower: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    bb_width: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    kc_upper: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    kc_middle: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    kc_lower: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    volatility_squeeze: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # --- Volume ---
    volume_ma20: Mapped[int | None] = mapped_column(BigInteger)
    relative_volume: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    obv: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    volume_spike: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    volume_dry_up: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    accumulation_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    distribution_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))

    # --- Price action ---
    higher_high: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    higher_low: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    lower_high: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    lower_low: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    break_of_structure: Mapped[str | None] = mapped_column(String(16))
    inside_bar: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    outside_bar: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    gap_up: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    gap_down: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    nr4: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    nr7: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # --- Market structure ---
    swing_high: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    swing_low: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    trend_channel_upper: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    trend_channel_lower: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    is_range: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_consolidation: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    base_length_days: Mapped[int | None] = mapped_column(Integer)
    range_width_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))

    # --- Support / resistance ---
    support_level: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    resistance_level: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    pivot_point: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    breakout_level: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    pullback_zone_low: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    pullback_zone_high: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))

    # --- Patterns (rule-based heuristic flags — see app/features/patterns/) ---
    pattern_triangle: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pattern_bull_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pattern_bear_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pattern_flat_base: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pattern_ipo_base: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pattern_rectangle: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pattern_cup_handle: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pattern_vcp: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # --- Relative strength ---
    rs_vs_nifty: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    rs_vs_sector: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    sector_rank: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"DailyFeature(symbol_id={self.symbol_id}, date={self.date})"
