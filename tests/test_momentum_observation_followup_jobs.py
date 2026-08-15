"""Tests for app.scheduler.momentum_observation_followup_jobs — fills in
momentum_alert_observations.price_after_15m/1h/1d from real, already-
collected price bars. Never estimates/interpolates a price."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.momentum.momentum_models import MomentumState, StateTransition
from app.providers.base_provider import Candle, ProviderSymbol
from app.repositories.market_repository import PriceRepository, SymbolRepository
from app.repositories.momentum_alert_observation_repository import (
    MomentumAlertObservationRepository,
    NewObservation,
)
from app.repositories.momentum_state_repository import MomentumStateRepository
from app.scheduler.momentum_observation_followup_jobs import (
    JOB_ID_MOMENTUM_OBSERVATION_FOLLOWUP,
    _pct_change,
    _run_followup,
    register_momentum_observation_followup_jobs,
)
from app.scheduler.service import SchedulerService


def test_pct_change_computes_a_plain_percentage() -> None:
    result = _pct_change(Decimal("100"), Decimal("110"))
    assert result == Decimal("10.0")


def test_pct_change_handles_a_decline() -> None:
    result = _pct_change(Decimal("100"), Decimal("95"))
    assert result == Decimal("-5.0")


def test_pct_change_is_none_when_base_is_none() -> None:
    assert _pct_change(None, Decimal("100")) is None


def test_pct_change_is_none_when_later_is_none() -> None:
    assert _pct_change(Decimal("100"), None) is None


def test_pct_change_is_none_when_base_is_zero() -> None:
    assert _pct_change(Decimal("0"), Decimal("100")) is None


async def _seed_observation(
    session_factory: async_sessionmaker[AsyncSession],
    symbol: str,
    *,
    trigger_at: datetime,
    price_at_trigger: Decimal | None,
) -> int:
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol, exchange="NSE", instrument_token=f"NSE_EQ|{symbol}")
        )
        await session.commit()
        _record, transition_id = await MomentumStateRepository(session).apply_transition(
            symbol_row.id,
            StateTransition(
                symbol=symbol,
                from_state=None,
                to_state=MomentumState.TRIGGERED,
                timestamp=trigger_at,
                reason="test",
                score=87.0,
            ),
        )
        await MomentumAlertObservationRepository(session).insert(
            NewObservation(
                transition_id=transition_id,
                symbol_id=symbol_row.id,
                alert_id=None,
                momentum_state="TRIGGERED",
                trigger_at=trigger_at,
                signal_score=Decimal("87.00"),
                signal_confidence=Decimal("70.00"),
                as_of_data_at=trigger_at,
                data_age_seconds=0.0,
                is_stale=False,
                price_at_trigger=price_at_trigger,
            )
        )
        await session.commit()
        return symbol_row.id


async def test_run_followup_fills_15m_from_a_real_subsequent_bar(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    trigger_at = now - timedelta(minutes=20)
    symbol_id = await _seed_observation(
        session_factory, "FOLLOWUP15", trigger_at=trigger_at, price_at_trigger=Decimal("100")
    )

    async with session_factory() as session:
        await PriceRepository(session).upsert_intraday_many(
            symbol_id,
            [Candle(timestamp=trigger_at + timedelta(minutes=16), open=105, high=106, low=104, close=105.5, volume=1000)],
        )
        await session.commit()

    await _run_followup(session_factory, Settings())

    async with session_factory() as session:
        rows = await MomentumAlertObservationRepository(session).list_recent(limit=10)
    assert rows[0].price_after_15m == Decimal("105.5")
    assert rows[0].price_change_pct_15m == Decimal("5.5")
    assert rows[0].price_after_1h is None  # too soon for this horizon
    assert rows[0].price_after_1d is None


async def test_run_followup_leaves_horizon_null_when_no_bar_arrived_yet(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    trigger_at = now - timedelta(minutes=20)
    await _seed_observation(
        session_factory, "NOBARARRIVED", trigger_at=trigger_at, price_at_trigger=Decimal("100")
    )
    # No intraday price seeded at all -- nothing to observe yet.

    await _run_followup(session_factory, Settings())

    async with session_factory() as session:
        rows = await MomentumAlertObservationRepository(session).list_recent(limit=10)
    assert rows[0].price_after_15m is None


async def test_run_followup_fills_1d_from_daily_close(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    trigger_at = now - timedelta(days=2)
    symbol_id = await _seed_observation(
        session_factory, "FOLLOWUP1D", trigger_at=trigger_at, price_at_trigger=Decimal("100")
    )

    async with session_factory() as session:
        await PriceRepository(session).upsert_daily_many(
            symbol_id,
            [Candle(timestamp=trigger_at + timedelta(days=1), open=100, high=112, low=99, close=110, volume=100_000)],
        )
        await session.commit()

    await _run_followup(session_factory, Settings())

    async with session_factory() as session:
        rows = await MomentumAlertObservationRepository(session).list_recent(limit=10)
    assert rows[0].price_after_1d == Decimal("110")
    assert rows[0].price_change_pct_1d == Decimal("10.0")


async def test_register_momentum_observation_followup_jobs_adds_the_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(scheduler_enabled=True, scheduler_timezone="Asia/Kolkata")
    scheduler_service = SchedulerService(settings)

    register_momentum_observation_followup_jobs(scheduler_service, session_factory, settings)

    job_ids = {job.id for job in scheduler_service.jobs}
    assert JOB_ID_MOMENTUM_OBSERVATION_FOLLOWUP in job_ids
