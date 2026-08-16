"""ScannerEngine: the scheduler's entry point for the scanner layer.

Iterates every scanner in the `ScannerRegistry`, and for each one: opens a
`scanner_runs` row, delegates the actual scanning to `ScannerManager`, and
closes out the run with aggregate counts. This is the class `main.py`
constructs and the scheduler job calls — same role `FeatureEngine` and
`MarketDataCollector` play in Phases 2/3.
"""

from dataclasses import dataclass, field

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.core.time import utc_now
from app.health.freshness import check_feature_freshness
from app.repositories.market_repository import SymbolRepository
from app.repositories.scanner_repository import ScannerRunRepository
from app.repositories.worker_heartbeat_repository import WorkerHeartbeatRepository
from app.scanner.scanner_manager import ScannerManager
from app.scanner.scanner_registry import ScannerRegistry

SCANNER_ENGINE_WORKER_NAME = "scanner_engine"


@dataclass
class ScannerEngineResult:
    scanners_run: int = 0
    symbols_scanned: int = 0
    qualified_count: int = 0
    rejected_count: int = 0
    error_count: int = 0
    errors: list[str] = field(default_factory=list)
    # Visibility only — see app.health.freshness's module docstring. Does
    # NOT block scanning: a scanner's own honestly-computed reason/
    # failed_rules already say what it saw, and DecisionEvaluator's
    # data_freshness rule is the actual gate against an ALERT firing off
    # a stale LISTED-scanner result. This just makes "the underlying
    # features haven't advanced" loudly visible instead of a silent gap
    # someone has to notice on their own, which is what actually happened
    # in production before this field existed.
    features_stale: bool = False
    freshness_reason: str = ""


class ScannerEngine:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        registry: ScannerRegistry,
        settings: Settings | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._registry = registry
        self._manager = ScannerManager(session_factory)
        self._settings = settings or Settings()

    async def run_all(self) -> ScannerEngineResult:
        result = ScannerEngineResult()

        async with self._session_factory() as session:
            symbols = await SymbolRepository(session).list_active()
            freshness = await check_feature_freshness(session, self._settings)
        result.features_stale = freshness.is_stale
        result.freshness_reason = freshness.reason
        if freshness.is_stale:
            logger.warning(
                "Scanner engine: daily_features is STALE — {reason}",
                reason=freshness.reason,
            )

        for scanner in self._registry.get_all():
            async with self._session_factory() as session:
                candidate_symbols = await scanner.get_candidate_symbols(session, symbols)

            start_time = utc_now()
            async with self._session_factory() as session:
                run = await ScannerRunRepository(session).start(scanner.name, start_time)
                run_id = run.id
                await session.commit()

            stats = await self._manager.run_scanner(scanner, candidate_symbols, run_id)

            finish_time = utc_now()
            async with self._session_factory() as session:
                run_repo = ScannerRunRepository(session)
                # Merge the detached `run` object into this session rather than
                # re-fetching by id — cheaper, and avoids an extra round-trip.
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
        await self._ping_heartbeat(
            f"{result.scanners_run} scanner(s), {result.qualified_count} qualified"
        )
        return result

    async def _ping_heartbeat(self, detail: str) -> None:
        """See app.pipeline.worker._ping_heartbeat's docstring."""
        try:
            async with self._session_factory() as session:
                await WorkerHeartbeatRepository(session).ping_success(
                    SCANNER_ENGINE_WORKER_NAME, detail=detail
                )
                await session.commit()
        except Exception:  # noqa: BLE001 - a heartbeat write must never affect real work
            logger.warning("ScannerEngine: failed to record heartbeat")
