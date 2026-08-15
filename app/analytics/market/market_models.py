"""Domain types for market-wide breadth and regime analytics.

See `breadth.py` for `MarketBreadthSnapshot` and `regime.py` for
`MarketRegimeEvidence`.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


class MarketRegimeState(StrEnum):
    SUPPORTIVE = "SUPPORTIVE"
    NEUTRAL = "NEUTRAL"
    RISK_OFF = "RISK_OFF"


@dataclass
class MarketBreadthSnapshot:
    """Aggregated across the full active LISTED universe — bulk queries
    only (see `breadth.py`), never a per-symbol loop over ~9,598 rows.

    Every percentage's denominator is *symbols with that specific data
    point available*, not `total_symbols` — a symbol missing e.g. EMA200
    (not enough history yet) is excluded from `pct_above_ema200`'s
    denominator rather than silently counted as "below.\""""

    as_of: date
    computed_at: datetime
    total_symbols: int

    advancing: int
    declining: int
    unchanged: int
    advance_decline_pct: Decimal | None  # advancing / (advancing+declining) * 100

    up_volume: int
    down_volume: int
    up_down_volume_pct: Decimal | None  # up_volume / (up_volume+down_volume) * 100

    symbols_with_ema20: int
    pct_above_ema20: Decimal | None
    symbols_with_ema50: int
    pct_above_ema50: Decimal | None
    symbols_with_ema200: int
    pct_above_ema200: Decimal | None

    symbols_with_52wk_range: int
    new_highs: int
    new_lows: int
    # Net (highs - lows) rescaled from [-1,1] to [0,100], 50 = balanced.
    new_highs_lows_net_pct: Decimal | None

    # % of symbols trading above their session VWAP — NOT computed.
    # `session_features` (the only VWAP source in this project) is
    # populated for a small watched subset only (confirmed live: 43 of
    # 9,598 active symbols on the latest date), not the LISTED universe,
    # so any "% above VWAP" computed from it would misrepresent the whole
    # market as if it were representative of 0.4% of it. Always `None`;
    # the coverage counts below explain why rather than hiding it.
    pct_above_vwap: Decimal | None = None
    vwap_coverage_symbols: int = 0
    vwap_coverage_total: int = 0


@dataclass
class MarketRegimeEvidence:
    """One point-in-time read of overall market condition, from multiple
    independent evidence sources — see `regime.py`."""

    as_of: date
    computed_at: datetime
    breadth: MarketBreadthSnapshot

    index_symbol: str
    index_trend_direction: str | None
    index_trend_strength: Decimal | None

    volatility_index_symbol: str | None
    volatility_state: str | None  # "expansion" | "contraction" | "neutral"

    sector_leading_pct: Decimal | None  # None when no sector_symbols supplied

    score: Decimal | None  # 0-100 normalized composite
    regime: MarketRegimeState | None
    evidence_sources_used: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
