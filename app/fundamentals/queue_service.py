"""FundamentalQueueService: processes candidates' fundamental data in
small, paced batches instead of firing a Trendlyne request per scanned
symbol.

INCIDENT THAT MOTIVATED THIS: a manual verification scan called
Trendlyne once per scanned symbol (~220 distinct symbols x 3 calls each)
in a tight sequential loop with no delay, and exhausted the account's
rate limit ("Maximum weighted channel limit exceeded") within minutes —
long before reaching the small number of symbols that could plausibly
qualify. The technical scanner itself was never concurrent (see
`app.scanner.scanner_manager`, a plain sequential `for` loop) — the
problem was pure throughput: hundreds of requests fired back-to-back
with no pacing, no batching, and no circuit breaker.

This service is the fix: `build_candidate()` no longer calls Trendlyne at
all (see `app.candidates.builder`) — every candidate is persisted with
`fundamental_status="PENDING"`, and this service, running on its own
scheduler job (see `app.scheduler.fundamental_queue_jobs`), works through
the backlog later:

  Technical Scanner  →  Candidate Queue (PENDING rows in scanner_results)
        ↓ (own schedule, never blocks the scanner above)
  Fundamental Queue  →  Trendlyne, paced in small batches

Every fetch *attempt* (success, cache hit, rate-limited, or failed) is
recorded in `fundamental_fetch_log` — this is what makes the queue
restart-safe (a fresh process re-derives "requests today" and "are we
still cooling down" from that table, not from in-memory state) and is
also the data source for the /health and dashboard queue-status views.

Phase 9: every attempt also upserts `fundamental_snapshots` (see
`app.repositories.fundamental_snapshot_repository`), the general-purpose
"cached snapshot" a symbol not currently going through this candidate
queue could still read from — `FundamentalSnapshotRepository.get_cached()`
is a pure DB read with no provider call in it at all, so consuming it
can never block on an external request the way a live fetch could. This
doesn't change the existing embedded-in-`scanner_results.feature_snapshot`
behavior below at all; it's an additional, independent write next to it.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from loguru import logger
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.candidates.builder import (
    combine_scores,
    factor_to_dict,
    field_snapshot_to_dict,
    quality_from_score,
)
from app.config.settings import Settings
from app.fundamentals.models import FundamentalData
from app.fundamentals.orchestrator import StatusAwareFundamentalProvider
from app.fundamentals.provider import FundamentalDataProvider
from app.fundamentals.queue_models import FetchStatus, QueueRunResult, QueueStatusSummary
from app.fundamentals.scorer import FundamentalScorer
from app.models.scanner_result import ScannerResult
from app.repositories.fundamental_fetch_log_repository import FundamentalFetchLogRepository
from app.repositories.fundamental_snapshot_repository import FundamentalSnapshotRepository
from app.repositories.market_repository import SymbolRepository

# scanner_name values that carry a StockCandidate snapshot (fundamental
# fields, setup_state, ...) — breakout_v1 is a different shape and has no
# fundamental component at all. Mirrors CandidateService's own set (see
# app.services.candidate_service) without importing across that layer.
CANDIDATE_SCANNER_NAMES = {"fno_momentum_v1", "pre_breakout_v1", "ipo_intraday_v1"}

# Priority tiers per the Phase 7.x spec: BREAKOUT_CONFIRMED > MOMENTUM >
# PRE_BREAKOUT > WATCH-like (rejected but still has a real setup) > the
# rest. In practice every persisted candidate row already has a non-None
# setup_state (CandidateScannerBase.validate() rejects anything without
# one before a row is ever written), so tier 4 below is what "WATCH /
# remaining candidates" maps onto for this data model.
_SETUP_STATE_TIER = {
    "BREAKOUT_CONFIRMED": 0,
    "MOMENTUM": 1,
    "PRE_BREAKOUT": 2,
}
_QUALIFIED_TIER_CAP = 2
_REJECTED_TIER = 3
_FALLBACK_TIER = 4

# Arbitrary, stable Postgres advisory-lock key — must never change (two
# processes only coordinate if they use the same key). Guards against the
# exact incident that motivated this: an accidental second process/
# container running its own copy of this queue against the same Trendlyne
# account, doubling real request pressure with no cross-process
# awareness (APScheduler's max_instances=1 only prevents overlap *within*
# one process — see app/scheduler/service.py).
_ADVISORY_LOCK_KEY = 881_721_100


def _coerce_float(value: object, default: float = 0.0) -> float:
    """`feature_snapshot` is a JSON dict[str, object] — values come back
    untyped. Never trust a stored technical_score to already be a float."""
    if value is None:
        return default
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


class FundamentalQueueService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        fundamental_provider: FundamentalDataProvider,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._fundamental_provider = fundamental_provider
        self._fundamental_scorer = FundamentalScorer(settings)

    async def run_queue(self) -> QueueRunResult:
        """Processes pending candidates in configurable batches until the
        backlog is exhausted, the run/day budget runs out, or Trendlyne
        reports a rate limit (in which case the queue stops immediately —
        no retry storm, see module docstring)."""
        result = QueueRunResult()
        settings = self._settings

        async with self._session_factory() as session:
            if not await self._acquire_run_lock(session):
                logger.info("FUNDAMENTAL_QUEUE skipped=true reason=another_worker_running")
                return result
            try:
                log_repo = FundamentalFetchLogRepository(session)

                paused_until = await self._paused_until(log_repo)
                if paused_until is not None:
                    logger.info(
                        "FUNDAMENTAL_QUEUE paused=true reason=cooldown resume_at={resume_at}",
                        resume_at=paused_until.isoformat(),
                    )
                    return result

                requests_today = await log_repo.count_today()
                remaining_today = settings.fundamental_max_requests_per_day - requests_today
                if remaining_today <= 0:
                    logger.info(
                        "FUNDAMENTAL_QUEUE daily_budget_exhausted=true requests_today={n} limit={limit}",
                        n=requests_today,
                        limit=settings.fundamental_max_requests_per_day,
                    )
                    result.budget_exhausted = True
                    return result

                run_budget = min(settings.fundamental_max_requests_per_run, remaining_today)

                pending = await self._select_pending(session)
                if not pending:
                    return result
                pending = pending[:run_budget]

                symbol_by_id = await self._resolve_symbols(
                    session, {row.symbol_id for row in pending}
                )

                batch_size = max(1, settings.fundamental_batch_size)
                for batch_start in range(0, len(pending), batch_size):
                    batch = pending[batch_start : batch_start + batch_size]
                    batch_num = batch_start // batch_size + 1
                    logger.info(
                        "FUNDAMENTAL_QUEUE batch={batch} size={size} started",
                        batch=batch_num,
                        size=len(batch),
                    )

                    for i, row in enumerate(batch):
                        symbol = symbol_by_id.get(row.symbol_id)
                        if symbol is None:
                            continue

                        status = await self._process_one(session, log_repo, row, symbol, result)
                        result.processed += 1
                        result.symbols.append(symbol)

                        if status == FetchStatus.RATE_LIMITED:
                            logger.warning(
                                "FUNDAMENTAL_RATE_LIMITED queue_paused=true batch={batch}",
                                batch=batch_num,
                            )
                            result.rate_limited = True
                            await session.commit()
                            return result

                        if i < len(batch) - 1:
                            await asyncio.sleep(settings.fundamental_request_delay_seconds)

                    logger.info("FUNDAMENTAL_QUEUE batch={batch} completed", batch=batch_num)
                    await session.commit()

                    is_last_batch = batch_start + batch_size >= len(pending)
                    if not is_last_batch:
                        logger.info(
                            "FUNDAMENTAL_QUEUE waiting={seconds}s",
                            seconds=settings.fundamental_batch_delay_seconds,
                        )
                        await asyncio.sleep(settings.fundamental_batch_delay_seconds)
            finally:
                await self._release_run_lock(session)

        return result

    async def get_status_summary(self) -> QueueStatusSummary:
        """/health and dashboard queue-status snapshot. Never includes the
        Trendlyne MCP URL/token — only counts and timestamps."""
        async with self._session_factory() as session:
            log_repo = FundamentalFetchLogRepository(session)
            pending_rows = await self._select_pending(session)
            paused_until = await self._paused_until(log_repo)
            requests_today = await log_repo.count_today()

            trendlyne_status = "NOT_CONFIGURED"
            if isinstance(self._fundamental_provider, StatusAwareFundamentalProvider):
                trendlyne_status = "RATE_LIMITED" if paused_until is not None else "HEALTHY"

            return QueueStatusSummary(
                pending=len(pending_rows),
                completed_today=await log_repo.count_today(status=FetchStatus.SUCCESS),
                rate_limited_today=await log_repo.count_today(status=FetchStatus.RATE_LIMITED),
                failed_today=await log_repo.count_today(status=FetchStatus.FAILED),
                cached_today=await log_repo.count_today(status=FetchStatus.CACHED),
                batch_size=self._settings.fundamental_batch_size,
                is_paused=paused_until is not None,
                paused_until=paused_until,
                last_request_at=await log_repo.last_requested_at(),
                requests_today=requests_today,
                daily_budget=self._settings.fundamental_max_requests_per_day,
                trendlyne_status=trendlyne_status,
            )

    # --- internals -----------------------------------------------------

    async def _paused_until(self, log_repo: FundamentalFetchLogRepository) -> datetime | None:
        rate_limited_at = await log_repo.most_recent_rate_limit()
        if rate_limited_at is None:
            return None
        consecutive = await log_repo.count_consecutive_rate_limits()
        cooldown_seconds = self._escalated_cooldown_seconds(consecutive)
        resume_at = rate_limited_at + timedelta(seconds=cooldown_seconds)
        now = datetime.now(UTC)
        return resume_at if now < resume_at else None

    def _escalated_cooldown_seconds(self, consecutive_rate_limits: int) -> float:
        """Trendlyne's rate-limit error carries no reset-time info (see
        trendlyne_mcp_client.py), so rather than guess the actual reset
        window, each consecutive rate-limited attempt (no success in
        between) waits longer than the last — cooldown * multiplier^n,
        capped at fundamental_rate_limit_max_cooldown_seconds. This is
        what stops the queue from retrying at a fixed interval forever
        while an account-level quota stays exhausted, without ever
        fabricating a specific reset time."""
        settings = self._settings
        exponent = max(0, consecutive_rate_limits - 1)
        cooldown = settings.fundamental_rate_limit_cooldown_seconds * (
            settings.fundamental_rate_limit_backoff_multiplier**exponent
        )
        return min(cooldown, settings.fundamental_rate_limit_max_cooldown_seconds)

    async def _acquire_run_lock(self, session: AsyncSession) -> bool:
        """Postgres-only advisory lock so two separate processes/
        containers can never run the queue concurrently and double up on
        Trendlyne calls — a no-op (always "acquired") on SQLite, which
        the test suite uses and which has no advisory-lock primitive."""
        bind = session.bind
        if bind is None or bind.dialect.name != "postgresql":
            return True
        acquired = await session.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": _ADVISORY_LOCK_KEY}
        )
        return bool(acquired.scalar_one())

    async def _release_run_lock(self, session: AsyncSession) -> None:
        bind = session.bind
        if bind is None or bind.dialect.name != "postgresql":
            return
        await session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _ADVISORY_LOCK_KEY})

    async def _select_pending(self, session: AsyncSession) -> list[ScannerResult]:
        stmt = select(ScannerResult).where(ScannerResult.scanner_name.in_(CANDIDATE_SCANNER_NAMES))
        rows = list((await session.execute(stmt)).scalars().all())

        ttl = timedelta(minutes=self._settings.fundamental_cache_ttl_minutes)
        now = datetime.now(UTC)

        pending: list[ScannerResult] = []
        for row in rows:
            snapshot = row.feature_snapshot
            fund_status = snapshot.get("fundamental_status")
            if fund_status == FetchStatus.SUCCESS.value:
                fetched_at_raw = snapshot.get("fundamental_fetched_at")
                if isinstance(fetched_at_raw, str):
                    try:
                        fetched_at = datetime.fromisoformat(fetched_at_raw)
                    except ValueError:
                        fetched_at = None
                    if fetched_at is not None and now - fetched_at < ttl:
                        continue  # still fresh — not pending, don't re-fetch
            pending.append(row)

        pending.sort(key=self._priority_key)
        return pending

    def _priority_key(self, row: ScannerResult) -> tuple[int, float]:
        snapshot = row.feature_snapshot
        setup_state = snapshot.get("setup_state")
        tier = _SETUP_STATE_TIER.get(str(setup_state), _FALLBACK_TIER)
        if tier > _QUALIFIED_TIER_CAP:
            tier = _FALLBACK_TIER if row.status != "rejected" else _REJECTED_TIER
        score = _coerce_float(snapshot.get("technical_score"))
        return (tier, -score)

    async def _resolve_symbols(self, session: AsyncSession, symbol_ids: set[int]) -> dict[int, str]:
        symbols = await SymbolRepository(session).list_by_ids(list(symbol_ids))
        return {s.id: s.symbol for s in symbols}

    async def _process_one(
        self,
        session: AsyncSession,
        log_repo: FundamentalFetchLogRepository,
        row: ScannerResult,
        symbol: str,
        result: QueueRunResult,
    ) -> FetchStatus:
        try:
            if isinstance(self._fundamental_provider, StatusAwareFundamentalProvider):
                data, status, error = await self._fundamental_provider.get_fundamentals_with_status(
                    symbol
                )
            else:
                data = await self._fundamental_provider.get_fundamentals(symbol)
                status = FetchStatus.SUCCESS if data is not None else FetchStatus.FAILED
                error = None
        except Exception as exc:  # noqa: BLE001 - one symbol's failure must never break the queue
            logger.warning(
                "FUNDAMENTAL_FETCH symbol={symbol} status=failed error={error}",
                symbol=symbol,
                error=exc,
            )
            data, status, error = None, FetchStatus.FAILED, str(exc)[:500]

        await log_repo.record(symbol_id=row.symbol_id, status=status, error_message=error)
        await FundamentalSnapshotRepository(session, self._settings).upsert(
            row.symbol_id,
            data=data,
            source=self._fundamental_provider.name,
            status=status,
            error_message=error,
        )
        logger.info(
            "FUNDAMENTAL_FETCH symbol={symbol} status={status}", symbol=symbol, status=status.value
        )

        if status == FetchStatus.SUCCESS:
            result.succeeded += 1
        elif status == FetchStatus.CACHED:
            result.cached += 1
        elif status == FetchStatus.FAILED:
            result.failed += 1

        if data is not None:
            await self._apply_result(row, data, status)
        elif status in (FetchStatus.FAILED, FetchStatus.RATE_LIMITED):
            snapshot = dict(row.feature_snapshot)
            snapshot["fundamental_status"] = status.value
            row.feature_snapshot = snapshot

        await session.flush()
        return status

    async def _apply_result(
        self, row: ScannerResult, data: FundamentalData, status: FetchStatus
    ) -> None:
        """Updates the persisted scanner_results row with freshly-fetched
        fundamental data — mirrors the scoring build_candidate() already
        does, just as a targeted update instead of a fresh build. Never
        fabricates: any field the source didn't report stays absent, and
        FundamentalScorer treats that as UNKNOWN, never zero."""
        fundamental_result = self._fundamental_scorer.score(data)

        snapshot = dict(row.feature_snapshot)
        technical_score = _coerce_float(snapshot.get("technical_score"))
        overall_score = combine_scores(fundamental_result.score, technical_score, self._settings)
        overall_score = round(min(max(overall_score, 0.0), 100.0), 2)

        snapshot.update(
            {
                "fundamental_score": fundamental_result.score,
                "overall_score": overall_score,
                "quality": quality_from_score(overall_score).value,
                "fundamental_reasons": list(fundamental_result.positive_reasons),
                "fundamental_factors": [factor_to_dict(f) for f in fundamental_result.factors],
                "fundamental_field_sources": [
                    field_snapshot_to_dict(s) for s in data.field_snapshots.values()
                ],
                "risk_flags": list(fundamental_result.risk_flags),
                "data_completeness_pct": fundamental_result.data_completeness_pct,
                "fundamental_status": status.value,
                "fundamental_fetched_at": datetime.now(UTC).isoformat(),
            }
        )
        row.feature_snapshot = snapshot
        row.score = Decimal(str(overall_score))
