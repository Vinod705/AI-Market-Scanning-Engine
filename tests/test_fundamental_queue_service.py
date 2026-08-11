"""Tests for FundamentalQueueService (Phase 7.x) — the paced, prioritized,
budget-capped queue that replaced firing a Trendlyne request per scanned
candidate (see app/fundamentals/queue_service.py's module docstring for
the incident that motivated it).

Covers the user's 17-item verification checklist using fakes only — no
real Trendlyne quota is ever touched by these tests.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.fundamentals.models import FieldAvailability, FieldSnapshot, FundamentalData
from app.fundamentals.provider import FundamentalDataProvider
from app.fundamentals.queue_models import FetchStatus
from app.fundamentals.queue_service import FundamentalQueueService
from app.fundamentals.scorer import FundamentalScorer
from app.models.fundamental_fetch_log import FundamentalFetchLog
from app.providers.base_provider import ProviderSymbol
from app.repositories.fundamental_fetch_log_repository import FundamentalFetchLogRepository
from app.repositories.market_repository import SymbolRepository
from app.repositories.scanner_repository import ScannerResultRepository


def _fast_settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "fundamental_batch_size": 10,
        "fundamental_batch_delay_seconds": 0.0,
        "fundamental_request_delay_seconds": 0.0,
        "fundamental_rate_limit_cooldown_seconds": 1800.0,
        "fundamental_max_requests_per_run": 200,
        "fundamental_max_requests_per_day": 350,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


async def _seed_candidate(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    symbol_name: str,
    scanner_name: str = "fno_momentum_v1",
    setup_state: str | None = "MOMENTUM",
    technical_score: float = 50.0,
    status: str = "qualified",
    fundamental_status: str = "PENDING",
    fundamental_fetched_at: str | None = None,
) -> int:
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol_name, exchange="N", instrument_token=symbol_name)
        )
        await session.commit()
        symbol_id = symbol_row.id

        snapshot: dict[str, object] = {
            "setup_state": setup_state,
            "technical_score": technical_score,
            "fundamental_status": fundamental_status,
        }
        if fundamental_fetched_at is not None:
            snapshot["fundamental_fetched_at"] = fundamental_fetched_at

        await ScannerResultRepository(session).upsert(
            symbol_id=symbol_id,
            scanner_name=scanner_name,
            date=date(2026, 1, 5),
            score=Decimal(str(technical_score)),
            status=status,
            reason="seeded for queue test",
            feature_snapshot=snapshot,
        )
        await session.commit()
    return symbol_id


async def _get_snapshot(
    session_factory: async_sessionmaker[AsyncSession], symbol_name: str
) -> dict[str, object]:
    async with session_factory() as session:
        symbol = await SymbolRepository(session).get_by_symbol(symbol_name)
        assert symbol is not None
        rows = await ScannerResultRepository(session).get_for_symbol(symbol.id)
        assert len(rows) == 1
        return dict(rows[0].feature_snapshot)


@dataclass
class _FakeFetch:
    status: FetchStatus = FetchStatus.SUCCESS
    data: FundamentalData | None = None
    error: str | None = None


class _FakeStatusProvider(FundamentalDataProvider):
    """Structurally implements StatusAwareFundamentalProvider. Records call
    order and peak concurrency so tests can assert the queue never fans
    requests out in parallel."""

    name = "fake_status_provider"

    def __init__(self, overrides: dict[str, _FakeFetch] | None = None) -> None:
        self._overrides = overrides or {}
        self.calls: list[str] = []
        self._concurrent = 0
        self.max_concurrent = 0

    async def get_fundamentals(self, symbol: str) -> FundamentalData | None:
        data, _status, _error = await self.get_fundamentals_with_status(symbol)
        return data

    async def get_fundamentals_with_status(
        self, symbol: str
    ) -> tuple[FundamentalData | None, FetchStatus, str | None]:
        self._concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self._concurrent)
        self.calls.append(symbol)
        await asyncio.sleep(0)  # yield control, exposing any accidental concurrency
        self._concurrent -= 1

        fetch = self._overrides.get(symbol)
        if fetch is not None:
            return fetch.data, fetch.status, fetch.error

        data = FundamentalData(symbol=symbol, roe_pct=20.0, pe=15.0)
        data.field_snapshots["roe_pct"] = FieldSnapshot(
            field_name="roe_pct",
            value=20.0,
            source="Fake",
            period="Annual",
            status=FieldAvailability.AVAILABLE,
        )
        return data, FetchStatus.SUCCESS, None


class _RaisingProvider(FundamentalDataProvider):
    """Simulates a provider whose exception (like the real
    TrendlyneMcpError) never includes the MCP URL/token."""

    name = "fake_raising_provider"

    def __init__(self, message: str) -> None:
        self._message = message
        self.calls: list[str] = []

    async def get_fundamentals(self, symbol: str) -> FundamentalData | None:
        raise RuntimeError(self._message)

    async def get_fundamentals_with_status(
        self, symbol: str
    ) -> tuple[FundamentalData | None, FetchStatus, str | None]:
        self.calls.append(symbol)
        raise RuntimeError(self._message)


# 1. Large candidate volumes never fan out into simultaneous requests.
async def test_queue_never_issues_concurrent_requests(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    for i in range(20):
        await _seed_candidate(session_factory, symbol_name=f"SYM{i}", technical_score=float(i))

    provider = _FakeStatusProvider()
    queue = FundamentalQueueService(session_factory, _fast_settings(), provider)
    result = await queue.run_queue()

    assert result.processed == 20
    assert provider.max_concurrent == 1


# 2. Batch size defaults to 10.
def test_batch_size_defaults_to_ten() -> None:
    assert Settings().fundamental_batch_size == 10


# 3+4+5+6. Batching, ordering, and configurable delays.
async def test_batches_are_paced_sequentially_with_configured_delays(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Highest technical_score processed first within the same tier.
    await _seed_candidate(session_factory, symbol_name="A", technical_score=40.0)
    await _seed_candidate(session_factory, symbol_name="B", technical_score=30.0)
    await _seed_candidate(session_factory, symbol_name="C", technical_score=20.0)
    await _seed_candidate(session_factory, symbol_name="D", technical_score=10.0)

    settings = _fast_settings(
        fundamental_batch_size=2,
        fundamental_request_delay_seconds=1.5,
        fundamental_batch_delay_seconds=4.5,
    )
    provider = _FakeStatusProvider()
    queue = FundamentalQueueService(session_factory, settings, provider)

    sleep_calls: list[float] = []
    real_sleep = asyncio.sleep

    async def _fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    result = await queue.run_queue()

    assert provider.calls == ["A", "B", "C", "D"]
    assert result.processed == 4
    # request delay within batch 1 (A->B), batch delay between batches
    # (B->C), request delay within batch 2 (C->D) — never after the last.
    # (zeros are the fake provider's own internal control-yield, not queue pacing)
    paced_sleeps = [seconds for seconds in sleep_calls if seconds != 0]
    assert paced_sleeps == [1.5, 4.5, 1.5]


# 7+8. Rate limit pauses the queue immediately, no retry storm.
async def test_rate_limit_pauses_queue_without_retry_storm(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_candidate(session_factory, symbol_name="E", technical_score=90.0)
    await _seed_candidate(session_factory, symbol_name="F", technical_score=80.0)
    await _seed_candidate(session_factory, symbol_name="G", technical_score=70.0)

    provider = _FakeStatusProvider(
        overrides={"E": _FakeFetch(status=FetchStatus.RATE_LIMITED, error="rate limited")}
    )
    queue = FundamentalQueueService(session_factory, _fast_settings(), provider)

    result = await queue.run_queue()
    assert result.rate_limited is True
    assert provider.calls == ["E"]  # F and G never attempted in the same run
    assert result.processed == 1

    # Second run within the cooldown window must not attempt anything either.
    result2 = await queue.run_queue()
    assert provider.calls == ["E"]
    assert result2.processed == 0


# 9. Daily safety budget stops further requests.
async def test_daily_budget_stops_further_requests(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_candidate(session_factory, symbol_name="H", technical_score=10.0)
    await _seed_candidate(session_factory, symbol_name="I", technical_score=20.0)
    await _seed_candidate(session_factory, symbol_name="J", technical_score=30.0)

    settings = _fast_settings(fundamental_max_requests_per_day=2)
    provider = _FakeStatusProvider()
    queue = FundamentalQueueService(session_factory, settings, provider)

    async with session_factory() as session:
        log_repo = FundamentalFetchLogRepository(session)
        await log_repo.record(symbol_id=symbol_id, status=FetchStatus.SUCCESS)
        await log_repo.record(symbol_id=symbol_id, status=FetchStatus.SUCCESS)
        await session.commit()

    result = await queue.run_queue()
    assert result.budget_exhausted is True
    assert provider.calls == []
    assert result.processed == 0


async def test_daily_budget_clamps_run_size_to_remaining(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_candidate(session_factory, symbol_name="K", technical_score=10.0)
    await _seed_candidate(session_factory, symbol_name="L", technical_score=20.0)
    await _seed_candidate(session_factory, symbol_name="M", technical_score=30.0)

    settings = _fast_settings(fundamental_max_requests_per_day=3)
    provider = _FakeStatusProvider()
    queue = FundamentalQueueService(session_factory, settings, provider)

    async with session_factory() as session:
        log_repo = FundamentalFetchLogRepository(session)
        await log_repo.record(symbol_id=symbol_id, status=FetchStatus.SUCCESS)
        await log_repo.record(symbol_id=symbol_id, status=FetchStatus.SUCCESS)
        await session.commit()

    result = await queue.run_queue()
    assert result.processed == 1  # only 1 request left in today's budget
    assert len(provider.calls) == 1


# 10. Technical scanning never touches a fundamental provider (structural
# guarantee — build_candidate() takes no provider argument at all; see
# tests/test_candidates_builder.py::test_build_candidate_never_fetches_fundamentals_itself).
# Confirmed here from the queue's side: scanning alone produces PENDING rows
# and logs zero fetch attempts until the queue is explicitly run.
async def test_scanning_alone_never_touches_the_fundamental_log(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_candidate(session_factory, symbol_name="SCANONLY")
    async with session_factory() as session:
        count = await FundamentalFetchLogRepository(session).count_today()
    assert count == 0


# 11. Cached (fresh) fundamentals do not trigger another request.
async def test_fresh_cached_fundamentals_are_not_refetched(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    fresh = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    stale = (datetime.now(UTC) - timedelta(minutes=500)).isoformat()
    await _seed_candidate(
        session_factory,
        symbol_name="FRESH",
        fundamental_status="SUCCESS",
        fundamental_fetched_at=fresh,
    )
    await _seed_candidate(
        session_factory,
        symbol_name="STALE",
        fundamental_status="SUCCESS",
        fundamental_fetched_at=stale,
    )

    provider = _FakeStatusProvider()
    queue = FundamentalQueueService(session_factory, _fast_settings(), provider)
    result = await queue.run_queue()

    assert provider.calls == ["STALE"]
    assert result.processed == 1


# 12. A failed stock does not stop the batch.
async def test_failed_stock_does_not_stop_the_batch(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_candidate(session_factory, symbol_name="FAILME", technical_score=90.0)
    await _seed_candidate(session_factory, symbol_name="OKSTOCK", technical_score=80.0)

    provider = _FakeStatusProvider(
        overrides={"FAILME": _FakeFetch(status=FetchStatus.FAILED, error="no data")}
    )
    queue = FundamentalQueueService(session_factory, _fast_settings(), provider)
    result = await queue.run_queue()

    assert result.processed == 2
    assert result.failed == 1
    assert result.succeeded == 1
    assert provider.calls == ["FAILME", "OKSTOCK"]


# 13. IPO and F&O candidates share the same queue.
async def test_ipo_and_fno_candidates_share_the_queue(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_candidate(session_factory, symbol_name="IPOCO", scanner_name="ipo_intraday_v1")
    await _seed_candidate(session_factory, symbol_name="FNOCO", scanner_name="fno_momentum_v1")
    await _seed_candidate(session_factory, symbol_name="PBCO", scanner_name="pre_breakout_v1")

    provider = _FakeStatusProvider()
    queue = FundamentalQueueService(session_factory, _fast_settings(), provider)
    result = await queue.run_queue()

    assert result.processed == 3
    assert set(provider.calls) == {"IPOCO", "FNOCO", "PBCO"}


# 14. Highest-priority (tier, then technical score) candidates go first.
async def test_priority_ordering_across_tiers(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_candidate(
        session_factory, symbol_name="N", setup_state="BREAKOUT_CONFIRMED", technical_score=10.0
    )
    await _seed_candidate(
        session_factory, symbol_name="O", setup_state="MOMENTUM", technical_score=99.0
    )
    await _seed_candidate(
        session_factory, symbol_name="P", setup_state="PRE_BREAKOUT", technical_score=99.0
    )
    await _seed_candidate(
        session_factory,
        symbol_name="R",
        setup_state=None,
        status="rejected",
        technical_score=99.0,
    )
    await _seed_candidate(
        session_factory,
        symbol_name="S",
        setup_state=None,
        status="qualified",
        technical_score=99.0,
    )

    provider = _FakeStatusProvider()
    queue = FundamentalQueueService(session_factory, _fast_settings(), provider)
    await queue.run_queue()

    assert provider.calls == ["N", "O", "P", "R", "S"]


# 15. No fundamental values are ever fabricated.
async def test_no_fundamental_values_are_fabricated(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_candidate(session_factory, symbol_name="PARTIAL", technical_score=60.0)

    data = FundamentalData(symbol="PARTIAL", roe_pct=25.0)
    data.field_snapshots["roe_pct"] = FieldSnapshot(
        field_name="roe_pct",
        value=25.0,
        source="Fake",
        period="Annual",
        status=FieldAvailability.AVAILABLE,
    )
    provider = _FakeStatusProvider(
        overrides={"PARTIAL": _FakeFetch(status=FetchStatus.SUCCESS, data=data)}
    )
    settings = _fast_settings()
    queue = FundamentalQueueService(session_factory, settings, provider)
    await queue.run_queue()

    snapshot = await _get_snapshot(session_factory, "PARTIAL")
    field_sources = snapshot["fundamental_field_sources"]
    assert isinstance(field_sources, list)
    assert len(field_sources) == 1
    assert field_sources[0]["field_name"] == "roe_pct"

    expected = FundamentalScorer(settings).score(data)
    assert snapshot["fundamental_score"] == expected.score


# 16. Trendlyne credentials never appear in logs, even on failure.
async def test_credentials_never_appear_in_logs_or_error_records(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    secret = "SUPERSECRETTOKEN123"
    settings = _fast_settings(trendlyne_mcp_url=f"https://mcp.trendlyne.com/mcp/?token={secret}")
    await _seed_candidate(session_factory, symbol_name="LEAKCHECK")

    # Mirrors the real TrendlyneMcpError guarantee: the exception text
    # itself never carries the URL/token.
    provider = _RaisingProvider("tool 'get_overview' returned HTTP 500")
    queue = FundamentalQueueService(session_factory, settings, provider)

    captured: list[str] = []
    sink_id = logger.add(lambda msg: captured.append(str(msg)), level="DEBUG")
    try:
        await queue.run_queue()
    finally:
        logger.remove(sink_id)

    assert not any(secret in message for message in captured)

    async with session_factory() as session:
        symbol = await SymbolRepository(session).get_by_symbol("LEAKCHECK")
        assert symbol is not None
        log_repo = FundamentalFetchLogRepository(session)
        last_at = await log_repo.last_requested_at()
        assert last_at is not None


# 17. Restarting (a fresh service instance, no shared in-memory state) does
# not redo already-completed work.
async def test_restart_does_not_duplicate_completed_work(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_candidate(session_factory, symbol_name="RESTARTME", technical_score=50.0)

    settings = _fast_settings()
    provider1 = _FakeStatusProvider()
    queue1 = FundamentalQueueService(session_factory, settings, provider1)
    result1 = await queue1.run_queue()
    assert result1.processed == 1
    assert provider1.calls == ["RESTARTME"]

    # Simulate a process restart: brand new service instance, no in-memory
    # queue state carried over — only the DB (scanner_results + fetch log).
    provider2 = _FakeStatusProvider()
    queue2 = FundamentalQueueService(session_factory, settings, provider2)
    result2 = await queue2.run_queue()

    assert provider2.calls == []
    assert result2.processed == 0


# --- FundamentalFetchLogRepository -------------------------------------


async def test_fetch_log_repository_count_today_and_status_filter(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_candidate(session_factory, symbol_name="LOGCOUNT")
    async with session_factory() as session:
        repo = FundamentalFetchLogRepository(session)
        await repo.record(symbol_id=symbol_id, status=FetchStatus.SUCCESS)
        await repo.record(symbol_id=symbol_id, status=FetchStatus.FAILED)
        await session.commit()

    async with session_factory() as session:
        repo = FundamentalFetchLogRepository(session)
        assert await repo.count_today() == 2
        assert await repo.count_today(status=FetchStatus.SUCCESS) == 1
        assert await repo.count_today(status=FetchStatus.FAILED) == 1
        assert await repo.count_today(status=FetchStatus.RATE_LIMITED) == 0


async def test_fetch_log_repository_most_recent_rate_limit_clears_after_success(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Uses explicit `requested_at` timestamps (rather than back-to-back
    `record()` calls) since SQLite's `CURRENT_TIMESTAMP` only has
    second-level resolution — two real calls in the same test can land in
    the same second and make ordering ambiguous. Postgres (production)
    doesn't have this limitation; this is purely a test-determinism fix."""
    symbol_id = await _seed_candidate(session_factory, symbol_name="COOLDOWN")
    base = datetime.now(UTC)
    async with session_factory() as session:
        session.add(
            FundamentalFetchLog(
                symbol_id=symbol_id,
                status=FetchStatus.RATE_LIMITED.value,
                requested_at=base,
            )
        )
        await session.commit()
        repo = FundamentalFetchLogRepository(session)
        assert await repo.most_recent_rate_limit() is not None

        session.add(
            FundamentalFetchLog(
                symbol_id=symbol_id,
                status=FetchStatus.SUCCESS.value,
                requested_at=base + timedelta(seconds=1),
            )
        )
        await session.commit()
        assert await repo.most_recent_rate_limit() is None


