"""ScannerManager: runs one `BaseScanner` strategy across a batch of symbols.

Per-symbol failures are isolated (one bad symbol doesn't abort the run —
same pattern as `MarketDataCollector` and `FeatureEngine`), and a symbol
already scanned for the current feature date is skipped entirely rather
than re-scanned, which is both the "no duplicate alert" mechanism and a
cheap early-exit.
"""

from dataclasses import dataclass, field

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.symbol import Symbol
from app.repositories.scanner_repository import ScannerLogRepository, ScannerResultRepository
from app.scanner.base_scanner import BaseScanner


@dataclass
class ScannerRunStats:
    symbols_scanned: int = 0
    qualified_count: int = 0
    rejected_count: int = 0
    error_count: int = 0
    errors: list[str] = field(default_factory=list)


class ScannerManager:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def run_scanner(
        self, scanner: BaseScanner, symbols: list[Symbol], run_id: int
    ) -> ScannerRunStats:
        stats = ScannerRunStats(symbols_scanned=len(symbols))

        for symbol in symbols:
            try:
                await self._scan_one(scanner, symbol, run_id, stats)
            except Exception as exc:  # noqa: BLE001 - isolate per-symbol failures
                logger.exception(
                    "{scanner} failed for symbol_id={symbol_id}",
                    scanner=scanner.name,
                    symbol_id=symbol.id,
                )
                stats.error_count += 1
                stats.errors.append(f"{symbol.symbol}: {exc}")
                await self._try_log(run_id, scanner.name, symbol.id, "error", str(exc))

        logger.info(
            "{scanner} run: {scanned} scanned, {qualified} qualified, {rejected} rejected, {errors} errors",
            scanner=scanner.name,
            scanned=stats.symbols_scanned,
            qualified=stats.qualified_count,
            rejected=stats.rejected_count,
            errors=stats.error_count,
        )
        return stats

    # --- internals -----------------------------------------------------

    async def _scan_one(
        self, scanner: BaseScanner, symbol: Symbol, run_id: int, stats: ScannerRunStats
    ) -> None:
        async with self._session_factory() as session:
            result_repo = ScannerResultRepository(session)
            log_repo = ScannerLogRepository(session)

            context = await scanner.build_context(session, symbol)
            if context is None:
                await log_repo.log(
                    run_id=run_id,
                    scanner_name=scanner.name,
                    level="info",
                    message="no context available (e.g. no features computed yet)",
                    symbol_id=symbol.id,
                )
                stats.rejected_count += 1
                await session.commit()
                return

            if await result_repo.exists_for_date(symbol.id, scanner.name, context.scan_date):
                return  # already scanned for this date — nothing new to do

            validation = scanner.validate(context)
            if not validation.valid:
                await log_repo.log(
                    run_id=run_id,
                    scanner_name=scanner.name,
                    level="info",
                    message=f"validation failed: {validation.reason}",
                    symbol_id=symbol.id,
                )
                stats.rejected_count += 1
                await session.commit()
                return

            outcome = scanner.scan(context)
            score = scanner.score(context)
            await scanner.save_results(result_repo, context, outcome, score, context.scan_date)

            if outcome.qualified:
                stats.qualified_count += 1
            else:
                stats.rejected_count += 1
                await log_repo.log(
                    run_id=run_id,
                    scanner_name=scanner.name,
                    level="info",
                    message=outcome.reason,
                    symbol_id=symbol.id,
                )

            await session.commit()

    async def _try_log(
        self, run_id: int, scanner_name: str, symbol_id: int, level: str, message: str
    ) -> None:
        """Best-effort log write in a fresh session — the session that hit the
        original error may be unusable, so this doesn't reuse it."""
        try:
            async with self._session_factory() as session:
                await ScannerLogRepository(session).log(
                    run_id=run_id,
                    scanner_name=scanner_name,
                    level=level,
                    message=message,
                    symbol_id=symbol_id,
                )
                await session.commit()
        except Exception:  # noqa: BLE001 - logging must never itself raise
            logger.warning("Failed to write scanner_logs entry for run_id={run_id}", run_id=run_id)
