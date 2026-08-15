"""Read-side service backing the /analytics API — the dashboard's data
source. Every method here is a pure DB read against an existing table
(a scheduled snapshot job's output, or an existing engine's own
persisted state); nothing here calls a scanner, SignalFusionEngine,
MomentumStateEngine, or any analytics compute-on-call function. That
separation is what makes "dashboard requests never execute live
scanner calculations" true by construction, not just convention.
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.momentum.momentum_models import ALERT_WORTHY_STATES
from app.repositories.alert_repository import AlertDeliveryLogRepository, AlertRepository
from app.repositories.feature_repository import DailyFeatureRepository
from app.repositories.fundamental_snapshot_repository import FundamentalSnapshotRepository
from app.repositories.market_regime_snapshot_repository import MarketRegimeSnapshotRepository
from app.repositories.market_repository import (
    CollectorLogRepository,
    MarketDataFeedLogRepository,
    SymbolRepository,
)
from app.repositories.momentum_alert_observation_repository import (
    MomentumAlertObservationRepository,
)
from app.repositories.momentum_state_repository import MomentumStateRepository
from app.repositories.oi_repository import OiObservationRepository
from app.repositories.sector_rrg_snapshot_repository import SectorRrgSnapshotRepository
from app.schemas.analytics import (
    CollectorRunOut,
    FundamentalsCoverageOut,
    LiveTriggerOut,
    MarketFeedConnectionOut,
    MarketRegimeOut,
    MomentumCandidateOut,
    MomentumTransitionOut,
    OiBuildupOut,
    ProviderHealthOut,
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

    async def get_live_triggers(self, limit: int) -> list[LiveTriggerOut]:
        """Phase 16 operational validation: recent alert-worthy triggers
        with their real subsequent price behavior (as much as has been
        observed so far) and delivery latency. Delivery latency is
        computed here at read time from `Alert.created_at` and the
        latest `AlertDeliveryLog` for that alert — not stored, so it
        always reflects the actual send outcome rather than a snapshot
        taken before delivery happened."""
        rows = await MomentumAlertObservationRepository(self._session).list_recent(limit)
        symbol_names = await self._symbol_names([r.symbol_id for r in rows])

        alert_repo = AlertRepository(self._session)
        delivery_repo = AlertDeliveryLogRepository(self._session)

        results = []
        for row in rows:
            delivery_status: str | None = None
            delivery_latency_seconds: float | None = None
            if row.alert_id is not None:
                alert = await alert_repo.get_by_id(row.alert_id)
                delivery_log = await delivery_repo.get_latest_for_alert(row.alert_id)
                if delivery_log is not None:
                    delivery_status = delivery_log.status
                    if alert is not None:
                        delivery_latency_seconds = self._seconds_between(
                            alert.created_at, delivery_log.created_at
                        )

            results.append(
                LiveTriggerOut(
                    symbol=symbol_names.get(row.symbol_id, "UNKNOWN"),
                    momentum_state=row.momentum_state,
                    trigger_at=row.trigger_at,
                    signal_score=row.signal_score,
                    signal_confidence=row.signal_confidence,
                    data_age_seconds=row.data_age_seconds,
                    is_stale=row.is_stale,
                    price_at_trigger=row.price_at_trigger,
                    price_after_15m=row.price_after_15m,
                    price_after_1h=row.price_after_1h,
                    price_after_1d=row.price_after_1d,
                    price_change_pct_15m=row.price_change_pct_15m,
                    price_change_pct_1h=row.price_change_pct_1h,
                    price_change_pct_1d=row.price_change_pct_1d,
                    alert_id=row.alert_id,
                    delivery_status=delivery_status,
                    delivery_latency_seconds=delivery_latency_seconds,
                )
            )
        return results

    async def get_provider_health(self, limit: int) -> ProviderHealthOut:
        collector_rows = await CollectorLogRepository(self._session).list_recent(limit)
        feed_rows = await MarketDataFeedLogRepository(self._session).list_recent(limit)
        return ProviderHealthOut(
            recent_collector_runs=[
                CollectorRunOut(
                    start_time=r.start_time,
                    finish_time=r.finish_time,
                    duration=r.duration,
                    symbols_processed=r.symbols_processed,
                    success_count=r.success_count,
                    failed_count=r.failed_count,
                    error_message=r.error_message,
                )
                for r in collector_rows
            ],
            recent_feed_connections=[
                MarketFeedConnectionOut(
                    connected_at=r.connected_at,
                    disconnected_at=r.disconnected_at,
                    messages_received=r.messages_received,
                    ticks_processed=r.ticks_processed,
                    duplicates_dropped=r.duplicates_dropped,
                    candles_flushed=r.candles_flushed,
                    disconnect_reason=r.disconnect_reason,
                )
                for r in feed_rows
            ],
        )

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

    @staticmethod
    def _seconds_between(earlier: datetime, later: datetime) -> float:
        """SQLite (this project's test backend) doesn't enforce tz-aware
        storage for DateTime(timezone=True) — a value written tz-aware
        can come back naive. Same fix as
        FundamentalFetchLogRepository.most_recent_rate_limit."""
        if earlier.tzinfo is None:
            earlier = earlier.replace(tzinfo=UTC)
        if later.tzinfo is None:
            later = later.replace(tzinfo=UTC)
        return (later - earlier).total_seconds()

    async def _symbol_names(self, symbol_ids: list[int]) -> dict[int, str]:
        if not symbol_ids:
            return {}
        symbols = await self._symbol_repo.list_by_ids(list(set(symbol_ids)))
        return {s.id: s.symbol for s in symbols}
