"""Deterministic Relative Rotation Graph (RRG) calculation — no LLM, no
I/O. Given a security's and a benchmark's daily price history, produces a
time series of RS-Ratio / RS-Momentum readings and classifies each into
one of the four standard RRG quadrants.

**Reuses the existing relative-strength calculation** —
`app.features.relative_strength.calculator.RelativeStrengthFeatureCalculator`
(the same one that produces `DailyFeature.rs_vs_nifty`) supplies the base
RS time series here; this module does not recompute a security's return
vs. its benchmark from scratch. That calculator is generic over any two
OHLCV frames, so the same call handles both a stock vs. its benchmark and
a sector index vs. its benchmark — see `stock_rrg.py`/`sector_rrg.py`.

**RS-Ratio / RS-Momentum methodology**: the published JdK RS-Ratio/
RS-Momentum technique (StockCharts' "Relative Rotation Graphs") is a
double rolling-window normalization — RS-Ratio normalizes RS itself,
RS-Momentum normalizes RS-Ratio's rate of change — both centered at 100.
The exact proprietary scaling constant StockCharts uses is not publicly
disclosed, so this uses standard statistical Z-score normalization
instead (mean 0, std 1, rescaled to center 100). This preserves exactly
what the classification below actually depends on — whether each value
is above or below 100 — regardless of the undisclosed constant, so no
threshold is invented for the part that matters.

**Quadrant classification** (LEADING / WEAKENING / LAGGING / IMPROVING)
is the standard, universally-used RRG definition — purely the sign of
(RS-Ratio - 100) and (RS-Momentum - 100), not a project-specific rule.
"""

from datetime import date as date_
from decimal import Decimal

import pandas as pd

from app.analytics.rrg.rrg_models import RrgPoint, RrgQuadrant
from app.core.time import utc_now
from app.features.relative_strength.calculator import RelativeStrengthFeatureCalculator
from app.models.daily_price import DailyPrice

_ROLLING_WINDOW = 14  # standard RRG smoothing period (widely published, not project-specific)


def prices_to_frame(prices: list[DailyPrice]) -> pd.DataFrame:
    """Same shape as `app.features.engine`'s private `_prices_to_frame` —
    both `stock_rrg.py` and `sector_rrg.py` need it, and
    `RelativeStrengthFeatureCalculator` only needs the `close` column, so
    kept minimal here rather than importing that module's private
    helper."""
    return pd.DataFrame(
        {"close": [float(p.close) for p in prices]},
        index=pd.DatetimeIndex([p.date for p in prices]),
    )


def _decimal_or_none(value: float) -> Decimal | None:
    if pd.isna(value):
        return None
    return Decimal(str(round(float(value), 4)))


def rolling_zscore(series: pd.Series, window: int = _ROLLING_WINDOW) -> pd.Series:
    """Standard rolling Z-score (mean 0, std 1), rescaled to center 100 —
    the same self-referential normalization RS-Ratio/RS-Momentum use below
    (no external/invented scaling constant: each value is measured against
    that series' own recent distribution). Reused as-is by
    `app.analytics.sector.sector_strength` for price-performance and
    momentum-acceleration normalization, so every "how unusual is this
    reading" calculation in the codebase uses one technique, not several."""
    mean = series.rolling(window).mean()
    std = series.rolling(window).std()
    return 100 + (series - mean) / std


def classify_quadrant(rs_ratio: float, rs_momentum: float) -> RrgQuadrant | None:
    """`None` when either input is missing (NaN) — never guessed into a
    quadrant. The boundary (exactly 100 on either axis) is treated as the
    non-negative side (`>=`), applied consistently to both axes."""
    if pd.isna(rs_ratio) or pd.isna(rs_momentum):
        return None
    ratio_up = rs_ratio >= 100
    momentum_up = rs_momentum >= 100
    if ratio_up and momentum_up:
        return RrgQuadrant.LEADING
    if ratio_up and not momentum_up:
        return RrgQuadrant.WEAKENING
    if not ratio_up and not momentum_up:
        return RrgQuadrant.LAGGING
    return RrgQuadrant.IMPROVING


def compute_rrg_series(
    security_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    *,
    symbol: str,
    benchmark_symbol: str,
    window: int = _ROLLING_WINDOW,
) -> list[RrgPoint]:
    """`security_df`/`benchmark_df` must be OHLCV frames indexed by date,
    ascending, matching `RelativeStrengthFeatureCalculator`'s existing
    contract (see `app/features/engine.py::_prices_to_frame`). Returns one
    `RrgPoint` per date in `security_df`'s index — `rs_ratio`/
    `rs_momentum`/`quadrant` are `None` for the leading dates where the
    rolling window doesn't yet have enough history, never fabricated."""
    rs_frame = RelativeStrengthFeatureCalculator.calculate(security_df, benchmark_df)
    rs_series = rs_frame["rs_vs_nifty"]

    rs_ratio = rolling_zscore(rs_series, window)
    rs_momentum = rolling_zscore(rs_ratio.diff(), window)

    computed_at = utc_now()
    points: list[RrgPoint] = []
    for index_value, rs_value, ratio_value, momentum_value in zip(
        rs_series.index, rs_series, rs_ratio, rs_momentum, strict=True
    ):
        index_date: date_ = (
            index_value.date() if isinstance(index_value, pd.Timestamp) else index_value
        )
        points.append(
            RrgPoint(
                symbol=symbol,
                benchmark_symbol=benchmark_symbol,
                date=index_date,
                rs=_decimal_or_none(rs_value),
                rs_ratio=_decimal_or_none(ratio_value),
                rs_momentum=_decimal_or_none(momentum_value),
                quadrant=classify_quadrant(ratio_value, momentum_value),
                computed_at=computed_at,
            )
        )
    return points
