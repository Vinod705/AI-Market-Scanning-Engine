"""Technical Intelligence domain types — mirrors `app.fundamentals.models`
in shape (factor-level breakdown, explicit reasons) but is its own module
because technical data is always derived from price/volume the project
already has (Phase 3's `daily_features` / `session_features`), so there is
no "no data source" case the way there is for fundamentals. A factor can
still be individually unavailable (e.g. `ema200` needs 200 days of
history a recent IPO won't have yet), which is why `TechnicalFactor`
keeps the same UNKNOWN status as the fundamentals module.
"""

from dataclasses import dataclass
from enum import StrEnum


class FactorStatus(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    UNKNOWN = "UNKNOWN"


@dataclass
class TechnicalFactor:
    factor_name: str
    category: str  # trend | momentum | volume | volatility | vwap | structure
    value: str | None
    normalized_score: float | None
    weight: float
    contribution: float
    status: FactorStatus
    reason: str


@dataclass
class TechnicalScoreResult:
    score: float
    data_completeness_pct: float
    factors: list[TechnicalFactor]
    positive_reasons: list[str]
    negative_reasons: list[str]
    unknown_reasons: list[str]
