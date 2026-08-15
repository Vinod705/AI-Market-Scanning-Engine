"""Market regime: combines breadth, index trend, volatility, and
(optionally) sector participation into one SUPPORTIVE/NEUTRAL/RISK_OFF
classification and a 0-100 confidence score.

**Not wired into `DecisionEvaluator`/`AlertManager`/any scanner's
`score()`.** Per this phase's own scope, this is meant to become a
*confidence modifier* for a future SignalFusionEngine, not an automatic
per-stock rejection — nothing here changes any existing scanner, decision,
or alert weight.

**Evidence sources** (real, reused, each independently missing-safe):
- Breadth: `breadth.py` (advance/decline, up/down volume, % above MAs,
  new highs/lows) — all LISTED-universe-wide, bulk-computed.
- Index trend: the benchmark index's own `DailyFeature.trend_direction`/
  `.trend_strength` (reused, same fields `app.analytics.rrg`'s benchmark
  already has computed).
- Volatility: the benchmark index's own `bb_width` history, self-normalized
  via `rrg_engine.rolling_zscore` — "where reliable data exists" per this
  phase's own instruction, since there is no India VIX (or any options-
  implied-volatility) data source in this project; using the benchmark's
  own realized-volatility proxy is real data, not a fabricated VIX.
- Sector participation: reuses `app.analytics.sector.sector_rotation`
  (Phase 7) — % of the caller-supplied sectors currently
  LEADING/STRENGTHENING. Only included when the caller supplies
  `sector_symbols`; there is no fixed "the sectors" list stored anywhere
  (same reasoning as Phase 7), so this module doesn't invent one either.
"""

from decimal import Decimal
from statistics import NormalDist

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.market.breadth import compute_market_breadth
from app.analytics.market.market_models import (
    MarketBreadthSnapshot,
    MarketRegimeEvidence,
    MarketRegimeState,
)
from app.analytics.rrg.rrg_engine import rolling_zscore
from app.analytics.sector.sector_rotation import compute_sector_rotation
from app.config.settings import Settings
from app.core.time import utc_now
from app.repositories.feature_repository import DailyFeatureRepository
from app.repositories.market_repository import SymbolRepository

_ZSCORE_WINDOW = 14  # matches app.analytics.rrg/sector's own smoothing window
_NORMAL = NormalDist()
# The only non-arbitrary way to split a 0-100 composite into three named
# bands is equal thirds — same reasoning as RRG's zero-based quadrant
# boundary: no magnitude is invented, just the score's own midpoint/thirds.
_SUPPORTIVE_THRESHOLD = Decimal(200) / Decimal(3)  # 66.67
_RISK_OFF_THRESHOLD = Decimal(100) / Decimal(3)  # 33.33


def _to_probability_score(z: float) -> Decimal | None:
    if pd.isna(z):
        return None
    return Decimal(str(round(_NORMAL.cdf(float(z)) * 100, 4)))


def _index_trend_score(direction: str | None, strength: Decimal | None) -> Decimal | None:
    if direction is None or strength is None:
        return None
    clamped = max(Decimal(0), min(Decimal(100), strength))
    if direction == "up":
        return clamped
    if direction == "down":
        return 100 - clamped
    return Decimal(50)  # "sideways"


def _average_ma_pct(breadth: MarketBreadthSnapshot) -> Decimal | None:
    values = [
        v
        for v in (breadth.pct_above_ema20, breadth.pct_above_ema50, breadth.pct_above_ema200)
        if v is not None
    ]
    if not values:
        return None
    return sum(values, start=Decimal(0)) / len(values)


def classify_regime(score: Decimal | None) -> MarketRegimeState | None:
    """`None` when there's no evidence at all — never guessed into NEUTRAL
    as a default."""
    if score is None:
        return None
    if score >= _SUPPORTIVE_THRESHOLD:
        return MarketRegimeState.SUPPORTIVE
    if score <= _RISK_OFF_THRESHOLD:
        return MarketRegimeState.RISK_OFF
    return MarketRegimeState.NEUTRAL


