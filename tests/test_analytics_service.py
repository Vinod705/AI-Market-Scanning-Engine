"""Tests for app.services.analytics_service.AnalyticsService — the
dashboard's read-only data source (Phase 15). Every method here reads an
existing table; none of these tests exercise a scanner, SignalFusionEngine,
MomentumStateEngine, or a compute_market_regime/compute_sector_rotation
call directly (that reuse contract is enforced separately by
tests/test_analytics_snapshot_jobs.py and the module's own docstring)."""

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.market.breadth import compute_market_breadth
from app.analytics.market.market_models import MarketRegimeEvidence, MarketRegimeState
from app.analytics.sector.sector_models import SectorEvidence, SectorRotationState
from app.config.settings import Settings
from app.derivatives.derivatives_models import BuildupClassification, InstrumentType, OiReading
from app.fundamentals.models import FundamentalData
from app.fundamentals.queue_models import FetchStatus
from app.momentum.momentum_models import MomentumState, StateTransition
from app.providers.base_provider import ProviderSymbol
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
    NewObservation,
)
from app.repositories.momentum_state_repository import MomentumStateRepository
from app.repositories.oi_repository import OiObservationRepository
from app.repositories.sector_rrg_snapshot_repository import SectorRrgSnapshotRepository
from app.services.analytics_service import AnalyticsService


async def _seed_symbol(session_factory: async_sessionmaker[AsyncSession], symbol: str) -> int:
    async with session_factory() as session:
        row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol, exchange="NSE", instrument_token=f"NSE_EQ|{symbol}")
        )
        await session.commit()
        return row.id


async def test_get_market_regime_returns_none_when_no_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        result = await AnalyticsService(session, Settings()).get_market_regime()
    assert result is None


async def test_get_market_regime_returns_latest_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        breadth = await compute_market_breadth(session)
        await MarketRegimeSnapshotRepository(session).insert(
            MarketRegimeEvidence(
                as_of=date(2026, 1, 5),
                computed_at=datetime.now(UTC),
                breadth=breadth,
                index_symbol="NIFTY 50",
                index_trend_direction="up",
                index_trend_strength=Decimal("70"),
                volatility_index_symbol="NIFTY 50",
                volatility_state="contraction",
                sector_leading_pct=None,
                score=Decimal("82.0"),
                regime=MarketRegimeState.SUPPORTIVE,
                evidence_sources_used=["advance_decline"],
                missing_evidence=["volatility"],
            )
        )
        await session.commit()

        result = await AnalyticsService(session, Settings()).get_market_regime()

    assert result is not None
    assert result.regime == "SUPPORTIVE"
    assert result.score == Decimal("82.0")


