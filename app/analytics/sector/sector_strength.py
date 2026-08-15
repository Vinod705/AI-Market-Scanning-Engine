"""Multi-evidence sector strength: fetches a sector index's real RRG state,
already-computed technical features, and price history, and combines them
into one normalized `SectorEvidence` — deterministic, no LLM.

**Why not "index is green -> strong"**: none of the evidence here is raw
day's price direction. `rs_ratio`/`rs_momentum` measure standing/trend
*relative to the benchmark*, not absolute direction. `price_performance_pct`
and `momentum_acceleration` are reported as raw, human-readable numbers,
but the score built from them uses `rrg_engine.rolling_zscore` — each
measured against *that sector's own recent distribution*, so a green day
that's completely ordinary for that sector's volatility scores near
neutral, not high. `momentum_score`/`trend_strength` are the project's
existing RSI/MACD/EMA-based technical features (`DailyFeature`), not price
color either.

**Evidence sources**:
- Relative strength + RRG state: `app.analytics.rrg` (reused, Phase 6)
- Momentum / trend: `DailyFeature.momentum_score`/`.trend_strength`
  (reused — computed by the existing feature pipeline for this sector's
  own `Symbol` row, confirmed live this session to already exist for
  sector indices like "NIFTY IT"/"BANKNIFTY")
- Price performance: this sector's own % return over a lookback window,
  self-normalized (not vs. an invented threshold)
- Momentum acceleration: change in `momentum_score` over the same window,
  self-normalized the same way
- Breadth / volume participation: unavailable — see `sector_breadth.py`
"""

from decimal import Decimal
from statistics import NormalDist

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.rrg.rrg_engine import prices_to_frame, rolling_zscore
from app.analytics.rrg.rrg_models import RrgQuadrant
from app.analytics.rrg.sector_rrg import compute_sector_rrg
from app.analytics.sector.sector_models import SectorEvidence, SectorRotationState
from app.config.settings import Settings
from app.repositories.feature_repository import DailyFeatureRepository
from app.repositories.market_repository import PriceRepository, SymbolRepository

_ZSCORE_WINDOW = 14  # matches app.analytics.rrg's own smoothing window
_RETURN_LOOKBACK_DAYS = 20  # matches RelativeStrengthFeatureCalculator's own lookback
_NORMAL = NormalDist()

_ROTATION_STATE_FROM_QUADRANT: dict[RrgQuadrant, SectorRotationState] = {
    RrgQuadrant.LEADING: SectorRotationState.LEADING,
    RrgQuadrant.WEAKENING: SectorRotationState.WEAKENING,
    RrgQuadrant.LAGGING: SectorRotationState.LAGGING,
    RrgQuadrant.IMPROVING: SectorRotationState.STRENGTHENING,
}


def rotation_state_from_quadrant(quadrant: RrgQuadrant | None) -> SectorRotationState | None:
    if quadrant is None:
        return None
    return _ROTATION_STATE_FROM_QUADRANT[quadrant]


def _to_probability_score(z: float) -> Decimal | None:
    """Z-score -> 0-100 via the standard normal CDF — no free/invented
    scaling parameter (see rrg_engine.py's module docstring for why this
    technique is used throughout instead of a made-up magnitude constant)."""
    if pd.isna(z):
        return None
    return Decimal(str(round(_NORMAL.cdf(float(z)) * 100, 4)))


