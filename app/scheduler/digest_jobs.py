"""Twice-daily (7AM / 4PM IST) breakout digest job -- summarizes IPO and
F&O alerts newly created since the last digest, sent via the same IPO/F&O
Telegram bots the real-time alert pipeline already uses (see
app.notifications.router.NotificationRouter). Purely additive: does not
touch the existing per-minute decision-engine/alert pipeline at all.
"""

from collections import Counter
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.momentum.momentum_models import ALERT_WORTHY_STATES
from app.notifications.base import NotificationProvider
from app.notifications.digest import (
    DigestEntry,
    build_digest_text,
    build_market_overview_text,
    split_by_universe,
)
from app.repositories.alert_repository import AlertRepository
from app.repositories.market_regime_snapshot_repository import MarketRegimeSnapshotRepository
from app.repositories.market_repository import SymbolRepository
from app.repositories.momentum_state_repository import MomentumStateRepository
from app.repositories.sector_rrg_snapshot_repository import SectorRrgSnapshotRepository
from app.scheduler.service import SchedulerService

JOB_ID_DIGEST_MORNING = "breakout_digest_morning"
JOB_ID_DIGEST_EVENING = "breakout_digest_evening"

# Fixed lookback per run rather than a persisted "last digest sent at"
# timestamp: the two jobs fire at fixed times (7:00 and 16:00 IST), so the
# window each one covers is a known constant relative to its own fire time.
# Simpler and avoids a new table; the trade-off is that a missed/delayed
# run isn't automatically made up (acceptable for a summary digest, unlike
# the real-time alert pipeline this doesn't replace).
_MORNING_LOOKBACK_HOURS = 15.0  # since yesterday 16:00 IST
_EVENING_LOOKBACK_HOURS = 9.0  # since today 07:00 IST


async def _send_universe_digest(
    provider: NotificationProvider, entries: list[DigestEntry], *, universe_label: str
) -> None:
    text = build_digest_text(entries, universe_label=universe_label)
    if text is None:
        return
    result = await provider.send_message(recipient=provider.default_recipient, text=text)
    if not result.success:
        logger.warning(
            "{universe} breakout digest delivery failed: {error}",
            universe=universe_label,
            error=result.error_message,
        )


async def _send_market_overview(
    session_factory: async_sessionmaker[AsyncSession],
    provider: NotificationProvider,
    *,
    since: datetime,
    lookback_hours: float,
) -> None:
    """Summarizes the same stored analytics the dashboard reads (Phase
    15) — a pure DB read + format + send, no computation of its own. See
    `app.notifications.digest.build_market_overview_text`'s docstring."""
    async with session_factory() as session:
        regime_row = await MarketRegimeSnapshotRepository(session).get_latest()
        sector_rows = await SectorRrgSnapshotRepository(session).get_latest_batch()
        trigger_count = await MomentumStateRepository(session).count_transitions_since(
            since, ALERT_WORTHY_STATES
        )

    sector_counts = Counter(
        row.rotation_state for row in sector_rows if row.rotation_state is not None
    )

    text = build_market_overview_text(
        regime=regime_row.regime if regime_row is not None else None,
        regime_score=float(regime_row.score) if regime_row and regime_row.score else None,
        sector_rotation_counts=dict(sector_counts),
        momentum_trigger_count=trigger_count,
        lookback_hours=lookback_hours,
    )
    if text is None:
        return
    result = await provider.send_message(recipient=provider.default_recipient, text=text)
    if not result.success:
        logger.warning("Market overview digest delivery failed: {error}", error=result.error_message)


async def _run_digest(
    session_factory: async_sessionmaker[AsyncSession],
    ipo_provider: NotificationProvider,
    fno_provider: NotificationProvider,
    default_provider: NotificationProvider,
    *,
    lookback_hours: float,
) -> None:
    since = datetime.now(UTC) - timedelta(hours=lookback_hours)

    async with session_factory() as session:
        alerts = await AlertRepository(session).list_created_since(since)
        symbol_ids = {alert.symbol_id for alert in alerts}
        symbols = {
            s.id: s.symbol for s in await SymbolRepository(session).list_by_ids(list(symbol_ids))
        }

    entries = []
    for alert in alerts:
        category = alert.feature_snapshot.get("alert_category")
        entries.append(
            DigestEntry(
                symbol=symbols.get(alert.symbol_id, str(alert.symbol_id)),
                alert_category=category if isinstance(category, str) else None,
                score=float(alert.score),
            )
        )
    ipo_entries, fno_entries = split_by_universe(entries)

    await _send_universe_digest(ipo_provider, ipo_entries, universe_label="IPO")
    await _send_universe_digest(fno_provider, fno_entries, universe_label="F&O")
    await _send_market_overview(
        session_factory, default_provider, since=since, lookback_hours=lookback_hours
    )

    logger.info(
        "Breakout digest sent: {ipo} IPO signal(s), {fno} F&O signal(s), lookback={hours}h",
        ipo=len(ipo_entries),
        fno=len(fno_entries),
        hours=lookback_hours,
    )


def register_digest_jobs(
    scheduler_service: SchedulerService,
    session_factory: async_sessionmaker[AsyncSession],
    ipo_provider: NotificationProvider,
    fno_provider: NotificationProvider,
    default_provider: NotificationProvider,
) -> None:
    async def _morning_job() -> None:
        await _run_digest(
            session_factory,
            ipo_provider,
            fno_provider,
            default_provider,
            lookback_hours=_MORNING_LOOKBACK_HOURS,
        )

    async def _evening_job() -> None:
        await _run_digest(
            session_factory,
            ipo_provider,
            fno_provider,
            default_provider,
            lookback_hours=_EVENING_LOOKBACK_HOURS,
        )

    scheduler_service.add_job(
        _morning_job,
        trigger="cron",
        hour=7,
        minute=0,
        id=JOB_ID_DIGEST_MORNING,
        replace_existing=True,
    )
    scheduler_service.add_job(
        _evening_job,
        trigger="cron",
        hour=16,
        minute=0,
        id=JOB_ID_DIGEST_EVENING,
        replace_existing=True,
    )

    logger.info(
        "Registered breakout digest jobs: {morning} (07:00), {evening} (16:00)",
        morning=JOB_ID_DIGEST_MORNING,
        evening=JOB_ID_DIGEST_EVENING,
    )
