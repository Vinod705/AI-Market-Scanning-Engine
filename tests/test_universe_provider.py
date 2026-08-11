"""Tests for app.universe.provider.UniverseProvider and FnoUniverseRepository."""

from datetime import date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.providers.base_provider import ProviderSymbol
from app.repositories.fno_universe_repository import FnoUniverseRepository
from app.repositories.market_repository import SymbolRepository
from app.universe.provider import UniverseProvider

_DAYS_PER_YEAR = 365


async def _seed_symbol_with_listing_date(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    symbol_name: str,
    listing_date: date | None,
    is_active: bool = True,
) -> int:
    """Creates `symbol_name` with a real `Symbol.listing_date` set directly
    -- no daily_prices involved at all, since IPO-universe membership no
    longer depends on local price history in any way."""
    async with session_factory() as session:
        symbol = await SymbolRepository(session).upsert(
            ProviderSymbol(
                symbol=symbol_name,
                exchange="N",
                instrument_token=symbol_name,
                listing_date=(
                    datetime.combine(listing_date, datetime.min.time())
                    if listing_date is not None
                    else None
                ),
            )
        )
        if not is_active:
            symbol.is_active = False
        await session.commit()
        return symbol.id


async def test_get_ipo_universe_includes_a_stock_listed_10_days_ago(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The production scenario (MANIPALHOS et al.) that both the old
    pattern_ipo_base proxy and the intermediate bar-count proxy handled
    only by accident -- a real listing date handles it directly."""
    await _seed_symbol_with_listing_date(
        session_factory, symbol_name="MANIPALHOS", listing_date=date.today() - timedelta(days=10)
    )

    async with session_factory() as session:
        universe = await UniverseProvider(session, Settings()).get_ipo_universe()

    assert {s.symbol for s in universe} == {"MANIPALHOS"}


async def test_get_ipo_universe_includes_a_stock_listed_1_year_ago(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The core bug this redesign fixes: a 1-year-old IPO must still be
    evaluated, not just ones a few weeks old."""
    await _seed_symbol_with_listing_date(
        session_factory,
        symbol_name="ONEYEARCO",
        listing_date=date.today() - timedelta(days=_DAYS_PER_YEAR),
    )

    async with session_factory() as session:
        universe = await UniverseProvider(session, Settings()).get_ipo_universe()

    assert {s.symbol for s in universe} == {"ONEYEARCO"}


async def test_get_ipo_universe_includes_a_stock_listed_2_years_ago(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol_with_listing_date(
        session_factory,
        symbol_name="TWOYEARCO",
        listing_date=date.today() - timedelta(days=2 * _DAYS_PER_YEAR),
    )

    async with session_factory() as session:
        universe = await UniverseProvider(session, Settings()).get_ipo_universe()

    assert {s.symbol for s in universe} == {"TWOYEARCO"}


async def test_get_ipo_universe_includes_a_stock_at_exactly_the_age_boundary(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Boundary is inclusive: listing_date == today - N years is IN."""
    settings = Settings()
    cutoff = date.today() - timedelta(days=_DAYS_PER_YEAR * settings.ipo_universe_max_age_years)
    await _seed_symbol_with_listing_date(session_factory, symbol_name="EDGECO", listing_date=cutoff)

    async with session_factory() as session:
        universe = await UniverseProvider(session, settings).get_ipo_universe()

    assert {s.symbol for s in universe} == {"EDGECO"}


async def test_get_ipo_universe_excludes_a_stock_just_outside_the_age_boundary(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings()
    just_outside = date.today() - timedelta(
        days=_DAYS_PER_YEAR * settings.ipo_universe_max_age_years + 1
    )
    await _seed_symbol_with_listing_date(
        session_factory, symbol_name="OLDCO", listing_date=just_outside
    )

    async with session_factory() as session:
        universe = await UniverseProvider(session, settings).get_ipo_universe()

    assert universe == []


async def test_get_ipo_universe_excludes_a_stock_with_null_listing_date(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Unmatched/unbackfilled symbols (listing_date NULL) are correctly
    excluded, not guessed into the universe."""
    await _seed_symbol_with_listing_date(
        session_factory, symbol_name="UNBACKFILLED", listing_date=None
    )

    async with session_factory() as session:
        universe = await UniverseProvider(session, Settings()).get_ipo_universe()

    assert universe == []


async def test_get_ipo_universe_excludes_an_inactive_symbol_even_with_recent_listing_date(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol_with_listing_date(
        session_factory,
        symbol_name="INACTIVECO",
        listing_date=date.today() - timedelta(days=10),
        is_active=False,
    )

    async with session_factory() as session:
        universe = await UniverseProvider(session, Settings()).get_ipo_universe()

    assert universe == []


async def test_get_ipo_universe_age_window_is_configurable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A 4-year-old stock is excluded under the default 3-year window but
    included once the setting is widened -- confirms the 3 isn't
    hard-coded anywhere in the query."""
    await _seed_symbol_with_listing_date(
        session_factory,
        symbol_name="FOURYEARCO",
        listing_date=date.today() - timedelta(days=4 * _DAYS_PER_YEAR),
    )

    async with session_factory() as session:
        default_universe = await UniverseProvider(session, Settings()).get_ipo_universe()
    assert default_universe == []

    async with session_factory() as session:
        widened_universe = await UniverseProvider(
            session, Settings(ipo_universe_max_age_years=5)
        ).get_ipo_universe()
    assert {s.symbol for s in widened_universe} == {"FOURYEARCO"}


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
        universe = await UniverseProvider(session, Settings()).get_fno_universe()

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
        universe = await UniverseProvider(session, Settings()).get_listed_universe()
        active = await SymbolRepository(session).list_active()

    assert {s.symbol for s in universe} == {s.symbol for s in active}
