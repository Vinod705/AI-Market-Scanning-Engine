"""F&O universe refresh job.

Reuses the same 5paisa scrip-master call `register_market_data_jobs`
already exercises daily — refreshes `fno_universe` once a day, shortly
after the symbol master refresh (07:00 IST) so newly-listed symbols and
newly-added/expired F&O contracts are reconciled together.
"""

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.providers.base_provider import ProviderError
from app.providers.fivepaisa_provider import FivePaisaProvider
from app.repositories.fno_universe_repository import FnoUniverseRepository
from app.repositories.market_repository import SymbolRepository
from app.scheduler.service import SchedulerService

JOB_ID_FNO_UNIVERSE_REFRESH = "fno_universe_refresh"


def register_universe_jobs(
    scheduler_service: SchedulerService,
    provider: FivePaisaProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async def _refresh_job() -> None:
        try:
            if not provider.is_connected():
                await provider.connect()
            roots = await provider.get_fno_symbol_roots()
        except ProviderError as exc:
            logger.warning("F&O universe refresh skipped: provider error: {error}", error=exc)
            return

        async with session_factory() as session:
            all_symbols = await SymbolRepository(session).list_active()
            matched_ids = [s.id for s in all_symbols if s.symbol in roots]
            count = await FnoUniverseRepository(session).replace_all(matched_ids)
            await session.commit()

        logger.info("F&O universe refreshed: {count} symbols", count=count)

    scheduler_service.add_job(
        _refresh_job,
        trigger="cron",
        hour=7,
        minute=15,
        id=JOB_ID_FNO_UNIVERSE_REFRESH,
        replace_existing=True,
    )
    logger.info("Registered F&O universe refresh job: {job_id}", job_id=JOB_ID_FNO_UNIVERSE_REFRESH)
