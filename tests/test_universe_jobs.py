"""Tests for app.scheduler.universe_jobs job registration and execution.

No prior coverage existed for this file — added while touching its
signature (FnoRootsProvider Protocol, replacing a FivePaisaProvider-only
type hint) so the F&O-roots-matching behavior has a real test, not just
registration.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.providers.base_provider import ProviderError
from app.providers.base_provider import ProviderSymbol as PSymbol
from app.repositories.fno_universe_repository import FnoUniverseRepository
from app.repositories.market_repository import SymbolRepository
from app.scheduler.service import SchedulerService
from app.scheduler.universe_jobs import JOB_ID_FNO_UNIVERSE_REFRESH, register_universe_jobs


class _FakeFnoProvider:
    """A plain duck-typed fake — no MarketDataProvider inheritance at all —
    proving FnoRootsProvider is genuinely structural, not accidentally
    requiring a real provider subclass."""

    def __init__(self, roots: set[str], *, connected: bool = True) -> None:
        self._roots = roots
        self._connected = connected
        self.connect_calls = 0

    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self.connect_calls += 1
        self._connected = True

    async def get_fno_symbol_roots(self) -> set[str]:
        return self._roots


async def _seed_symbols(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        repo = SymbolRepository(session)
        await repo.upsert(PSymbol(symbol="TCS", exchange="N", instrument_token="1"))
        await repo.upsert(PSymbol(symbol="INFY", exchange="N", instrument_token="2"))
        await repo.upsert(PSymbol(symbol="NOTFNO", exchange="N", instrument_token="3"))
        await session.commit()


async def test_register_universe_jobs_adds_expected_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(scheduler_enabled=True, scheduler_timezone="Asia/Kolkata")
    scheduler_service = SchedulerService(settings)

    register_universe_jobs(scheduler_service, _FakeFnoProvider(set()), session_factory)

    job_ids = {job.id for job in scheduler_service.jobs}
    assert JOB_ID_FNO_UNIVERSE_REFRESH in job_ids


async def test_refresh_job_matches_roots_against_active_symbols(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbols(session_factory)
    settings = Settings(scheduler_enabled=True, scheduler_timezone="Asia/Kolkata")
    scheduler_service = SchedulerService(settings)
    provider = _FakeFnoProvider({"TCS", "INFY", "NIFTY"})  # NIFTY has no matching Symbol row

    register_universe_jobs(scheduler_service, provider, session_factory)
    refresh_job = next(job for job in scheduler_service.jobs if job.id == JOB_ID_FNO_UNIVERSE_REFRESH)
    await refresh_job.func()

    async with session_factory() as session:
        symbol_ids = await FnoUniverseRepository(session).list_symbol_ids()
        symbols = await SymbolRepository(session).list_by_ids(symbol_ids)
    assert {s.symbol for s in symbols} == {"TCS", "INFY"}


async def test_refresh_job_connects_when_not_already_connected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbols(session_factory)
    settings = Settings(scheduler_enabled=True, scheduler_timezone="Asia/Kolkata")
    scheduler_service = SchedulerService(settings)
    provider = _FakeFnoProvider({"TCS"}, connected=False)

    register_universe_jobs(scheduler_service, provider, session_factory)
    refresh_job = next(job for job in scheduler_service.jobs if job.id == JOB_ID_FNO_UNIVERSE_REFRESH)
    await refresh_job.func()

    assert provider.connect_calls == 1


async def test_refresh_job_skips_gracefully_on_provider_error(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    class _FailingProvider(_FakeFnoProvider):
        async def get_fno_symbol_roots(self) -> set[str]:
            raise ProviderError("boom")

    settings = Settings(scheduler_enabled=True, scheduler_timezone="Asia/Kolkata")
    scheduler_service = SchedulerService(settings)
    provider = _FailingProvider(set())

    register_universe_jobs(scheduler_service, provider, session_factory)
    refresh_job = next(job for job in scheduler_service.jobs if job.id == JOB_ID_FNO_UNIVERSE_REFRESH)

    await refresh_job.func()  # must not raise
