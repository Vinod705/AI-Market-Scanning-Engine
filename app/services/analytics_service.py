"""Read-side service backing the /analytics API — the dashboard's data
source. Every method here is a pure DB read against an existing table
(a scheduled snapshot job's output, or an existing engine's own
persisted state); nothing here calls a scanner, SignalFusionEngine,
MomentumStateEngine, or any analytics compute-on-call function. That
separation is what makes "dashboard requests never execute live
scanner calculations" true by construction, not just convention.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.momentum.momentum_models import ALERT_WORTHY_STATES
from app.repositories.feature_repository import DailyFeatureRepository
from app.repositories.fundamental_snapshot_repository import FundamentalSnapshotRepository
from app.repositories.market_regime_snapshot_repository import MarketRegimeSnapshotRepository
from app.repositories.market_repository import SymbolRepository
from app.repositories.momentum_state_repository import MomentumStateRepository
from app.repositories.oi_repository import OiObservationRepository
from app.repositories.sector_rrg_snapshot_repository import SectorRrgSnapshotRepository
from app.schemas.analytics import (
    FundamentalsCoverageOut,
    MarketRegimeOut,
    MomentumCandidateOut,
    MomentumTransitionOut,
    OiBuildupOut,
    SectorRrgOut,
    VolumeLeaderOut,
)


class AnalyticsService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._symbol_repo = SymbolRepository(session)

    async def get_market_regime(self) -> MarketRegimeOut | None:
        row = await MarketRegimeSnapshotRepository(self._session).get_latest()
        if row is None:
            return None
        return MarketRegimeOut(
            computed_at=row.computed_at,
            as_of=row.as_of,
            regime=row.regime,
            score=row.score,
            index_symbol=row.index_symbol,
            index_trend_direction=row.index_trend_direction,
            index_trend_strength=row.index_trend_strength,
            volatility_state=row.volatility_state,
            sector_leading_pct=row.sector_leading_pct,
            evidence_sources_used=list(row.evidence_sources_used),
            missing_evidence=list(row.missing_evidence),
        )

    async def get_sector_rrg(self) -> list[SectorRrgOut]:
        rows = await SectorRrgSnapshotRepository(self._session).get_latest_batch()
        return [
            SectorRrgOut(
                computed_at=row.computed_at,
                sector_symbol=row.sector_symbol,
                benchmark_symbol=row.benchmark_symbol,
                date=row.date,
                rs_ratio=row.rs_ratio,
                rs_momentum=row.rs_momentum,
                rotation_state=row.rotation_state,
                momentum_score=row.momentum_score,
                trend_strength=row.trend_strength,
                price_performance_pct=row.price_performance_pct,
                score=row.score,
            )
            for row in rows
        ]

    async def get_momentum_candidates(self, limit: int) -> list[MomentumCandidateOut]:
        rows = await MomentumStateRepository(self._session).list_by_states(
            ALERT_WORTHY_STATES, limit=limit
        )
        symbol_names = await self._symbol_names([r.symbol_id for r in rows])
        return [
            MomentumCandidateOut(
                symbol=symbol_names.get(row.symbol_id, "UNKNOWN"),
                state=row.state,
                score=row.score,
                reason=row.reason,
                entered_at=row.entered_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    async def get_momentum_history(self, limit: int) -> list[MomentumTransitionOut]:
        rows = await MomentumStateRepository(self._session).list_recent_transitions(limit=limit)
        symbol_names = await self._symbol_names([r.symbol_id for r in rows])
        return [
            MomentumTransitionOut(
                symbol=symbol_names.get(row.symbol_id, "UNKNOWN"),
                from_state=row.from_state,
                to_state=row.to_state,
                timestamp=row.timestamp,
                reason=row.reason,
                score=row.score,
            )
            for row in rows
        ]

    async def get_volume_leaders(self, limit: int) -> list[VolumeLeaderOut]:
        rows = await DailyFeatureRepository(self._session).list_top_relative_volume(limit=limit)
        symbol_names = await self._symbol_names([r.symbol_id for r in rows])
        return [
            VolumeLeaderOut(
                symbol=symbol_names.get(row.symbol_id, "UNKNOWN"),
                date=row.date,
                relative_volume=row.relative_volume,
                volume_spike=row.volume_spike,
                volume_ma20=row.volume_ma20,
            )
            for row in rows
            if row.relative_volume is not None
        ]

    async def get_oi_buildup(self, limit: int) -> list[OiBuildupOut]:
        rows = await OiObservationRepository(self._session).list_latest_buildups(limit=limit)
        symbol_names = await self._symbol_names([r.symbol_id for r in rows])
        return [
            OiBuildupOut(
                symbol=symbol_names.get(row.symbol_id, "UNKNOWN"),
                instrument_key=row.instrument_key,
                observed_at=row.observed_at,
                classification=row.classification,
                oi=row.oi,
                oi_change_pct=row.oi_change_pct,
                price_change_pct=row.price_change_pct,
            )
            for row in rows
        ]

    async def get_fundamentals_coverage(self) -> FundamentalsCoverageOut:
        summary = await FundamentalSnapshotRepository(
            self._session, self._settings
        ).get_coverage_summary()
        return FundamentalsCoverageOut(
            total_symbols_tracked=summary.total_snapshots,
            with_data_count=summary.with_data_count,
            fresh_count=summary.fresh_count,
            last_fetched_at=summary.last_fetched_at,
        )

    # --- internals -----------------------------------------------------

    async def _symbol_names(self, symbol_ids: list[int]) -> dict[int, str]:
        if not symbol_ids:
            return {}
        symbols = await self._symbol_repo.list_by_ids(list(set(symbol_ids)))
        return {s.id: s.symbol for s in symbols}
