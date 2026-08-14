"""Tests for the market data ORM models and repository layer."""

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.daily_price import DailyPrice
from app.models.symbol import Symbol
from app.providers.base_provider import Candle, ProviderSymbol
from app.repositories.market_repository import (
    CollectorLogRepository,
    MarketStatusRepository,
    PriceRepository,
    SymbolRepository,
)


async def test_symbol_unique_constraint(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        session.add(Symbol(symbol="TCS", exchange="N", instrument_token="11536"))
        session.add(Symbol(symbol="TCS", exchange="N", instrument_token="99999"))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_symbol_repository_upsert_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider_symbol = ProviderSymbol(
        symbol="TCS", exchange="N", instrument_token="11536", company_name="TCS Ltd"
    )

    async with session_factory() as session:
        repo = SymbolRepository(session)
        await repo.upsert(provider_symbol)
        await repo.upsert(provider_symbol)
        await session.commit()

        active = await repo.list_active()
        assert len(active) == 1
        assert active[0].company_name == "TCS Ltd"


async def test_symbol_repository_upsert_matches_across_exchange_spelling(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Live bug this session: FivePaisa writes exchange="N", Upstox writes
    exchange="NSE" for the same real symbol. upsert() used to match on the
    literal (symbol, exchange) pair, so switching primary providers left
    it blind to the existing row and created a duplicate instead of
    updating in place — confirmed live (2,465 symbols duplicated in one
    refresh). Matching by symbol alone fixes it."""
    async with session_factory() as session:
        repo = SymbolRepository(session)
        first = await repo.upsert(
            ProviderSymbol(symbol="TCS", exchange="N", instrument_token="11536")
        )
        await session.commit()

        second = await repo.upsert(
            ProviderSymbol(symbol="TCS", exchange="NSE", instrument_token="NSE_EQ|INE467B01029")
        )
        await session.commit()

        assert second.id == first.id  # same row updated, not a new one
        assert second.exchange == "NSE"
        assert second.instrument_token == "NSE_EQ|INE467B01029"

        active = await repo.list_active()
        assert len(active) == 1


async def test_price_repository_upsert_daily_avoids_duplicates(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        symbol_repo = SymbolRepository(session)
        symbol = await symbol_repo.upsert(
            ProviderSymbol(symbol="TCS", exchange="N", instrument_token="11536")
        )
        await session.commit()

        price_repo = PriceRepository(session)
        candle = Candle(
            timestamp=datetime(2026, 1, 5), open=100, high=105, low=99, close=102, volume=1000
        )
        await price_repo.upsert_daily(symbol.id, candle)
        await price_repo.upsert_daily(symbol.id, candle.model_copy(update={"close": 103}))
        await session.commit()

        rows = (await session.execute(DailyPrice.__table__.select())).fetchall()
        assert len(rows) == 1
        assert float(rows[0].close) == 103


async def test_price_repository_get_52_week_high_low_returns_max_high_min_low(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    today = date.today()
    async with session_factory() as session:
        symbol = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="NEWCO", exchange="N", instrument_token="1")
        )
        await session.commit()

        price_repo = PriceRepository(session)
        await price_repo.upsert_daily_many(
            symbol.id,
            [
                Candle(
                    timestamp=datetime.combine(today - timedelta(days=3), datetime.min.time()),
                    open=100,
                    high=110,
                    low=95,
                    close=105,
                    volume=1000,
                ),
                Candle(
                    timestamp=datetime.combine(today - timedelta(days=2), datetime.min.time()),
                    open=105,
                    high=130,
                    low=90,
                    close=120,
                    volume=1000,
                ),
                Candle(
                    timestamp=datetime.combine(today - timedelta(days=1), datetime.min.time()),
                    open=120,
                    high=125,
                    low=115,
                    close=118,
                    volume=1000,
                ),
            ],
        )
        await session.commit()

        high_low = await price_repo.get_52_week_high_low(symbol.id)

    assert high_low is not None
    high, low = high_low
    assert float(high) == 130  # max of 110, 130, 125
    assert float(low) == 90  # min of 95, 90, 115


async def test_price_repository_get_52_week_high_low_ignores_bars_outside_the_window(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A bar older than 365 days must not count toward the 52-week
    high/low -- this is the entire point of bounding the window instead
    of aggregating unbounded over daily_prices."""
    today = date.today()
    async with session_factory() as session:
        symbol = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="OLDBARCO", exchange="N", instrument_token="1")
        )
        await session.commit()

        price_repo = PriceRepository(session)
        await price_repo.upsert_daily_many(
            symbol.id,
            [
                # Outside the window: an extreme high/low that must be ignored.
                Candle(
                    timestamp=datetime.combine(today - timedelta(days=400), datetime.min.time()),
                    open=500,
                    high=999,
                    low=1,
                    close=500,
                    volume=1000,
                ),
                # Inside the window.
                Candle(
                    timestamp=datetime.combine(today - timedelta(days=10), datetime.min.time()),
                    open=100,
                    high=120,
                    low=90,
                    close=110,
                    volume=1000,
                ),
            ],
        )
        await session.commit()

        high_low = await price_repo.get_52_week_high_low(symbol.id)

    assert high_low is not None
    high, low = high_low
    assert float(high) == 120
    assert float(low) == 90


async def test_price_repository_get_52_week_high_low_returns_none_for_no_bars(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        symbol = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="NOBARS", exchange="N", instrument_token="1")
        )
        await session.commit()

        high_low = await PriceRepository(session).get_52_week_high_low(symbol.id)

    assert high_low is None


async def test_market_status_repository_upsert_singleton(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repo = MarketStatusRepository(session)
        await repo.upsert(market_open=True, provider_connected=True, last_update=datetime.now())
        await repo.upsert(provider_connected=False)
        await session.commit()

        status = await repo.get()
        assert status is not None
        assert status.market_open is True
        assert status.provider_connected is False


async def test_collector_log_repository_start_and_finish(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repo = CollectorLogRepository(session)
        log = await repo.start(datetime(2026, 1, 5, 9, 0))
        await session.commit()

        await repo.finish(
            log,
            finish_time=datetime(2026, 1, 5, 9, 0, 30),
            symbols_processed=10,
            success_count=9,
            failed_count=1,
            error_message="one symbol failed",
        )
        await session.commit()

        assert log.duration == 30.0
        assert log.success_count == 9
        assert log.failed_count == 1


async def test_get_latest_daily_returns_most_recent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        symbol = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="TCS", exchange="N", instrument_token="11536")
        )
        await session.commit()

        price_repo = PriceRepository(session)
        await price_repo.upsert_daily(
            symbol.id,
            Candle(timestamp=datetime(2026, 1, 4), open=1, high=2, low=1, close=2, volume=10),
        )
        await price_repo.upsert_daily(
            symbol.id,
            Candle(timestamp=datetime(2026, 1, 5), open=2, high=3, low=2, close=3, volume=20),
        )
        await session.commit()

        latest = await price_repo.get_latest_daily(symbol.id)
        assert latest is not None
        assert latest.date == date(2026, 1, 5)
