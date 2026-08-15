"""Domain types for the Signal Fusion Engine.

`DataStatus` is the explicit missing-vs-neutral distinction the phase
requires: `MISSING` means no data could be obtained at all (excluded
from the weighted average, weights renormalized across whatever remains
— same pattern as `app.analytics.sector.sector_strength`/
`app.analytics.market.regime`); `NEUTRAL` means data *was* obtained and
genuinely indicates a neutral/~50 reading (e.g. market regime NEUTRAL, an
RRG reading exactly at the quadrant boundary) — that value stays IN the
weighted average, correctly pulling the composite toward neutral, not
silently dropped. Treating "no data" as if it were "neutral data" would
be exactly the "missing data" fabrication the phase says not to do.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class DataStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    NEUTRAL = "NEUTRAL"
    MISSING = "MISSING"


@dataclass
class ComponentScore:
    """One input's contribution to the fused score."""

    name: str
    status: DataStatus
    score: float | None  # 0-100; None only when status == MISSING
    weight: float  # configured weight before renormalization
    reasons: list[str] = field(default_factory=list)


@dataclass
class SignalFusionResult:
    """`overall_score` is `None` only when every single component is
    MISSING (nothing to average) — never defaulted to a fabricated
    midpoint. `confidence` is the fraction of total configured weight
    that came from non-MISSING components (0-100) — the same "how much of
    the intended input set did we actually have" concept as
    `data_completeness_pct` already used in `app.technical.scorer`/
    `app.fundamentals.scorer`, not a separately-invented statistical
    measure."""

    symbol: str
    overall_score: float | None
    component_scores: dict[str, ComponentScore]
    positive_factors: list[str]
    negative_factors: list[str]
    missing_data: list[str]
    confidence: float
    timestamp: datetime
