"""Persistence for `market_regime_snapshots` — the read-only cache the
dashboard/digest consume instead of ever calling
`app.analytics.market.regime.compute_market_regime` directly."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.market.market_models import MarketRegimeEvidence
from app.models.market_regime_snapshot import MarketRegimeSnapshot


class MarketRegimeSnapshotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert(self, evidence: MarketRegimeEvidence) -> MarketRegimeSnapshot:
        row = MarketRegimeSnapshot(
            computed_at=evidence.computed_at,
            as_of=evidence.as_of,
            regime=evidence.regime.value if evidence.regime is not None else None,
            score=evidence.score,
            index_symbol=evidence.index_symbol,
            index_trend_direction=evidence.index_trend_direction,
            index_trend_strength=evidence.index_trend_strength,
            volatility_state=evidence.volatility_state,
            sector_leading_pct=evidence.sector_leading_pct,
            evidence_sources_used=list(evidence.evidence_sources_used),
            missing_evidence=list(evidence.missing_evidence),
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_latest(self) -> MarketRegimeSnapshot | None:
        stmt = (
            select(MarketRegimeSnapshot)
            .order_by(MarketRegimeSnapshot.computed_at.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()