async def test_get_sector_rrg_returns_latest_batch(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await SectorRrgSnapshotRepository(session).insert_many(
            [
                SectorEvidence(
                    sector_symbol="NIFTY IT",
                    benchmark_symbol="NIFTY 50",
                    date=date(2026, 1, 5),
                    rs=Decimal("1.02"),
                    rs_ratio=Decimal("103.0"),
                    rs_momentum=Decimal("101.0"),
                    rotation_state=SectorRotationState.LEADING,
                    momentum_score=Decimal("70"),
                    trend_strength=Decimal("65"),
                    price_performance_pct=Decimal("3.2"),
                    momentum_acceleration=Decimal("1.1"),
                    breadth=None,
                    volume_participation=None,
                    score=Decimal("88.0"),
                    evidence_sources_used=["relative_strength"],
                    missing_evidence=["breadth"],
                    computed_at=datetime.now(UTC),
                )
            ],
            computed_at=datetime.now(UTC),
        )
        await session.commit()

        result = await AnalyticsService(session, Settings()).get_sector_rrg()

    assert len(result) == 1
    assert result[0].sector_symbol == "NIFTY IT"
    assert result[0].rotation_state == "LEADING"


async def test_get_momentum_candidates_resolves_symbol_names(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory, "TRIGGERED1")
    async with session_factory() as session:
        await MomentumStateRepository(session).apply_transition(
            symbol_id,
            StateTransition(
                symbol="TRIGGERED1",
                from_state=None,
                to_state=MomentumState.TRIGGERED,
                timestamp=datetime.now(UTC),
                reason="score cleared trigger band",
                score=87.0,
            ),
        )
        await session.commit()

        result = await AnalyticsService(session, Settings()).get_momentum_candidates(20)

    assert len(result) == 1
    assert result[0].symbol == "TRIGGERED1"
    assert result[0].state == "TRIGGERED"


async def test_get_momentum_history_resolves_symbol_names(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory, "HISTSYM")
    async with session_factory() as session:
        await MomentumStateRepository(session).apply_transition(
            symbol_id,
            StateTransition(
                symbol="HISTSYM",
                from_state=None,
                to_state=MomentumState.WATCH,
                timestamp=datetime.now(UTC),
                reason="entered watch band",
                score=45.0,
            ),
        )
        await session.commit()

        result = await AnalyticsService(session, Settings()).get_momentum_history(20)

    assert len(result) == 1
    assert result[0].symbol == "HISTSYM"
    assert result[0].to_state == "WATCH"


async def test_get_volume_leaders_resolves_symbol_names_and_excludes_no_data(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory, "RVOLSYM")
    async with session_factory() as session:
        await DailyFeatureRepository(session).upsert(
            symbol_id, date(2026, 1, 5), {"relative_volume": 2.5, "volume_spike": True}
        )
        await session.commit()

        result = await AnalyticsService(session, Settings()).get_volume_leaders(20)

    assert len(result) == 1
    assert result[0].symbol == "RVOLSYM"
    assert float(result[0].relative_volume) == 2.5


async def test_get_oi_buildup_resolves_symbol_names(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory, "OISYM")
    async with session_factory() as session:
        await OiObservationRepository(session).insert(
            symbol_id,
            OiReading(
                underlying_symbol="OISYM",
                instrument_key="NSE_FO|OISYM",
                instrument_type=InstrumentType.FUTURES,
                strike_price=None,
                expiry_date=date(2026, 1, 29),
                observed_at=datetime.now(UTC),
                price=Decimal("100"),
                prev_price=Decimal("95"),
                price_change_pct=Decimal("5.26"),
                volume=1000,
                oi=Decimal("50000"),
                prev_oi=Decimal("40000"),
                oi_change=Decimal("10000"),
                oi_change_pct=Decimal("25.0"),
                classification=BuildupClassification.LONG_BUILDUP,
            ),
        )
        await session.commit()

        result = await AnalyticsService(session, Settings()).get_oi_buildup(20)

    assert len(result) == 1
    assert result[0].symbol == "OISYM"
    assert result[0].classification == "LONG_BUILDUP"


async def test_get_fundamentals_coverage_reports_counts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory, "FUNDSYM")
    settings = Settings(fundamental_cache_ttl_minutes=240)
    async with session_factory() as session:
        await FundamentalSnapshotRepository(session, settings).upsert(
            symbol_id,
            data=FundamentalData(symbol="FUNDSYM", pe=20.0),
            source="Upstox",
            status=FetchStatus.SUCCESS,
            error_message=None,
        )
        await session.commit()

        result = await AnalyticsService(session, settings).get_fundamentals_coverage()

    assert result.total_symbols_tracked == 1
    assert result.with_data_count == 1
    assert result.fresh_count == 1


async def test_get_live_triggers_resolves_symbol_names_and_price_fields(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory, "LIVETRIG")
    now = datetime.now(UTC)
    async with session_factory() as session:
        _record, transition_id = await MomentumStateRepository(session).apply_transition(
            symbol_id,
            StateTransition(
                symbol="LIVETRIG",
                from_state=None,
                to_state=MomentumState.TRIGGERED,
                timestamp=now,
                reason="score cleared trigger band",
                score=87.0,
            ),
        )
        await MomentumAlertObservationRepository(session).insert(
            NewObservation(
                transition_id=transition_id,
                symbol_id=symbol_id,
                alert_id=None,
                momentum_state="TRIGGERED",
                trigger_at=now,
                signal_score=Decimal("87.00"),
                signal_confidence=Decimal("70.00"),
                as_of_data_at=now,
                data_age_seconds=5.0,
                is_stale=False,
                price_at_trigger=Decimal("100.50"),
            )
        )
        await session.commit()

        result = await AnalyticsService(session, Settings()).get_live_triggers(20)

    assert len(result) == 1
    assert result[0].symbol == "LIVETRIG"
    assert result[0].momentum_state == "TRIGGERED"
    assert result[0].signal_score == Decimal("87.00")
    assert result[0].price_at_trigger == Decimal("100.50")
    assert result[0].alert_id is None
    assert result[0].delivery_status is None
    assert result[0].delivery_latency_seconds is None