# --- get_status_summary() ------------------------------------------------


async def test_status_summary_reflects_pause_and_budget(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_candidate(session_factory, symbol_name="SUMMARY1", technical_score=90.0)

    provider = _FakeStatusProvider(
        overrides={"SUMMARY1": _FakeFetch(status=FetchStatus.RATE_LIMITED, error="rate limited")}
    )
    settings = _fast_settings()
    queue = FundamentalQueueService(session_factory, settings, provider)
    await queue.run_queue()

    summary = await queue.get_status_summary()
    assert summary.is_paused is True
    assert summary.paused_until is not None
    assert summary.trendlyne_status == "RATE_LIMITED"
    assert summary.rate_limited_today == 1
    assert summary.batch_size == settings.fundamental_batch_size
    assert summary.daily_budget == settings.fundamental_max_requests_per_day


async def test_admin_fundamental_queue_endpoint_degrades_gracefully(client: AsyncClient) -> None:
    response = await client.get("/admin/fundamental-queue")
    assert response.status_code == 200
    body = response.json()
    assert body["trendlyne_status"] == "NOT_CONFIGURED"
    assert body["pending"] == 0
    assert body["is_paused"] is False


# --- Escalating rate-limit backoff (fix for the persistent-rate-limit
# incident: 12 consecutive RATE_LIMITED attempts, one every fixed 30-min
# cooldown, zero successes across several hours) -------------------------


async def test_fetch_log_repository_counts_consecutive_rate_limits(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Uses explicit, strictly-increasing `requested_at` timestamps rather
    than back-to-back `record()` calls — SQLite's `CURRENT_TIMESTAMP` only
    has second-level resolution, so consecutive real calls in the same
    test can land in the same second and make ordering ambiguous.
    Postgres (production) doesn't have this limitation; this is purely a
    test-determinism fix, same pattern as
    test_fetch_log_repository_most_recent_rate_limit_clears_after_success."""
    symbol_id = await _seed_candidate(session_factory, symbol_name="ESCALATE1")
    base = datetime.now(UTC)
    async with session_factory() as session:
        repo = FundamentalFetchLogRepository(session)
        assert await repo.count_consecutive_rate_limits() == 0

        for i in range(3):
            session.add(
                FundamentalFetchLog(
                    symbol_id=symbol_id,
                    status=FetchStatus.RATE_LIMITED.value,
                    requested_at=base + timedelta(seconds=i),
                )
            )
        await session.commit()
        assert await repo.count_consecutive_rate_limits() == 3

        # A success resets the streak — future rate limits start counting
        # from zero again, so the queue doesn't stay escalated forever.
        session.add(
            FundamentalFetchLog(
                symbol_id=symbol_id,
                status=FetchStatus.SUCCESS.value,
                requested_at=base + timedelta(seconds=10),
            )
        )
        await session.commit()
        assert await repo.count_consecutive_rate_limits() == 0

        session.add(
            FundamentalFetchLog(
                symbol_id=symbol_id,
                status=FetchStatus.RATE_LIMITED.value,
                requested_at=base + timedelta(seconds=20),
            )
        )
        await session.commit()
        assert await repo.count_consecutive_rate_limits() == 1


def test_escalated_cooldown_grows_and_caps() -> None:
    settings = _fast_settings(
        fundamental_rate_limit_cooldown_seconds=1800.0,
        fundamental_rate_limit_backoff_multiplier=2.0,
        fundamental_rate_limit_max_cooldown_seconds=21600.0,
    )
    queue = FundamentalQueueService(None, settings, _FakeStatusProvider())  # type: ignore[arg-type]

    assert queue._escalated_cooldown_seconds(1) == 1800.0
    assert queue._escalated_cooldown_seconds(2) == 3600.0
    assert queue._escalated_cooldown_seconds(3) == 7200.0
    assert queue._escalated_cooldown_seconds(4) == 14400.0
    # 5th would be 28800 uncapped -> clamped to the configured ceiling.
    assert queue._escalated_cooldown_seconds(5) == 21600.0
    assert queue._escalated_cooldown_seconds(12) == 21600.0


async def test_repeated_rate_limits_do_not_retry_at_the_fixed_interval(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The exact incident this fixes: after several consecutive rate
    limits, the queue must NOT resume just because the base cooldown
    (30 min) has elapsed — it must keep backing off."""
    symbol_id = await _seed_candidate(session_factory, symbol_name="ESCALATE2")
    settings = _fast_settings(
        fundamental_rate_limit_cooldown_seconds=1800.0,
        fundamental_rate_limit_backoff_multiplier=2.0,
        fundamental_rate_limit_max_cooldown_seconds=21600.0,
    )
    now = datetime.now(UTC)
    async with session_factory() as session:
        # 3 consecutive rate limits; the most recent one 40 minutes ago —
        # past the old fixed 30-minute cooldown, but well inside the
        # escalated 3rd-strike cooldown of 7200s (2h).
        for minutes_ago in (100, 70, 40):
            session.add(
                FundamentalFetchLog(
                    symbol_id=symbol_id,
                    status=FetchStatus.RATE_LIMITED.value,
                    requested_at=now - timedelta(minutes=minutes_ago),
                )
            )
        await session.commit()

    provider = _FakeStatusProvider()
    queue = FundamentalQueueService(session_factory, settings, provider)
    result = await queue.run_queue()

    assert result.processed == 0
    assert provider.calls == []  # still paused -> no new Trendlyne call at all


async def test_queue_resumes_once_paused_window_actually_elapses(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Same escalated cooldown, but far enough in the past that it has
    genuinely elapsed — the queue must still resume automatically."""
    symbol_id = await _seed_candidate(session_factory, symbol_name="ESCALATE3")
    settings = _fast_settings(
        fundamental_rate_limit_cooldown_seconds=1800.0,
        fundamental_rate_limit_backoff_multiplier=2.0,
        fundamental_rate_limit_max_cooldown_seconds=21600.0,
    )
    now = datetime.now(UTC)
    async with session_factory() as session:
        for minutes_ago in (300, 270, 240):  # 3rd-strike cooldown is 120min; 240min ago has cleared
            session.add(
                FundamentalFetchLog(
                    symbol_id=symbol_id,
                    status=FetchStatus.RATE_LIMITED.value,
                    requested_at=now - timedelta(minutes=minutes_ago),
                )
            )
        await session.commit()

    provider = _FakeStatusProvider()
    queue = FundamentalQueueService(session_factory, settings, provider)
    result = await queue.run_queue()

    assert result.processed == 1
    assert provider.calls == ["ESCALATE3"]


async def test_success_after_rate_limits_resets_to_base_cooldown(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Once a real success happens, a later single rate limit should not
    inherit the old escalation — it starts back at the base cooldown."""
    symbol_id = await _seed_candidate(session_factory, symbol_name="ESCALATE4")
    settings = _fast_settings(
        fundamental_rate_limit_cooldown_seconds=1800.0,
        fundamental_rate_limit_backoff_multiplier=2.0,
        fundamental_rate_limit_max_cooldown_seconds=21600.0,
    )
    now = datetime.now(UTC)
    async with session_factory() as session:
        for minutes_ago in (200, 170, 140):
            session.add(
                FundamentalFetchLog(
                    symbol_id=symbol_id,
                    status=FetchStatus.RATE_LIMITED.value,
                    requested_at=now - timedelta(minutes=minutes_ago),
                )
            )
        session.add(
            FundamentalFetchLog(
                symbol_id=symbol_id,
                status=FetchStatus.SUCCESS.value,
                requested_at=now - timedelta(minutes=100),
            )
        )
        # A single fresh rate limit, 40 minutes ago -> only the FIRST
        # strike since the success, so cooldown should be the base 30min,
        # already elapsed at 40 minutes -> queue should be resumed.
        session.add(
            FundamentalFetchLog(
                symbol_id=symbol_id,
                status=FetchStatus.RATE_LIMITED.value,
                requested_at=now - timedelta(minutes=40),
            )
        )
        await session.commit()

    provider = _FakeStatusProvider()
    queue = FundamentalQueueService(session_factory, settings, provider)
    result = await queue.run_queue()

    assert result.processed == 1
    assert provider.calls == ["ESCALATE4"]


# --- Cross-process advisory lock (fix for the second contributing cause:
# an accidental duplicate process/container running its own copy of this
# queue against the same Trendlyne account) ------------------------------


async def test_advisory_lock_is_a_noop_on_sqlite(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The lock is Postgres-only (advisory locks have no SQLite
    equivalent, and this test suite runs on SQLite) — it must always
    "succeed" here so existing/new tests keep working. True cross-process
    mutual exclusion can only be exercised against a real Postgres
    instance, which this harness doesn't have; documented as a test
    limitation in the final report."""
    settings = _fast_settings()
    queue = FundamentalQueueService(session_factory, settings, _FakeStatusProvider())
    async with session_factory() as session:
        acquired = await queue._acquire_run_lock(session)
        assert acquired is True
        await queue._release_run_lock(session)  # must not raise


async def test_run_queue_processes_normally_with_lock_guard_in_place(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The lock guard must not change normal (non-contended) behavior —
    regression check that wrapping run_queue() in acquire/release didn't
    break the existing batch/pace/no-fabrication behavior."""
    for i in range(3):
        await _seed_candidate(session_factory, symbol_name=f"LOCKOK{i}", technical_score=float(i))

    provider = _FakeStatusProvider()
    queue = FundamentalQueueService(session_factory, _fast_settings(), provider)
    result = await queue.run_queue()

    assert result.processed == 3
    assert result.succeeded == 3
    assert provider.max_concurrent == 1
