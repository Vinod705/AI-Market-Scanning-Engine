"""Adapts the existing Phase 3 feature-store rows into the plain-value
shape `app.technical.scorer.TechnicalScorer` scores — kept as a thin,
explicit conversion instead of scoring off the ORM rows directly so the
scorer stays unit-testable without a database.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.models.daily_feature import DailyFeature
from app.models.session_feature import SessionFeature


def _f(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


@dataclass
class TechnicalInputs:
    price: float

    ema20: float | None
    ema50: float | None
    trend_direction: str | None
    trend_strength: float | None

    rsi14: float | None
    macd_histogram: float | None
    adx14: float | None

    relative_volume: float | None
    accumulation_score: float | None
    distribution_score: float | None

    volatility_squeeze: bool
    atr_expansion: bool

    session_vwap: float | None

    higher_high: bool
    higher_low: bool

    @classmethod
    def from_models(
        cls,
        *,
        price: float,
        daily: DailyFeature,
        session: SessionFeature | None,
    ) -> "TechnicalInputs":
        return cls(
            price=price,
            ema20=_f(daily.ema20),
            ema50=_f(daily.ema50),
            trend_direction=daily.trend_direction,
            trend_strength=_f(daily.trend_strength),
            rsi14=_f(daily.rsi14),
            macd_histogram=_f(daily.macd_histogram),
            adx14=_f(daily.adx14),
            relative_volume=_f(daily.relative_volume),
            accumulation_score=_f(daily.accumulation_score),
            distribution_score=_f(daily.distribution_score),
            volatility_squeeze=daily.volatility_squeeze,
            atr_expansion=daily.atr_expansion,
            session_vwap=_f(session.session_vwap) if session is not None else None,
            higher_high=daily.higher_high,
            higher_low=daily.higher_low,
        )