async def test_get_live_triggers_computes_delivery_latency_from_real_alert_and_delivery_log(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory, "LATENCYSYM")
    now = datetime.now(UTC)
    async with session_factory() as session:
        alert = await AlertRepository(session).create(
            symbol_id=symbol_id,
            scanner_name="momentum_state_v1",
            signal_type="TRIGGERED",
            decision="ALERT",
            score=Decimal("87.00"),
            quality="HIGH",
            entry_reference=None,
            breakout_level=None,
            support_level=None,
            resistance_level=None,
            feature_snapshot={},
            reason="test",
            passed_rules=[],
            fingerprint="test-fingerprint-latency",
            signal_date=date(2026, 1, 5),
            expires_at=None,
        )
        await AlertDeliveryLogRepository(session).log_attempt(
            alert_id=alert.id, provider="telegram", status="SENT", attempt_number=1
        )
        await session.commit()

        _record, transition_id = await MomentumStateRepository(session).apply_transition(
            symbol_id,
            StateTransition(
                symbol="LATENCYSYM",
                from_state=None,
                to_state=MomentumState.TRIGGERED,
                timestamp=now,
                reason="score cleared trigger band",
                score=87.0,
            ),
        )
        await MomentumAlertObservationRepository(session).insert(
            NewObservation(
                transition_id=transition_id,
                symbol_id=symbol_id,
                alert_id=alert.id,
                momentum_state="TRIGGERED",
                trigger_at=now,
                signal_score=Decimal("87.00"),
                signal_confidence=Decimal("70.00"),
                as_of_data_at=now,
                data_age_seconds=5.0,
                is_stale=False,
                price_at_trigger=Decimal("100.50"),
            )
        )
        await session.commit()

        result = await AnalyticsService(session, Settings()).get_live_triggers(20)

    assert result[0].alert_id == alert.id
    assert result[0].delivery_status == "SENT"
    assert result[0].delivery_latency_seconds is not None
    assert result[0].delivery_latency_seconds >= 0


async def test_get_provider_health_reports_collector_and_feed_logs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        collector_log = await CollectorLogRepository(session).start(datetime(2026, 1, 5, 9, 0, tzinfo=UTC))
        await CollectorLogRepository(session).finish(
            collector_log,
            finish_time=datetime(2026, 1, 5, 9, 1, tzinfo=UTC),
            symbols_processed=100,
            success_count=98,
            failed_count=2,
            error_message="two symbols timed out",
        )
        feed_log = await MarketDataFeedLogRepository(session).open(datetime(2026, 1, 5, 9, 0, tzinfo=UTC))
        await MarketDataFeedLogRepository(session).close(
            feed_log,
            disconnected_at=datetime(2026, 1, 5, 9, 30, tzinfo=UTC),
            messages_received=500,
            ticks_processed=480,
            duplicates_dropped=20,
            candles_flushed=32,
            disconnect_reason="connection reset",
        )
        await session.commit()

        result = await AnalyticsService(session, Settings()).get_provider_health(20)

    assert len(result.recent_collector_runs) == 1
    assert result.recent_collector_runs[0].failed_count == 2
    assert result.recent_collector_runs[0].error_message == "two symbols timed out"
    assert len(result.recent_feed_connections) == 1
    assert result.recent_feed_connections[0].disconnect_reason == "connection reset"
    assert result.recent_feed_connections[0].duplicates_dropped == 20
