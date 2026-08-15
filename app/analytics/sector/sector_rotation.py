"""Sector rotation summary: runs `sector_strength.compute_sector_evidence`
across multiple sectors and groups them by rotation state.

Grouping reuses the RRG-quadrant-derived `SectorRotationState` on each
`SectorEvidence` — "leading"/"weakening"/"lagging"/"strengthening" sectors
are exactly the sectors whose latest reading falls in each state, not a
separately invented ranking rule.
"""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.sector.sector_models import SectorEvidence, SectorRotationState
from app.analytics.sector.sector_strength import compute_sector_evidence
from app.config.settings import Settings
from app.core.time import utc_now


@dataclass
class SectorRotationSummary:
    sectors: list[SectorEvidence]
    leading: list[str] = field(default_factory=list)
    weakening: list[str] = field(default_factory=list)
    lagging: list[str] = field(default_factory=list)
    strengthening: list[str] = field(default_factory=list)
    no_data: list[str] = field(default_factory=list)
    computed_at: datetime = field(default_factory=utc_now)


_STATE_GROUP: dict[SectorRotationState, str] = {
    SectorRotationState.LEADING: "leading",
    SectorRotationState.WEAKENING: "weakening",
    SectorRotationState.LAGGING: "lagging",
    SectorRotationState.STRENGTHENING: "strengthening",
}


async def compute_sector_rotation(
    session: AsyncSession,
    settings: Settings,
    sector_symbols: list[str],
    *,
    benchmark_symbol: str | None = None,
    lookback_days: int = 250,
) -> SectorRotationSummary:
    """A `sector_symbols` entry with no data becomes a `no_data` entry, not
    a silently dropped one and not a fabricated evidence row."""
    summary = SectorRotationSummary(sectors=[])

    for sector_symbol in sector_symbols:
        evidence = await compute_sector_evidence(
            session,
            settings,
            sector_symbol,
            benchmark_symbol=benchmark_symbol,
            lookback_days=lookback_days,
        )
        if evidence is None:
            summary.no_data.append(sector_symbol)
            continue

        summary.sectors.append(evidence)
        if evidence.rotation_state is not None:
            group = getattr(summary, _STATE_GROUP[evidence.rotation_state])
            group.append(sector_symbol)

    summary.sectors.sort(key=lambda e: (e.score is None, -(e.score or 0)))
    return summary
