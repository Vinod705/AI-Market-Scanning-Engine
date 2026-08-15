"""Tests for app.scheduler.momentum_pipeline_jobs — Phase 16's live
wiring of MomentumPipelineCoordinator. Never places an order, never
backtests; only verifies the job runs the real coordinator on a market-
hours schedule and skips cleanly when the market is closed."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.manager import AlertManager
from app.alerts.queue import AlertQueue
from app.config.settings import Settings
from app.providers.base_provider import Candle, ProviderSymbol
from app.repositories.feature_repository import DailyFeatureRepository
from app.repositories.market_repository import PriceRepository, SymbolRepository
from app.repositories.momentum_state_repository import MomentumStateRepository
from app.repositories.scanner_repository import ScannerResultRepository
from app.scheduler.momentum_pipeline_jobs import (
    JOB_ID_MOMENTUM_PIPELINE,
    _run_momentum_pipeline,
    register_momentum_pipeline_jobs,
)
from app.scheduler.service import SchedulerService

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
        "pipeline_min_confidence_pct": 10.0,
        "decision_min_alert_score": 60.0,
        "scheduler_enabled": True,
        "scheduler_timezone": "Asia/Kolkata",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


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
            [Candle(timestamp=datetime(2026, 1, 5), open=100, high=105, low=99, close=103, volume=100_000)],
        )
        await DailyFeatureRepository(session).upsert(symbol_id, date(2026, 1, 5), _QUALIFYING_FEATURES)
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


async def test_run_skips_entirely_when_market_closed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_qualifying_candidate(session_factory)
    settings = _settings()
    alert_manager = AlertManager(session_factory, settings, AlertQueue())

    await _run_momentum_pipeline(
        session_factory, settings, alert_manager, is_market_open=lambda now: False
    )

    async with session_factory() as session:
        record = await MomentumStateRepository(session).get_current(symbol_id)
    assert record is None  # never evaluated at all


async def test_run_evaluates_the_real_pipeline_when_market_open(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_qualifying_candidate(session_factory)
    settings = _settings()
    alert_manager = AlertManager(session_factory, settings, AlertQueue())

    await _run_momentum_pipeline(
        session_factory, settings, alert_manager, is_market_open=lambda now: True
    )

    async with session_factory() as session:
        record = await MomentumStateRepository(session).get_current(symbol_id)
    assert record is not None  # the real coordinator actually ran


async def test_register_momentum_pipeline_jobs_adds_the_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = _settings()
    scheduler_service = SchedulerService(settings)
    alert_manager = AlertManager(session_factory, settings, AlertQueue())

    register_momentum_pipeline_jobs(
        scheduler_service, session_factory, settings, alert_manager, is_market_open=lambda now: True
    )

    job_ids = {job.id for job in scheduler_service.jobs}
    assert JOB_ID_MOMENTUM_PIPELINE in job_ids
