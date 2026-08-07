"""ScannerEngine: the scheduler's entry point for the scanner layer.

Iterates every scanner in the `ScannerRegistry`, and for each one: opens a
`scanner_runs` row, delegates the actual scanning to `ScannerManager`, and
closes out the run with aggregate counts. This is the class `main.py`
constructs and the scheduler job calls — same role `FeatureEngine` and
`MarketDataCollector` play in Phases 2/3.
"""

from dataclasses import dataclass, field
from datetime import datetime

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.market_repository import SymbolRepository
from app.repositories.scanner_repository import ScannerRunRepository
from app.scanner.scanner_manager import ScannerManager
from app.scanner.scanner_registry import ScannerRegistry


@dataclass
class ScannerEngineResult:
    scanners_run: int = 0
    symbols_scanned: int = 0
    qualified_count: int = 0
    rejected_count: int = 0
    error_count: int = 0
    errors: list[str] = field(default_factory=list)


class ScannerEngine:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], registry: ScannerRegistry
    ) -> None:
        self._session_factory = session_factory
        self._registry = registry
        self._manager = ScannerManager(session_factory)

    async def run_all(self) -> ScannerEngineResult:
        result = ScannerEngineResult()

        async with self._session_factory() as session:
            symbols = await SymbolRepository(session).list_active()

        for scanner in self._registry.get_all():
            start_time = datetime.now()
            async with self._session_factory() as session:
                run = await ScannerRunRepository(session).start(scanner.name, start_time)
                run_id = run.id
                await session.commit()

            stats = await self._manager.run_scanner(scanner, symbols, run_id)

            finish_time = datetime.now()
            async with self._session_factory() as session:
                run_repo = ScannerRunRepository(session)
                # Merge the detached `run` object into this session rather than
                # re-fetching by id: a re-fetch would pull `start_time` back as
                # tz-aware (the column is TIMESTAMPTZ), which can't be subtracted
                # from the naive `finish_time` above. `merge` keeps the original
                # naive value, matching the pattern in `app.data.collector`.
                run_row = await session.merge(run)
                await run_repo.finish(
                    run_row,
                    finish_time=finish_time,
                    symbols_scanned=stats.symbols_scanned,
                    qualified_count=stats.qualified_count,
                    rejected_count=stats.rejected_count,
                    error_count=stats.error_count,
                )
                await session.commit()

            result.scanners_run += 1
            result.symbols_scanned += stats.symbols_scanned
            result.qualified_count += stats.qualified_count
            result.rejected_count += stats.rejected_count
            result.error_count += stats.error_count
            result.errors.extend(stats.errors)

        if result.scanners_run:
            logger.info(
                "Scanner engine run: {scanners} scanner(s), {qualified} qualified, {rejected} rejected",
                scanners=result.scanners_run,
                qualified=result.qualified_count,
                rejected=result.rejected_count,
            )
        return result
