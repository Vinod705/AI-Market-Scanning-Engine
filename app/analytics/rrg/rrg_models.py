"""Domain types for the Relative Rotation Graph (RRG) engine.

See `app.analytics.rrg.rrg_engine` for how these are computed.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


class RrgQuadrant(StrEnum):
    """Standard RRG quadrant naming (Julius de Kempenaer's Relative
    Rotation Graph methodology) — not a project-specific invention.
    Determined purely by the sign of (RS-Ratio - 100) and
    (RS-Momentum - 100); see `rrg_engine.classify_quadrant`."""

    LEADING = "LEADING"
    WEAKENING = "WEAKENING"
    LAGGING = "LAGGING"
    IMPROVING = "IMPROVING"


@dataclass
class RrgPoint:
    """One security's RRG reading on one trading day."""

    symbol: str
    benchmark_symbol: str
    date: date
    rs: Decimal | None
    rs_ratio: Decimal | None
    rs_momentum: Decimal | None
    quadrant: RrgQuadrant | None
    computed_at: datetime