def _clamp_0_100(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return max(Decimal(0), min(Decimal(100), value))


async def compute_sector_evidence(
    session: AsyncSession,
    settings: Settings,
    sector_symbol: str,
    *,
    benchmark_symbol: str | None = None,
    lookback_days: int = 250,
) -> SectorEvidence | None:
    """`None` when the sector symbol or its benchmark has no price history
    at all (same "never fabricate" contract as `stock_rrg`/`sector_rrg`)."""
    rrg_results = await compute_sector_rrg(
        session,
        settings,
        [sector_symbol],
        benchmark_symbol=benchmark_symbol,
        lookback_days=lookback_days,
    )
    rrg_points = rrg_results.get(sector_symbol)
    if not rrg_points:
        return None
    last_rrg = rrg_points[-1]

    symbol_row = await SymbolRepository(session).get_by_symbol(sector_symbol)
    if symbol_row is None:
        return None

    feature_history = await DailyFeatureRepository(session).get_history(
        symbol_row.id, limit=lookback_days
    )
    price_history = await PriceRepository(session).get_daily_history(
        symbol_row.id, limit=lookback_days
    )

    momentum_score: Decimal | None = None
    trend_strength: Decimal | None = None
    momentum_acceleration_raw: float | None = None
    momentum_acceleration_z = float("nan")
    if feature_history:
        latest_feature = feature_history[-1]
        momentum_score = latest_feature.momentum_score
        trend_strength = latest_feature.trend_strength

        momentum_series = pd.Series(
            {f.date: float(f.momentum_score) for f in feature_history if f.momentum_score is not None}
        ).sort_index()
        if len(momentum_series) > _ZSCORE_WINDOW:
            momentum_delta = momentum_series.diff(_ZSCORE_WINDOW)
            momentum_acceleration_raw_value = momentum_delta.iloc[-1]
            if not pd.isna(momentum_acceleration_raw_value):
                momentum_acceleration_raw = float(momentum_acceleration_raw_value)
            momentum_acceleration_z_series = rolling_zscore(momentum_delta, _ZSCORE_WINDOW)
            momentum_acceleration_z = momentum_acceleration_z_series.iloc[-1]

    price_performance_raw: float | None = None
    price_performance_z = float("nan")
    if price_history:
        price_frame = prices_to_frame(price_history)
        returns = price_frame["close"].pct_change(_RETURN_LOOKBACK_DAYS) * 100
        if not pd.isna(returns.iloc[-1]):
            price_performance_raw = float(returns.iloc[-1])
        price_performance_z_series = rolling_zscore(returns, _ZSCORE_WINDOW)
        price_performance_z = price_performance_z_series.iloc[-1]

    rs_ratio_z = (
        float(last_rrg.rs_ratio) - 100 if last_rrg.rs_ratio is not None else float("nan")
    )
    rs_momentum_z = (
        float(last_rrg.rs_momentum) - 100 if last_rrg.rs_momentum is not None else float("nan")
    )

    components: dict[str, Decimal | None] = {
        "relative_strength": _to_probability_score(rs_ratio_z),
        "rrg_momentum": _to_probability_score(rs_momentum_z),
        "momentum": _clamp_0_100(
            (momentum_score + 100) / 2 if momentum_score is not None else None
        ),
        "trend": _clamp_0_100(trend_strength),
        "price_performance": _to_probability_score(price_performance_z),
        "momentum_acceleration": _to_probability_score(momentum_acceleration_z),
        "breadth": None,
        "volume_participation": None,
    }
    weights = {
        "relative_strength": settings.sector_score_weight_relative_strength,
        "rrg_momentum": settings.sector_score_weight_rrg_momentum,
        "momentum": settings.sector_score_weight_momentum,
        "trend": settings.sector_score_weight_trend,
        "price_performance": settings.sector_score_weight_price_performance,
        "momentum_acceleration": settings.sector_score_weight_momentum_acceleration,
        "breadth": settings.sector_score_weight_breadth,
        "volume_participation": settings.sector_score_weight_volume_participation,
    }

    available: dict[str, Decimal] = {
        name: value for name, value in components.items() if value is not None
    }
    evidence_sources_used = list(available.keys())
    missing_evidence = [name for name in components if name not in available]

    weighted_sum = sum(
        (value * Decimal(str(weights[name])) for name, value in available.items()),
        start=Decimal(0),
    )
    weight_total = sum(
        (Decimal(str(weights[name])) for name in available), start=Decimal(0)
    )
    score = (
        (weighted_sum / weight_total).quantize(Decimal("0.01")) if weight_total > 0 else None
    )

    return SectorEvidence(
        sector_symbol=sector_symbol,
        benchmark_symbol=last_rrg.benchmark_symbol,
        date=last_rrg.date,
        rs=last_rrg.rs,
        rs_ratio=last_rrg.rs_ratio,
        rs_momentum=last_rrg.rs_momentum,
        rotation_state=rotation_state_from_quadrant(last_rrg.quadrant),
        momentum_score=momentum_score,
        trend_strength=trend_strength,
        price_performance_pct=(
            Decimal(str(round(price_performance_raw, 4)))
            if price_performance_raw is not None
            else None
        ),
        momentum_acceleration=(
            Decimal(str(round(momentum_acceleration_raw, 4)))
            if momentum_acceleration_raw is not None
            else None
        ),
        breadth=None,
        volume_participation=None,
        score=score,
        evidence_sources_used=evidence_sources_used,
        missing_evidence=missing_evidence,
        computed_at=last_rrg.computed_at,
    )
