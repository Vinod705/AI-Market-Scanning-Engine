"""Tests for the market data ORM models and repository layer."""

from datetime import date, datetime

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