async def compute_market_regime(
    session: AsyncSession,
    settings: Settings,
    *,
    index_symbol: str | None = None,
    sector_symbols: list[str] | None = None,
    lookback_days: int = 250,
) -> MarketRegimeEvidence:
    index_symbol = index_symbol or settings.feature_rs_benchmark_symbol
    breadth = await compute_market_breadth(session)

    index_row = await SymbolRepository(session).get_by_symbol(index_symbol)
    index_trend_direction: str | None = None
    index_trend_strength: Decimal | None = None
    volatility_state: str | None = None
    volatility_z = float("nan")

    if index_row is not None:
        feature_history = await DailyFeatureRepository(session).get_history(
            index_row.id, limit=lookback_days
        )
        if feature_history:
            latest = feature_history[-1]
            index_trend_direction = latest.trend_direction
            index_trend_strength = latest.trend_strength

            bb_width_series = pd.Series(
                {f.date: float(f.bb_width) for f in feature_history if f.bb_width is not None}
            ).sort_index()
            if len(bb_width_series) > _ZSCORE_WINDOW:
                # rolling_zscore centers at 100 (see rrg_engine.py) — undo
                # that offset to get back a plain zero-centered z-score.
                z_series = rolling_zscore(bb_width_series, _ZSCORE_WINDOW) - 100
                volatility_z = z_series.iloc[-1]
                if not pd.isna(volatility_z):
                    # Zero-based, same convention as RRG's quadrant
                    # boundary — no magnitude threshold invented.
                    if volatility_z > 0:
                        volatility_state = "expansion"
                    elif volatility_z < 0:
                        volatility_state = "contraction"
                    else:
                        volatility_state = "neutral"

    sector_leading_pct: Decimal | None = None
    if sector_symbols:
        sector_summary = await compute_sector_rotation(
            session, settings, sector_symbols, lookback_days=lookback_days
        )
        resolved = len(sector_summary.sectors)
        if resolved > 0:
            leading_count = len(sector_summary.leading) + len(sector_summary.strengthening)
            sector_leading_pct = Decimal(leading_count) / Decimal(resolved) * 100

    components: dict[str, Decimal | None] = {
        "advance_decline": breadth.advance_decline_pct,
        "up_down_volume": breadth.up_down_volume_pct,
        "pct_above_ma": _average_ma_pct(breadth),
        "new_highs_lows": breadth.new_highs_lows_net_pct,
        "index_trend": _index_trend_score(index_trend_direction, index_trend_strength),
        "volatility": (
            _to_probability_score(-volatility_z) if not pd.isna(volatility_z) else None
        ),
        "sector_participation": sector_leading_pct,
    }
    weights = {
        "advance_decline": settings.market_regime_weight_advance_decline,
        "up_down_volume": settings.market_regime_weight_up_down_volume,
        "pct_above_ma": settings.market_regime_weight_pct_above_ma,
        "new_highs_lows": settings.market_regime_weight_new_highs_lows,
        "index_trend": settings.market_regime_weight_index_trend,
        "volatility": settings.market_regime_weight_volatility,
        "sector_participation": settings.market_regime_weight_sector_participation,
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
    weight_total = sum((Decimal(str(weights[name])) for name in available), start=Decimal(0))
    score = (
        (weighted_sum / weight_total).quantize(Decimal("0.01")) if weight_total > 0 else None
    )

    return MarketRegimeEvidence(
        as_of=breadth.as_of,
        computed_at=utc_now(),
        breadth=breadth,
        index_symbol=index_symbol,
        index_trend_direction=index_trend_direction,
        index_trend_strength=index_trend_strength,
        volatility_index_symbol=index_symbol if volatility_state is not None else None,
        volatility_state=volatility_state,
        sector_leading_pct=sector_leading_pct,
        score=score,
        regime=classify_regime(score),
        evidence_sources_used=evidence_sources_used,
        missing_evidence=missing_evidence,
    )
