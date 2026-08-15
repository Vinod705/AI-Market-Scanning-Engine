"""Integration tests for app.decision.momentum_pipeline_coordinator.MomentumPipelineCoordinator
— the full Scanner -> Candidate -> SignalFusion -> Momentum state ->
DecisionEngine -> AlertManager path, against an in-memory DB."""

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.manager import AlertManager
from app.alerts.queue import AlertQueue
from app.config.settings import Settings
from app.core.time import utc_now
from app.decision.momentum_pipeline_coordinator import MomentumPipelineCoordinator
from app.providers.base_provider import Candle, ProviderSymbol
from app.repositories.feature_repository import DailyFeatureRepository
from app.repositories.market_repository import PriceRepository, SymbolRepository
from app.repositories.momentum_alert_observation_repository import (
    MomentumAlertObservationRepository,
)
from app.repositories.momentum_state_repository import MomentumStateRepository
from app.repositories.scanner_repository import ScannerResultRepository

_QUALIFYING_FEATURES: dict[str, object] = {
    "trend_strength": Decimal("70"),
    "trend_direction": "up",
    "rsi14": Decimal("60"),
    "macd_histogram": Decimal("1.5"),
    "adx14": Decimal("30"),
    "relative_volume": Decimal("2.5"),
    "ema20": Decimal("100"),
    "ema50": Decimal("95"),
}


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "pipeline_min_confidence_pct": 10.0,  # technical+volume alone (40%) clears this
        "decision_min_alert_score": 60.0,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _coordinator(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> MomentumPipelineCoordinator:
    alert_manager = AlertManager(session_factory, settings, AlertQueue())
    return MomentumPipelineCoordinator(session_factory, settings, alert_manager)


async def _seed_qualifying_candidate(
    session_factory: async_sessionmaker[AsyncSession], symbol: str = "TCS"
) -> int:
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol, exchange="NSE", instrument_token=f"NSE_EQ|{symbol}")
        )
        await session.commit()
        symbol_id = symbol_row.id

        await PriceRepository(session).upsert_daily_many(
            symbol_id,
            [
                Candle(
                    timestamp=datetime(2026, 1, 5),
                    open=100,
                    high=105,
                    low=99,
                    close=103,
                    volume=100_000,
                )
            ],
        )
        await DailyFeatureRepository(session).upsert(
            symbol_id, date(2026, 1, 5), _QUALIFYING_FEATURES
        )
        await ScannerResultRepository(session).upsert(
            symbol_id=symbol_id,
            scanner_name="breakout_v1",
            date=date(2026, 1, 5),
            score=Decimal("90"),
            status="qualified",
            reason="all conditions met",
            feature_snapshot={},
        )
        await session.commit()
        return symbol_id


async def test_no_qualified_candidates_produces_empty_run(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    coordinator = _coordinator(session_factory, _settings())
    result = await coordinator.run_all()
    assert result.candidates_evaluated == 0


async def test_first_run_can_trigger_immediately_when_score_already_qualifies(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The seeded fixture's real SignalFusion score (~87, technical+volume
    only) already clears `decision_min_alert_score=60` on the very first
    observation — matching app.momentum.state_machine's own documented
    rule that a cold-start symbol enters at whatever band the score
    naturally falls into (never capped to SETUP just because it's the
    first observation)."""
    await _seed_qualifying_candidate(session_factory)
    coordinator = _coordinator(session_factory, _settings())

    result = await coordinator.run_all()

    assert result.candidates_evaluated == 1
    assert result.trigger_count == 1
    assert result.alerts_created == 1


async def test_repeated_runs_with_stable_data_never_double_alert(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Same stable, unchanged data across 5 pipeline runs: TRIGGERED (run
    1, real alert) -> CONFIRMED (run 2, a second real alert — a genuinely
    different signal_type, not a duplicate) -> holds at CONFIRMED (runs
    3-5, no further alert at all) — this is the actual 'no repeated
    alerts for the same unchanged state' guarantee, exercised through the
    full wired pipeline rather than MomentumStateEngine directly."""
    await _seed_qualifying_candidate(session_factory)
    settings = _settings()
    coordinator = _coordinator(session_factory, settings)

    alert_counts = [(await coordinator.run_all()).alerts_created for _ in range(5)]

    assert alert_counts == [1, 1, 0, 0, 0]

    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).get_by_symbol("TCS")
        assert symbol_row is not None
        current = await MomentumStateRepository(session).get_current(symbol_row.id)
        assert current is not None
        assert current.state == "CONFIRMED"


