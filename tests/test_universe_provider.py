"""Tests for app.universe.provider.UniverseProvider and FnoUniverseRepository."""

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.providers.base_provider import ProviderSymbol
from app.repositories.feature_repository import DailyFeatureRepository
from app.repositories.fno_universe_repository import FnoUniverseRepository
from app.repositories.market_repository import SymbolRepository
from app.universe.provider import UniverseProvider


async def test_get_ipo_universe_returns_symbols_with_latest_ipo_base_flag(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        ipo_symbol = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="FRESHCO", exchange="N", instrument_token="1")
        )
        other_symbol = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="OLDCO", exchange="N", instrument_token="2")
        )
        await session.commit()

        feature_repo = DailyFeatureRepository(session)
        await feature_repo.upsert(ipo_symbol.id, date(2026, 1, 5), {"pattern_ipo_base": True})
        await feature_repo.upsert(other_symbol.id, date(2026, 1, 5), {"pattern_ipo_base": False})
        await session.commit()

    async with session_factory() as session:
        universe = await UniverseProvider(session).get_ipo_universe()

    symbols = {s.symbol for s in universe}
    assert symbols == {"FRESHCO"}


async def test_get_ipo_universe_uses_only_the_latest_date_per_symbol(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A symbol that *used to* look like an IPO base but no longer does
    (its latest row says False) should drop out of the universe."""
    async with session_factory() as session:
        symbol = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="GROWNUP", exchange="N", instrument_token="1")
        )
        await session.commit()

        feature_repo = DailyFeatureRepository(session)
        await feature_repo.upsert(symbol.id, date(2026, 1, 1), {"pattern_ipo_base": True})
        await feature_repo.upsert(symbol.id, date(2026, 1, 5), {"pattern_ipo_base": False})
        await session.commit()

    async with session_factory() as session:
        universe = await UniverseProvider(session).get_ipo_universe()

    assert universe == []


async def test_get_fno_universe_returns_symbols_from_fno_universe_table(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        fno_symbol = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="RELIANCE", exchange="N", instrument_token="1")
        )
        await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="NOTFNO", exchange="N", instrument_token="2")
        )
        await session.commit()

        await FnoUniverseRepository(session).replace_all([fno_symbol.id])
        await session.commit()

    async with session_factory() as session:
        universe = await UniverseProvider(session).get_fno_universe()

    assert [s.symbol for s in universe] == ["RELIANCE"]


async def test_fno_universe_replace_all_clears_stale_membership(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        symbol_a = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="A", exchange="N", instrument_token="1")
        )
        symbol_b = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="B", exchange="N", instrument_token="2")
        )
        await session.commit()

        repo = FnoUniverseRepository(session)
        await repo.replace_all([symbol_a.id, symbol_b.id])
        await session.commit()
        assert await repo.count() == 2

        await repo.replace_all([symbol_a.id])
        await session.commit()
        assert await repo.count() == 1
        assert await repo.list_symbol_ids() == [symbol_a.id]


async def test_get_listed_universe_matches_active_symbols(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="LISTEDCO", exchange="N", instrument_token="1")
        )
        await session.commit()

    async with session_factory() as session:
        universe = await UniverseProvider(session).get_listed_universe()
        active = await SymbolRepository(session).list_active()

    assert {s.symbol for s in universe} == {s.symbol for s in active}
