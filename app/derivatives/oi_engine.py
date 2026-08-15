"""OiEngine: fetches, computes, and persists open-interest observations for
the F&O-eligible universe (`app.repositories.fno_universe_repository`).

Not every equity has derivative contracts — this deliberately only
iterates `FnoUniverse` symbols, never the full LISTED universe, and a
symbol with no chain/futures data for its current expiry cycle simply
produces zero rows for that run, not a guessed/zero-filled one.

Standalone and on-demand (same shape as `app.data.collector.MarketDataCollector`)
— not yet wired into any scheduler job, `ScannerEngine`, or `DecisionEngine`
pass. Persisted `OiObservation` rows are structured so a future
SignalFusionEngine can read them as evidence; nothing about the existing
scanner/decision scoring is touched here.
"""

from dataclasses import dataclass, field

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.derivatives.futures_oi import reading_from_futures_bars
from app.derivatives.option_chain import readings_from_chain
from app.providers.base_provider import DerivativesProvider
from app.repositories.fno_universe_repository import FnoUniverseRepository
from app.repositories.market_repository import SymbolRepository
from app.repositories.oi_repository import OiObservationRepository


@dataclass
class OiEngineRunStats:
    symbols_processed: int = 0
    observations_written: int = 0
    symbols_with_no_data: int = 0
    error_count: int = 0
    errors: list[str] = field(default_factory=list)


class OiEngine:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], provider: DerivativesProvider
    ) -> None:
        self._session_factory = session_factory
        self._provider = provider

    async def run(self) -> OiEngineRunStats:
        stats = OiEngineRunStats()

        async with self._session_factory() as session:
            symbol_ids = await FnoUniverseRepository(session).list_symbol_ids()
            symbols = await SymbolRepository(session).list_by_ids(symbol_ids)

        for symbol in symbols:
            stats.symbols_processed += 1
            try:
                readings = list(
                    readings_from_chain(
                        await self._provider.get_option_chain(symbol.symbol, symbol.instrument_token)
                    )
                )

                futures_bars = await self._provider.get_futures_oi_history(symbol.symbol)
                futures_reading = reading_from_futures_bars(symbol.symbol, futures_bars)
                if futures_reading is not None:
                    readings.append(futures_reading)

                if not readings:
                    stats.symbols_with_no_data += 1
                    continue

                async with self._session_factory() as session:
                    repo = OiObservationRepository(session)
                    for reading in readings:
                        await repo.insert(symbol.id, reading)
                    await session.commit()
                stats.observations_written += len(readings)
            except Exception as exc:  # noqa: BLE001 - isolate per-symbol failures, same as ScannerManager/MarketDataCollector
                logger.exception("OiEngine failed for symbol={symbol}", symbol=symbol.symbol)
                stats.error_count += 1
                stats.errors.append(f"{symbol.symbol}: {exc}")

        logger.info(
            "OI engine run: {processed} symbols, {written} observations, "
            "{no_data} with no chain/futures data, {errors} errors",
            processed=stats.symbols_processed,
            written=stats.observations_written,
            no_data=stats.symbols_with_no_data,
            errors=stats.error_count,
        )
        return stats