async def test_low_confidence_rejects_before_touching_momentum_state(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """With no other evidence source available and a confidence bar the
    lone technical+volume signals can't clear, the pipeline must REJECT
    without ever writing a momentum-state row."""
    symbol_id = await _seed_qualifying_candidate(session_factory, symbol="LOWCONF")
    settings = _settings(pipeline_min_confidence_pct=99.0)
    coordinator = _coordinator(session_factory, settings)

    result = await coordinator.run_all()

    assert result.reject_count == 1
    async with session_factory() as session:
        record = await MomentumStateRepository(session).get_current(symbol_id)
        assert record is None  # never evaluated, since we rejected before momentum evaluation


async def test_trigger_writes_a_phase16_operational_observation_row(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A live TRIGGER must leave a real, factual
    momentum_alert_observations row behind — trigger time, score, and
    the real intraday price/freshness backing it — for
    app.scheduler.momentum_observation_followup_jobs to later fill in
    with real subsequent prices. Never a simulated trade, never a
    verdict about whether the trigger was "good"."""
    symbol_id = await _seed_qualifying_candidate(session_factory)
    now = utc_now()
    async with session_factory() as session:
        await PriceRepository(session).upsert_intraday_many(
            symbol_id,
            [
                Candle(
                    timestamp=now - timedelta(minutes=2),
                    open=102,
                    high=104,
                    low=101,
                    close=103.5,
                    volume=50_000,
                )
            ],
        )
        await session.commit()

    coordinator = _coordinator(session_factory, _settings())
    result = await coordinator.run_all(now=now)
    assert result.trigger_count == 1

    async with session_factory() as session:
        rows = await MomentumAlertObservationRepository(session).list_recent(limit=10)

    assert len(rows) == 1
    obs = rows[0]
    assert obs.symbol_id == symbol_id
    assert obs.momentum_state == "TRIGGERED"
    assert obs.alert_id is not None
    assert obs.price_at_trigger == Decimal("103.5")
    assert obs.data_age_seconds is not None
    assert 100 < obs.data_age_seconds < 140  # ~2 minutes, allowing for tz round-trip slack
    assert obs.is_stale is False
    # Nothing here simulates a trade or outcome — only the trigger's own
    # inputs are recorded; subsequent-price fields stay unset until a
    # separate follow-up job records real, later prices.
    assert obs.price_after_15m is None
    assert obs.price_after_1h is None
    assert obs.price_after_1d is None


async def test_no_observation_written_when_data_stale_beyond_threshold(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_qualifying_candidate(session_factory)
    now = utc_now()
    async with session_factory() as session:
        await PriceRepository(session).upsert_intraday_many(
            symbol_id,
            [
                Candle(
                    timestamp=now - timedelta(minutes=30),
                    open=102,
                    high=104,
                    low=101,
                    close=103.5,
                    volume=50_000,
                )
            ],
        )
        await session.commit()

    settings = _settings(momentum_observation_stale_data_threshold_seconds=300.0)
    coordinator = _coordinator(session_factory, settings)
    result = await coordinator.run_all(now=now)
    assert result.trigger_count == 1

    async with session_factory() as session:
        rows = await MomentumAlertObservationRepository(session).list_recent(limit=10)

    assert len(rows) == 1
    assert rows[0].is_stale is True


async def test_progression_to_confirmed_writes_a_second_distinct_observation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """TRIGGERED and the later CONFIRMED transition are genuinely
    separate events — each gets its own observation row, anchored to its
    own distinct transition_id."""
    await _seed_qualifying_candidate(session_factory)
    coordinator = _coordinator(session_factory, _settings())

    await coordinator.run_all()  # TRIGGERED
    await coordinator.run_all()  # CONFIRMED (same stable score)

    async with session_factory() as session:
        rows = await MomentumAlertObservationRepository(session).list_recent(limit=10)

    assert {r.momentum_state for r in rows} == {"TRIGGERED", "CONFIRMED"}
    assert len({r.transition_id for r in rows}) == 2


async def test_no_news_provider_is_ever_constructible_on_the_coordinator() -> None:
    """Structural guarantee: the coordinator's constructor has no
    parameter through which a caller could inject a live-calling
    NewsProvider — this is what makes 'never call external news APIs
    synchronously' true by construction, not just by convention."""
    import inspect

    signature = inspect.signature(MomentumPipelineCoordinator.__init__)
    assert "news_provider" not in signature.parameters
