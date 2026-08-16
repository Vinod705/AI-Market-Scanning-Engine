"""Momentum observation follow-up job (Phase 16 — live paper/simulation
mode) — fills in `momentum_alert_observations.price_after_15m/1h/1d` once
that much real time has actually passed, from real, already-collected
`intraday_prices`/`daily_prices` rows.

**Never estimates, interpolates, or simulates a price.** A horizon whose
target moment has no bar at or after it yet (data lag, or the window
genuinely hasn't arrived) is simply left NULL and revisited on the next
run — this job only ever writes a value it read from a real stored bar.
This is the "subsequent observed market behavior" half of Phase 16's
operational-validation record; it is not a backtest (no simulated entry/
exit, no P&L) and computes no verdict about whether a trigger was "good."
"""

from datetime import timedelta
from decimal import Decimal

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.core.time import to_market_time, utc_now
from app.models.momentum_alert_observation import MomentumAlertObservation
from app.repositories.market_repository import PriceRepository
from app.repositories.momentum_alert_observation_repository import (
    MomentumAlertObservationRepository,
)
from app.scheduler.service import SchedulerService

JOB_ID_MOMENTUM_OBSERVATION_FOLLOWUP = "momentum_observation_followup_run"

_HORIZON_15M = timedelta(minutes=15)
_HORIZON_1H = timedelta(hours=1)
_HORIZON_1D = timedelta(days=1)


def _pct_change(base: Decimal | None, later: Decimal | None) -> Decimal | None:
    if base is None or later is None or base == 0:
        return None
    return Decimal(str(round(float((later - base) / base) * 100, 4)))


async def _fill_15m(price_repo: PriceRepository, row: MomentumAlertObservation) -> bool:
    bar = await price_repo.get_intraday_at_or_after(row.symbol_id, row.trigger_at + _HORIZON_15M)
    if bar is None:
        return False
    row.price_after_15m = bar.close
    row.price_change_pct_15m = _pct_change(row.price_at_trigger, bar.close)
    return True


async def _fill_1h(price_repo: PriceRepository, row: MomentumAlertObservation) -> bool:
    bar = await price_repo.get_intraday_at_or_after(row.symbol_id, row.trigger_at + _HORIZON_1H)
    if bar is None:
        return False
    row.price_after_1h = bar.close
    row.price_change_pct_1h = _pct_change(row.price_at_trigger, bar.close)
    return True


async def _fill_1d(
    price_repo: PriceRepository, row: MomentumAlertObservation, market_timezone: str
) -> bool:
    # `daily_prices.date` is an IST trading date, so the "1 trading day
    # later" target must be derived in IST — an explicit conversion, not
    # a bare `.date()` on the UTC-stored `trigger_at`.
    target_date = to_market_time(row.trigger_at + _HORIZON_1D, market_timezone).date()
    bar = await price_repo.get_daily_at_or_after(row.symbol_id, target_date)
    if bar is None:
        return False
    row.price_after_1d = bar.close
    row.price_change_pct_1d = _pct_change(row.price_at_trigger, bar.close)
    return True


async def _run_followup(session_factory: async_sessionmaker[AsyncSession], settings: Settings) -> None:
    now = utc_now()
    filled = {"15m": 0, "1h": 0, "1d": 0}

    async with session_factory() as session:
        obs_repo = MomentumAlertObservationRepository(session)
        price_repo = PriceRepository(session)

        for row in await obs_repo.list_awaiting_15m(now - _HORIZON_15M):
            if await _fill_15m(price_repo, row):
                filled["15m"] += 1
        for row in await obs_repo.list_awaiting_1h(now - _HORIZON_1H):
            if await _fill_1h(price_repo, row):
                filled["1h"] += 1
        for row in await obs_repo.list_awaiting_1d(now - _HORIZON_1D):
            if await _fill_1d(price_repo, row, settings.market_timezone):
                filled["1d"] += 1

        await session.commit()

    if any(filled.values()):
        logger.info(
            "Momentum observation follow-up: filled {filled}",
            filled=filled,
        )


def register_momentum_observation_followup_jobs(
    scheduler_service: SchedulerService,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    async def _job() -> None:
        await _run_followup(session_factory, settings)

    scheduler_service.add_job(
        _job,
        trigger="interval",
        minutes=5,
        id=JOB_ID_MOMENTUM_OBSERVATION_FOLLOWUP,
        replace_existing=True,
    )

    logger.info(
        "Registered momentum observation follow-up job: {job_id} (every 5m)",
        job_id=JOB_ID_MOMENTUM_OBSERVATION_FOLLOWUP,
    )
