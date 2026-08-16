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

from app.config.settings import Settings
from app.core.time import utc_now
from app.data.market_updater import MarketStatusUpdater
from app.data.validator import DataValidator, ValidationError
from app.models.symbol import Symbol
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
    # Symbol refresh only: names deactivated this run (universe
    # reconciliation, see MarketDataCollector._reconcile_universe), and a
    # human-readable note when reconciliation was skipped as unsafe.
    deactivated_symbols: list[str] = field(default_factory=list)
    reconciliation_note: str | None = None

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
        settings: Settings | None = None,
    ) -> None:
        self._provider = provider
        self._session_factory = session_factory
        self._market_updater = market_updater
        self._settings = settings or Settings()

    async def collect_symbols(self) -> CollectorRunResult:
        """Refresh the symbol master from the provider into the database."""
        return await self._run("symbol refresh", self._collect_symbols_impl)

    async def collect_intraday(self, symbols: list[Symbol] | None = None) -> CollectorRunResult:
        """Pull the latest intraday candles for `symbols`, or every active
        symbol when omitted (today's default REST-sweep behavior,
        unchanged). Passing a subset is how
        `app.providers.upstox_websocket.UpstoxMarketFeed` backfills exactly
        the symbols it just (re)subscribed to on startup/reconnect, without
        re-pulling the whole universe."""
        return await self._run(
            "intraday collection", lambda: self._collect_intraday_impl(symbols)
        )

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
        # A total-fetch failure (get_symbols() raising) never reaches this
        # method at all — it's caught by _run()'s own try/except, which
        # records a market_updater failure and returns before any of this
        # runs. Only a *successful* fetch (however small) gets here, which
        # is exactly the case the reconciliation safety-fraction guard
        # below still needs to handle: a provider can return 200 (empty
        # body still parses to an empty/short list) without ever raising.
        result = CollectorRunResult()
        provider_symbols = await self._provider.get_symbols()
        result.symbols_processed = len(provider_symbols)

        confirmed_symbols: set[str] = set()
        async with self._session_factory() as session:
            repo = SymbolRepository(session)
            for provider_symbol in provider_symbols:
                try:
                    DataValidator.validate_symbol(provider_symbol)
                    await repo.upsert(provider_symbol)
                    result.success_count += 1
                    confirmed_symbols.add(provider_symbol.symbol)
                except ValidationError as exc:
                    result.failed_count += 1
                    result.errors.append(str(exc))

            await self._reconcile_universe(repo, confirmed_symbols, result)
            await session.commit()

        return result

    async def _reconcile_universe(
        self, repo: SymbolRepository, confirmed_symbols: set[str], result: CollectorRunResult
    ) -> None:
        """Deactivates symbols genuinely missing from this run's
        successfully-fetched/validated universe — the "removed" half of
        reconciliation `upsert` doesn't handle (it only ever adds/updates,
        never deactivates). Guarded against a partial-but-non-raising
        fetch: if the confirmed set is suspiciously small relative to
        what was already active, skip deactivation entirely rather than
        risk mass-deactivating real symbols over a transient truncated
        response."""
        previously_active = await repo.list_active()
        previous_count = len(previously_active)
        if previous_count == 0:
            return  # nothing to reconcile against yet (e.g. a fresh DB)

        min_fraction = self._settings.universe_reconciliation_min_fraction
        if len(confirmed_symbols) < previous_count * min_fraction:
            result.reconciliation_note = (
                f"skipped: fetch returned {len(confirmed_symbols)} confirmed symbols, "
                f"below {min_fraction:.0%} of the {previous_count} currently active — "
                "treated as a suspicious/partial response, not a real mass-delisting"
            )
            logger.warning("Universe reconciliation {note}", note=result.reconciliation_note)
            return

        deactivated = await repo.deactivate_missing(confirmed_symbols)
        result.deactivated_symbols = deactivated
        if deactivated:
            logger.info(
                "Universe reconciliation: deactivated {count} symbol(s) no longer in the "
                "broker's universe: {symbols}",
                count=len(deactivated),
                symbols=", ".join(deactivated[:20])
                + (f" (+{len(deactivated) - 20} more)" if len(deactivated) > 20 else ""),
            )

    async def _collect_intraday_impl(self, symbols: list[Symbol] | None) -> CollectorRunResult:
        return await self._collect_candles(intraday=True, symbols=symbols)

    async def _collect_daily_impl(self) -> CollectorRunResult:
        return await self._collect_candles(intraday=False, symbols=None)

    async def _collect_candles(
        self, *, intraday: bool, symbols: list[Symbol] | None
    ) -> CollectorRunResult:
        result = CollectorRunResult()

        if symbols is None:
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
