"""Pydantic response schemas for the /features API."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class DailyFeatureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: date

    # Trend
    ema20: Decimal | None
    ema50: Decimal | None
    ema200: Decimal | None
    sma20: Decimal | None
    trend_direction: str | None
    trend_strength: Decimal | None
    golden_cross: bool
    death_cross: bool

    # Momentum
    rsi14: Decimal | None
    macd_line: Decimal | None
    macd_signal: Decimal | None
    macd_histogram: Decimal | None
    adx14: Decimal | None
    plus_di14: Decimal | None
    minus_di14: Decimal | None
    momentum_score: Decimal | None

    # Volatility
    atr14: Decimal | None
    atr_expansion: bool
    atr_contraction: bool
    bb_upper: Decimal | None
    bb_middle: Decimal | None
    bb_lower: Decimal | None
    bb_width: Decimal | None
    kc_upper: Decimal | None
    kc_middle: Decimal | None
    kc_lower: Decimal | None
    volatility_squeeze: bool

    # Volume
    volume_ma20: int | None
    relative_volume: Decimal | None
    obv: Decimal | None
    volume_spike: bool
    volume_dry_up: bool
    accumulation_score: Decimal | None
    distribution_score: Decimal | None

    # Price action
    higher_high: bool
    higher_low: bool
    lower_high: bool
    lower_low: bool
    break_of_structure: str | None
    inside_bar: bool
    outside_bar: bool
    gap_up: bool
    gap_down: bool
    nr4: bool
    nr7: bool

    # Market structure
    swing_high: Decimal | None
    swing_low: Decimal | None
    trend_channel_upper: Decimal | None
    trend_channel_lower: Decimal | None
    is_range: bool
    is_consolidation: bool
    base_length_days: int | None
    range_width_pct: Decimal | None

    # Support / resistance
    support_level: Decimal | None
    resistance_level: Decimal | None
    pivot_point: Decimal | None
    breakout_level: Decimal | None
    pullback_zone_low: Decimal | None
    pullback_zone_high: Decimal | None

    # Patterns
    pattern_triangle: bool
    pattern_bull_flag: bool
    pattern_bear_flag: bool
    pattern_flat_base: bool
    pattern_ipo_base: bool
    pattern_rectangle: bool
    pattern_cup_handle: bool
    pattern_vcp: bool

    # Relative strength
    rs_vs_nifty: Decimal | None
    rs_vs_sector: Decimal | None
    sector_rank: int | None


class SessionFeatureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: date
    opening_range_high: Decimal | None
    opening_range_low: Decimal | None
    initial_balance_high: Decimal | None
    initial_balance_low: Decimal | None
    day_high: Decimal | None
    day_low: Decimal | None
    prev_day_high: Decimal | None
    prev_day_low: Decimal | None
    session_vwap: Decimal | None


class LatestFeaturesOut(BaseModel):
    symbol: str
    daily: DailyFeatureOut | None
    session: SessionFeatureOut | None


class FeatureHistoryOut(BaseModel):
    symbol: str
    history: list[DailyFeatureOut]


class FeatureStatusOut(BaseModel):
    symbols_with_features: int
    total_daily_feature_rows: int
    last_computed_at: datetime | None
