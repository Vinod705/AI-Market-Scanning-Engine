"""One-off/manual trigger for MarketDataCollector.collect_daily().

The scheduler already runs this once a day (16:00 IST, see
app.scheduler.jobs); this script exists to trigger the same collector
method on demand, e.g. right after widening `FIVEPAISA_DAILY_HISTORY_DAYS`
so existing symbols pick up the deeper history immediately instead of
waiting for the next scheduled run. Upserts are keyed on (symbol_id, date),
so re-running this is always safe.
"""

import asyncio

from loguru import logger

from app.config.settings import get_settings
from app.data.collector import MarketDataCollector
from app.data.market_updater import MarketStatusUpdater
from app.database.session import AsyncSessionLocal
from app.providers.base_provider import ProviderSymbol
from app.providers.fivepaisa_provider import FivePaisaProvider
from app.repositories.market_repository import SymbolRepository


async def _seed_symbol_cache(provider: FivePaisaProvider) -> int:
    """Seed the provider's symbol cache from the local `symbols` table
    instead of re-downloading the ~165k-row scrip master via get_scrips().

    Every active symbol already has its instrument_token from the last
    successful symbol-master sync, so there's nothing get_scrips() would
    tell us that the DB doesn't already have — and skipping it avoids
    hammering that endpoint again right after it started timing out/
    returning empty responses during an earlier run.
    """
    async with AsyncSessionLocal() as session:
        symbols = await SymbolRepository(session).list_active()

    provider_symbols = {
        symbol.symbol: ProviderSymbol(
            symbol=symbol.symbol,
            exchange=symbol.exchange,
            instrument_token=symbol.instrument_token,
            company_name=symbol.company_name,
            sector=symbol.sector,
            industry=symbol.industry,
            is_ipo=symbol.is_ipo,
        )
        for symbol in symbols
    }
    provider._symbol_cache = provider_symbols  # noqa: SLF001 - deliberate one-off cache seed
    return len(provider_symbols)


async def main() -> None:
    settings = get_settings()
    provider = FivePaisaProvider(settings)
    market_updater = MarketStatusUpdater(AsyncSessionLocal)
    collector = MarketDataCollector(provider, AsyncSessionLocal, market_updater)

    await provider.connect()
    try:
        count = await _seed_symbol_cache(provider)
        logger.info("Seeded symbol cache from DB: {count} symbols", count=count)

        result = await collector.collect_daily()
    finally:
        await provider.disconnect()

    logger.info(
        "Backfill complete: {processed} symbols, {success} succeeded, {failed} failed",
        processed=result.symbols_processed,
        success=result.success_count,
        failed=result.failed_count,
    )
    if result.error_message:
        logger.warning("Sample errors: {errors}", errors=result.error_message)


if __name__ == "__main__":
    asyncio.run(main())
