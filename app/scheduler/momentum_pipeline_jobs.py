"""Momentum pipeline job (Phase 16 — live paper/simulation mode) — the
only place `app.decision.momentum_pipeline_coordinator.MomentumPipelineCoordinator`
is ever run continuously, closing the loop:

    Upstox -> live data -> analytics -> SignalFusion ->
    momentum state -> DecisionEngine -> Telegram

`MomentumPipelineCoordinator` itself was built in Phase 13 and has been
real, tested infrastructure ever since — this job is what actually
schedules it, the same "layer exists, not wired in yet" precedent this
project has followed for every analytics engine until its own connecting
phase (see `app.signals.signal_fusion_engine`'s docstring for the
original statement of that pattern).

**Never places an order, never simulates a trade, never backtests.**
This job's only two effects are (1) whatever `MomentumPipelineCoordinator.run_all()`
already does — real-time TRIGGER/WATCH/REJECT/INVALIDATE verdicts,
reusing the existing alert/dedup/cooldown/Telegram pipeline verbatim —
and (2) the operational-validation observation rows that same call
writes for alert-worthy transitions (see the coordinator's own
docstring). There is no order-execution code anywhere in this project.

Gated on real market hours (`MarketStatusUpdater.is_market_open`) —
running a full-universe SignalFusion pass every tick while the market is
closed would just repeat the same reading against unchanging data, the
same reasoning `app.data.ingestion_worker` already uses.

Market regime is computed here, once per tick — not inside the
coordinator or per-symbol inside SignalFusionEngine — for the same
"compute market-wide evidence exactly once per scan cycle" reason
`app.signals.signal_fusion_engine`'s own docstring documents (a
per-symbol recomputation would be the exact throughput bug this project
has fixed before). News stays out entirely: `MomentumPipelineCoordinator`
has no parameter to accept a `NewsProvider` at all (Phase 13's own
structural guarantee), so nothing here can wire one in even by accident.
"""

from collections.abc import Callable
from datetime import datetime

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.manager import AlertManager
from app.analytics.market.market_models import MarketRegimeEvidence
from app.analytics.market.regime import compute_market_regime
from app.config.settings import Settings
from app.core.time import utc_now
from app.decision.momentum_pipeline_coordinator import MomentumPipelineCoordinator
from app.scheduler.service import SchedulerService

JOB_ID_MOMENTUM_PIPELINE = "momentum_pipeline_run"


async def _compute_regime_evidence(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> MarketRegimeEvidence | None:
    """Best-effort — a regime-computation failure must never stop the
    live trigger pipeline itself; the run simply proceeds with
    market_regime=None (SignalFusionEngine's own, already-tested
    "honestly MISSING" path), same as every other optional evidence
    source in this pipeline."""
    try:
        async with session_factory() as session:
            return await compute_market_regime(
                session, settings, sector_symbols=settings.regime_sector_symbol_list or None
            )
    except Exception:  # noqa: BLE001 - never let this block the live pipeline
        logger.exception("Momentum pipeline: market regime computation failed, continuing without it")
        return None


async def _run_momentum_pipeline(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    alert_manager: AlertManager,
    is_market_open: Callable[[datetime | None], bool],
) -> None:
    if not is_market_open(None):
        logger.debug("Momentum pipeline skipped=true reason=market_closed")
        return

    moment = utc_now()
    market_regime = await _compute_regime_evidence(session_factory, settings)

    coordinator = MomentumPipelineCoordinator(session_factory, settings, alert_manager)
    result = await coordinator.run_all(market_regime=market_regime, now=moment)

    if result.errors:
        logger.warning(
            "Momentum pipeline run had {count} per-symbol error(s): {errors}",
            count=len(result.errors),
            errors=result.errors[:5],
        )


def register_momentum_pipeline_jobs(
    scheduler_service: SchedulerService,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    alert_manager: AlertManager,
    is_market_open: Callable[[datetime | None], bool],
) -> None:
    async def _job() -> None:
        await _run_momentum_pipeline(session_factory, settings, alert_manager, is_market_open)

    scheduler_service.add_job(
        _job,
        trigger="interval",
        seconds=settings.momentum_pipeline_interval_seconds,
        id=JOB_ID_MOMENTUM_PIPELINE,
        replace_existing=True,
    )

    logger.info(
        "Registered momentum pipeline job: {job_id} (every {seconds}s, market-hours only)",
        job_id=JOB_ID_MOMENTUM_PIPELINE,
        seconds=settings.momentum_pipeline_interval_seconds,
    )
