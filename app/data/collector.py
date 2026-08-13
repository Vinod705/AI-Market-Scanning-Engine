"""Market data collector.

Orchestrates provider -> validator -> repository for three jobs: refreshing
the symbol master, and pulling intraday/daily candles for every active
symbol. Each collector method opens its own database session (these run
from scheduler jobs, outside any HTTP request scope) and isolates
per-symbol failures so one bad symbol doesn't abort the whole run.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.time import utc_now
from app.data.market_updater import MarketStatusUpdater
from app.data.validator import DataValidator, ValidationError
from app.providers.base_provider import MarketDataProvider, ProviderError
from app.repositories.market_repository import (
    CollectorLogRepository,
    PriceRepository,
    SymbolRepository,
)


@dataclass
class CollectorRunResult:
    """Summary of one collector run, mirroring the `collector_logs` row."""

    symbols_processed: int = 0
    success_count: int = 0
    failed_count: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def error_message(self) -> str | None:
        if not self.errors:
            return None
        return "; ".join(self.errors[:10]) + (
            f" (+{len(self.errors) - 10} more)" if len(self.errors) > 10 else ""
        )


class MarketDataCollector:
    """Coordinates symbol/candle collection for a single `MarketDataProvider`."""

    def __init__(
        self,
        provider: MarketDataProvider,
        session_factory: async_sessionmaker[AsyncSession],
        market_updater: MarketStatusUpdater,
    ) -> None:
        self._provider = provider
        self._session_factory = session_factory
        self._market_updater = market_updater

    async def collect_symbols(self) -> CollectorRunResult:
        """Refresh the symbol master from the provider into the database."""
        return await self._run("symbol refresh", self._collect_symbols_impl)

    async def collect_intraday(self) -> CollectorRunResult:
        """Pull the latest intraday candles for every active symbol."""
        return await self._run("intraday collection", self._collect_intraday_impl)

    async def collect_daily(self) -> CollectorRunResult:
        """Pull recent daily candles for every active symbol."""
        return await self._run("daily collection", self._collect_daily_impl)

    # --- internals -----------------------------------------------------

    async def _run(
        self, label: str, impl: Callable[[], Awaitable[CollectorRunResult]]
    ) -> CollectorRunResult:
        start_time = utc_now()
        logger.info("Starting {label}", label=label)

        async with self._session_factory() as session:
            log_repo = CollectorLogRepository(session)
            log = await log_repo.start(start_time)
            await session.commit()

        result = CollectorRunResult()
        try:
            if not self._provider.is_connected():
                await self._provider.connect()
            result = await impl()
        except ProviderError as exc:
            logger.error("{label} aborted: provider error: {error}", label=label, error=exc)
            result.errors.append(str(exc))
            await self._market_updater.record_failure(
                provider_connected=self._provider.is_connected()
            )
        except Exception as exc:  # noqa: BLE001 - never let a scheduler job crash the process
            logger.exception("{label} aborted with unexpected error", label=label)
            result.errors.append(str(exc))
            await self._market_updater.record_failure(
                provider_connected=self._provider.is_connected()
            )
        else:
            await self._market_updater.record_success(
                provider_connected=self._provider.is_connected()
            )

        finish_time = utc_now()
        async with self._session_factory() as session:
            log_repo = CollectorLogRepository(session)
            log = await session.merge(log)
            await log_repo.finish(
                log,
                finish_time=finish_time,
                symbols_processed=result.symbols_processed,
                success_count=result.success_count,
                failed_count=result.failed_count,
                error_message=result.error_message,
            )
            await session.commit()

        logger.info(
            "Finished {label}: {success} succeeded, {failed} failed, {duration:.1f}s",
            label=label,
            success=result.success_count,
            failed=result.failed_count,
            duration=(finish_time - start_time).total_seconds(),
        )
        return result

    async def _collect_symbols_impl(self) -> CollectorRunResult:
        result = CollectorRunResult()
        provider_symbols = await self._provider.get_symbols()
        result.symbols_processed = len(provider_symbols)

        async with self._session_factory() as session:
            repo = SymbolRepository(session)
            for provider_symbol in provider_symbols:
                try:
                    DataValidator.validate_symbol(provider_symbol)
                    await repo.upsert(provider_symbol)
                    result.success_count += 1
                except ValidationError as exc:
                    result.failed_count += 1
                    result.errors.append(str(exc))
            await session.commit()

        return result

    async def _collect_intraday_impl(self) -> CollectorRunResult:
        return await self._collect_candles(intraday=True)

    async def _collect_daily_impl(self) -> CollectorRunResult:
        return await self._collect_candles(intraday=False)

    async def _collect_candles(self, *, intraday: bool) -> CollectorRunResult:
        result = CollectorRunResult()

        async with self._session_factory() as session:
            symbols = await SymbolRepository(session).list_active()
        result.symbols_processed = len(symbols)

        for symbol in symbols:
            try:
                candles = (
                    await self._provider.get_intraday(symbol.symbol)
                    if intraday
                    else await self._provider.get_daily(symbol.symbol)
                )
                clean_candles = DataValidator.validate_candles(candles, context=symbol.symbol)

                async with self._session_factory() as session:
                    price_repo = PriceRepository(session)
                    if intraday:
                        await price_repo.upsert_intraday_many(symbol.id, clean_candles)
                    else:
                        await price_repo.upsert_daily_many(symbol.id, clean_candles)
                    await session.commit()

                result.success_count += 1
            except ProviderError as exc:
                logger.warning("Skipping {symbol}: {error}", symbol=symbol.symbol, error=exc)
                result.failed_count += 1
                result.errors.append(f"{symbol.symbol}: {exc}")
            except Exception as exc:  # noqa: BLE001 - isolate per-symbol failures
                logger.exception("Unexpected error collecting {symbol}", symbol=symbol.symbol)
                result.failed_count += 1
                result.errors.append(f"{symbol.symbol}: {exc}")

        return result
