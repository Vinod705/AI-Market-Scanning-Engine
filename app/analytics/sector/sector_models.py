"""Domain types for sector rotation analytics.

See `sector_strength.py` for how `SectorEvidence` is computed and
`sector_rotation.py` for how `SectorRotationSummary` groups sectors.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


class SectorRotationState(StrEnum):
    """Maps 1:1 onto `RrgQuadrant` (see `sector_strength.rotation_state_from_quadrant`),
    renaming IMPROVING -> STRENGTHENING: a sector whose relative strength is
    still below the benchmark average but whose momentum is turning up is
    naturally described as "strengthening" (from a weak position), the
    common informal reading of that RRG quadrant. LEADING/WEAKENING/LAGGING
    keep the standard RRG names unchanged."""

    LEADING = "LEADING"
    WEAKENING = "WEAKENING"
    LAGGING = "LAGGING"
    STRENGTHENING = "STRENGTHENING"


@dataclass
class SectorEvidence:
    """One sector's full multi-evidence read on one trading day.

    `breadth`/`volume_participation` are `None` — no sector/industry
    metadata exists anywhere in this project (`Symbol.sector`/`Symbol.industry`
    are 0% populated; Upstox's instrument records carry no such field
    either, confirmed live), so there is no constituent-stock membership
    list to compute either from. Listed explicitly in `missing_evidence`
    rather than silently omitted or defaulted to a guessed value. See
    `sector_breadth.py` for the (currently unused, real-data-dependent)
    pure math that would compute them if a membership source existed.
    """

    sector_symbol: str
    benchmark_symbol: str
    date: date

    # Relative strength / RRG (app.analytics.rrg, reused)
    rs: Decimal | None
    rs_ratio: Decimal | None
    rs_momentum: Decimal | None
    rotation_state: SectorRotationState | None

    # Technical momentum/trend (DailyFeature, reused — same fields
    # BreakoutScanner/VcpScanner/MomentumScanner/OrbScanner already read)
    momentum_score: Decimal | None
    trend_strength: Decimal | None

    # This module's own additions — see sector_strength.py
    price_performance_pct: Decimal | None
    momentum_acceleration: Decimal | None

    # Unavailable — see class docstring
    breadth: Decimal | None
    volume_participation: Decimal | None

    score: Decimal | None  # 0-100 normalized composite, None if no evidence at all
    evidence_sources_used: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    computed_at: datetime | None = None
