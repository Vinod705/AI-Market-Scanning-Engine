"""Tests for app.repositories.feature_repository."""

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.providers.base_provider import ProviderSymbol
from app.repositories.feature_repository import DailyFeatureRepository
from app.repositories.market_repository import SymbolRepository


async def test_daily_feature_upsert_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        symbol = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="TCS", exchange="N", instrument_token="1")
        )
        await session.commit()

        repo = DailyFeatureRepository(session)
        await repo.upsert(symbol.id, date(2026, 1, 5), {"rsi14": 55.5, "golden_cross": True})
        await repo.upsert(symbol.id, date(2026, 1, 5), {"rsi14": 60.0, "golden_cross": False})
        await session.commit()

        history = await repo.get_history(symbol.id, limit=10)
        assert len(history) == 1
        assert float(history[0].rsi14) == 60.0
        assert history[0].golden_cross is False


async def test_get_latest_date_returns_none_when_no_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        symbol = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="INFY", exchange="N", instrument_token="2")
        )
        await session.commit()
        latest_date = await DailyFeatureRepository(session).get_latest_date(symbol.id)
        assert latest_date is None


async def test_get_status_summary_counts_distinct_symbols(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        symbol_repo = SymbolRepository(session)
        tcs = await symbol_repo.upsert(
            ProviderSymbol(symbol="TCS", exchange="N", instrument_token="1")
        )
        infy = await symbol_repo.upsert(
            ProviderSymbol(symbol="INFY", exchange="N", instrument_token="2")
        )
        await session.commit()

        repo = DailyFeatureRepository(session)
        await repo.upsert(tcs.id, date(2026, 1, 5), {"rsi14": 50})
        await repo.upsert(tcs.id, date(2026, 1, 6), {"rsi14": 51})
        await repo.upsert(infy.id, date(2026, 1, 6), {"rsi14": 48})
        await session.commit()

        summary = await repo.get_status_summary()
        assert summary.symbols_with_features == 2
        assert summary.total_daily_feature_rows == 3
        assert summary.last_computed_at is not None
