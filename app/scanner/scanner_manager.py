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
from app.scanner.models import ScannerContext

# Bounded batch size for the session/commit-sharing loops below. Confirmed
# live this session: after the read-side bulk fix cut breakout_v1's query
# count 3x (22,047 -> 7,394), its wall-clock time barely moved (73.60s ->
# 72.34s) — the remaining cost is per-symbol session/commit overhead
# (connection checkout + BEGIN + COMMIT), not query count. Small and
# bounded on purpose, not "one huge transaction": keeps any single
# in-flight transaction's size/lock footprint modest, and caps how much
# work a mid-batch crash (process kill, DB restart) could lose to a single
# batch's uncommitted work rather than the whole run.
_BATCH_SIZE = 200


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

        # Bug fix: this used to call build_context (2 queries for the
        # default implementation) then exists_for_date (1 query) inside a
        # per-symbol loop — confirmed live this session as the dominant
        # pipeline-throughput bottleneck (breakout_v1 alone: 73.60s /
        # 22,047 queries of a 154.54s cycle over 9,598 symbols).
        # build_context_bulk defaults to looping build_context per symbol
        # (unchanged behavior for scanners that don't override it — the
        # F&O/IPO/pre_breakout candidate scanners), and BreakoutScanner
        # overrides it with a true 2-query bulk fetch. Either way the
        # resulting contexts are identical to what the old per-symbol path
        # produced; only the query pattern changes.
        async with self._session_factory() as session:
            contexts = await scanner.build_context_bulk(session, symbols)

        no_context = [s for s in symbols if contexts.get(s.id) is None]

        have_context: list[tuple[Symbol, ScannerContext]] = []
        for symbol in symbols:
            context = contexts.get(symbol.id)
            if context is not None:
                have_context.append((symbol, context))

        # Bulk dedup check — same (symbol, scanner, date) idempotency
        # exists_for_date already enforced ("no duplicate alert"), computed
        # once for the batch instead of once per symbol. scan_date is kept
        # per-symbol (it's each symbol's own latest feature date, not a
        # single shared "today") — see exists_bulk's docstring.
        async with self._session_factory() as session:
            pairs = [(symbol.id, context.scan_date) for symbol, context in have_context]
            already_scanned = await ScannerResultRepository(session).exists_bulk(
                pairs, scanner.name
            )
        to_evaluate = [
            (symbol, context)
            for symbol, context in have_context
            if (symbol.id, context.scan_date) not in already_scanned
        ]

        # Bug fix: this used to open one session + one commit per symbol
        # (via _try_log for the no-context case, _evaluate_one for
        # everything else) — confirmed live this session as breakout_v1's
        # remaining bottleneck after the read-side bulk fix: query count
        # dropped 3x but wall-clock time barely moved, because the cost
        # was connection-checkout/BEGIN/COMMIT overhead, not query count.
        # Bounded batches share one session/commit across up to
        # _BATCH_SIZE symbols; each symbol still gets its own SAVEPOINT
        # (session.begin_nested()) so one symbol's failure rolls back only
        # that symbol's work and leaves the rest of the batch's session
        # usable — same error-isolation guarantee as the old per-symbol
        # session, without a full session per symbol.
        for i in range(0, len(no_context), _BATCH_SIZE):
            await self._log_no_context_batch(
                scanner, no_context[i : i + _BATCH_SIZE], run_id, stats
            )

        for i in range(0, len(to_evaluate), _BATCH_SIZE):
            await self._evaluate_batch(scanner, to_evaluate[i : i + _BATCH_SIZE], run_id, stats)

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

    async def _log_no_context_batch(
        self, scanner: BaseScanner, batch: list[Symbol], run_id: int, stats: ScannerRunStats
    ) -> None:
        async with self._session_factory() as session:
            log_repo = ScannerLogRepository(session)
            for symbol in batch:
                stats.rejected_count += 1
                try:
                    async with session.begin_nested():
                        await log_repo.log(
                            run_id=run_id,
                            scanner_name=scanner.name,
                            level="info",
                            message="no context available (e.g. no features computed yet)",
                            symbol_id=symbol.id,
                        )
                except Exception:  # noqa: BLE001 - logging must never itself raise
                    logger.warning(
                        "Failed to write scanner_logs entry for run_id={run_id}", run_id=run_id
                    )
            await session.commit()

    async def _evaluate_batch(
        self,
        scanner: BaseScanner,
        batch: list[tuple[Symbol, ScannerContext]],
        run_id: int,
        stats: ScannerRunStats,
    ) -> None:
        async with self._session_factory() as session:
            result_repo = ScannerResultRepository(session)
            log_repo = ScannerLogRepository(session)

            for symbol, context in batch:
                try:
                    async with session.begin_nested():
                        await self._evaluate_one(
                            scanner, symbol, context, run_id, stats, result_repo, log_repo
                        )
                except Exception as exc:  # noqa: BLE001 - isolate per-symbol failures
                    logger.exception(
                        "{scanner} failed for symbol_id={symbol_id}",
                        scanner=scanner.name,
                        symbol_id=symbol.id,
                    )
                    stats.error_count += 1
                    stats.errors.append(f"{symbol.symbol}: {exc}")
                    try:
                        async with session.begin_nested():
                            await log_repo.log(
                                run_id=run_id,
                                scanner_name=scanner.name,
                                level="error",
                                message=str(exc),
                                symbol_id=symbol.id,
                            )
                    except Exception:  # noqa: BLE001 - logging must never itself raise
                        logger.warning(
                            "Failed to write scanner_logs entry for run_id={run_id}",
                            run_id=run_id,
                        )

            await session.commit()

    async def _evaluate_one(
        self,
        scanner: BaseScanner,
        symbol: Symbol,
        context: ScannerContext,
        run_id: int,
        stats: ScannerRunStats,
        result_repo: ScannerResultRepository,
        log_repo: ScannerLogRepository,
    ) -> None:
        """One symbol's validate -> scan -> score -> save, run inside the
        caller's SAVEPOINT — no session/commit of its own (the batch commits
        once for everyone, see _evaluate_batch)."""
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
