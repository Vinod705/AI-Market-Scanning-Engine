"""Tests for app.repositories.fundamental_snapshot_repository.FundamentalSnapshotRepository —
the cached-snapshot layer's persistence and freshness contract."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.fundamentals.models import FundamentalData
from app.fundamentals.queue_models import FetchStatus
from app.models.fundamental_snapshot import FundamentalSnapshot
from app.providers.base_provider import ProviderSymbol
from app.repositories.fundamental_snapshot_repository import FundamentalSnapshotRepository
from app.repositories.market_repository import SymbolRepository


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {"fundamental_cache_ttl_minutes": 240}
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


async def _seed_symbol_id(session_factory: async_sessionmaker[AsyncSession], symbol: str) -> int:
    async with session_factory() as session:
        row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol, exchange="NSE", instrument_token=f"NSE_EQ|{symbol}")
        )
        await session.commit()
        return row.id


async def test_get_cached_returns_none_for_unknown_symbol(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        cached = await FundamentalSnapshotRepository(session, _settings()).get_cached(999)
    assert cached is None


async def test_upsert_success_populates_value_source_and_freshness(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol_id(session_factory, "TCS")
    data = FundamentalData(symbol="TCS", pe=17.05, roe_pct=45.89)

    async with session_factory() as session:
        await FundamentalSnapshotRepository(session, _settings()).upsert(
            symbol_id, data=data, source="upstox", status=FetchStatus.SUCCESS, error_message=None
        )
        await session.commit()

    async with session_factory() as session:
        cached = await FundamentalSnapshotRepository(session, _settings()).get_cached(symbol_id)

    assert cached is not None
    assert cached.data is not None
    assert cached.data.pe == 17.05
    assert cached.data.roe_pct == 45.89
    assert cached.source == "upstox"
    assert cached.status == FetchStatus.SUCCESS
    assert cached.error_message is None
    assert cached.fetched_at is not None
    assert cached.is_fresh is True


async def test_failed_upsert_never_erases_prior_value_only_updates_check_metadata(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol_id(session_factory, "INFY")
    good_data = FundamentalData(symbol="INFY", pe=25.0)

    async with session_factory() as session:
        repo = FundamentalSnapshotRepository(session, _settings())
        await repo.upsert(
            symbol_id,
            data=good_data,
            source="upstox",
            status=FetchStatus.SUCCESS,
            error_message=None,
        )
        await session.commit()

    async with session_factory() as session:
        before = await FundamentalSnapshotRepository(session, _settings()).get_cached(symbol_id)
    assert before is not None

    async with session_factory() as session:
        repo = FundamentalSnapshotRepository(session, _settings())
        await repo.upsert(
            symbol_id,
            data=None,
            source="upstox",
            status=FetchStatus.FAILED,
            error_message="Upstox API rate limited",
        )
        await session.commit()

    async with session_factory() as session:
        after = await FundamentalSnapshotRepository(session, _settings()).get_cached(symbol_id)

    assert after is not None
    assert after.status == FetchStatus.FAILED
    assert after.error_message == "Upstox API rate limited"
    # Last known-good value untouched by the failed attempt.
    assert after.data is not None
    assert after.data.pe == 25.0
    assert after.fetched_at == before.fetched_at
    assert after.last_checked_at >= before.last_checked_at


async def test_stale_data_is_reported_as_not_fresh_never_silently(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Simulates a snapshot fetched well outside the TTL window — the
    cache must say so via is_fresh=False, not present old data as current."""
    symbol_id = await _seed_symbol_id(session_factory, "STALE")

    async with session_factory() as session:
        row = FundamentalSnapshot(
            symbol_id=symbol_id,
            source="upstox",
            data={"pe": 10.0},
            fetched_at=datetime.now(UTC) - timedelta(hours=10),
            last_checked_at=datetime.now(UTC) - timedelta(hours=10),
            status=FetchStatus.SUCCESS.value,
        )
        session.add(row)
        await session.commit()

    async with session_factory() as session:
        cached = await FundamentalSnapshotRepository(
            session, _settings(fundamental_cache_ttl_minutes=240)
        ).get_cached(symbol_id)

    assert cached is not None
    assert cached.data is not None  # last known value still returned
    assert cached.is_fresh is False  # but honestly marked stale


async def test_never_fetched_snapshot_has_no_data_and_is_not_fresh(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A symbol that has only ever failed (never a successful fetch) has
    data=None, not a fabricated placeholder value."""
    symbol_id = await _seed_symbol_id(session_factory, "NEVERWORKED")

    async with session_factory() as session:
        await FundamentalSnapshotRepository(session, _settings()).upsert(
            symbol_id,
            data=None,
            source=None,
            status=FetchStatus.FAILED,
            error_message="no resolvable ISIN",
        )
        await session.commit()

    async with session_factory() as session:
        cached = await FundamentalSnapshotRepository(session, _settings()).get_cached(symbol_id)

    assert cached is not None
    assert cached.data is None
    assert cached.is_fresh is False
    assert cached.status == FetchStatus.FAILED
    assert cached.error_message == "no resolvable ISIN"


async def test_get_coverage_summary_counts_total_with_data_and_fresh(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    fresh_id = await _seed_symbol_id(session_factory, "FRESHSYM")
    stale_id = await _seed_symbol_id(session_factory, "STALESYM")
    failed_id = await _seed_symbol_id(session_factory, "FAILEDSYM")
    settings = _settings(fundamental_cache_ttl_minutes=60)

    async with session_factory() as session:
        repo = FundamentalSnapshotRepository(session, settings)
        await repo.upsert(
            fresh_id,
            data=FundamentalData(symbol="FRESHSYM", pe=20.0),
            source="Upstox",
            status=FetchStatus.SUCCESS,
            error_message=None,
        )
        await repo.upsert(
            failed_id,
            data=None,
            source=None,
            status=FetchStatus.FAILED,
            error_message="no resolvable ISIN",
        )
        await session.commit()

    # Backdate the "stale" row's fetched_at directly, since upsert() always
    # stamps the real current time — same technique the freshness test
    # above would need if it manipulated fetched_at instead of the TTL.
    async with session_factory() as session:
        await FundamentalSnapshotRepository(session, settings).upsert(
            stale_id,
            data=FundamentalData(symbol="STALESYM", pe=15.0),
            source="Upstox",
            status=FetchStatus.SUCCESS,
            error_message=None,
        )
        await session.commit()

    async with session_factory() as session:
        stmt = select(FundamentalSnapshot).where(FundamentalSnapshot.symbol_id == stale_id)
        row = (await session.execute(stmt)).scalar_one()
        row.fetched_at = datetime.now(UTC) - timedelta(hours=5)
        await session.commit()

    async with session_factory() as session:
        summary = await FundamentalSnapshotRepository(session, settings).get_coverage_summary()

    assert summary.total_snapshots == 3
    assert summary.with_data_count == 2  # fresh + stale, not failed
    assert summary.fresh_count == 1  # only the un-backdated one
    assert summary.last_fetched_at is not None
