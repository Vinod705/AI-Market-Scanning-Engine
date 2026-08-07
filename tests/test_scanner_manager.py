"""Integration tests for app.scanner.scanner_manager.ScannerManager against an in-memory DB."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.providers.base_provider import Candle, ProviderSymbol
from app.repositories.feature_repository import DailyFeatureRepository
from app.repositories.market_repository import PriceRepository, SymbolRepository
from app.repositories.scanner_repository import ScannerResultRepository
from app.scanner.breakout_scanner import BreakoutScanner
from app.scanner.scanner_manager import ScannerManager

_QUALIFYING_FEATURE_VALUES: dict[str, object] = {
    "ema20": Decimal("105"),
    "ema50": Decimal("100"),
    "ema200": Decimal("90"),
    "adx14": Decimal("25"),
    "relative_volume": Decimal("2.0"),
    "resistance_level": Decimal("112"),
    "trend_strength": Decimal("80"),
    "momentum_score": Decimal("50"),
    "atr_expansion": True,
    "rs_vs_nifty": Decimal("10"),
}


async def _seed_qualifying_symbol(
    session_factory: async_sessionmaker[AsyncSession], symbol: str = "TCS"
) -> int:
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol, exchange="N", instrument_token=symbol)
        )
        await session.commit()
        symbol_id = symbol_row.id

        await PriceRepository(session).upsert_daily_many(
            symbol_id,
            [
                Candle(
                    timestamp=datetime(2026, 1, 5),
                    open=108,
                    high=111,
                    low=107,
                    close=110,
                    volume=100_000,
                )
            ],
        )
        await DailyFeatureRepository(session).upsert(
            symbol_id, date(2026, 1, 5), _QUALIFYING_FEATURE_VALUES
        )
        await session.commit()
        return symbol_id


async def test_scan_one_qualifies_symbol_meeting_all_conditions(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_qualifying_symbol(session_factory)

    async with session_factory() as session:
        symbols = await SymbolRepository(session).list_active()

    manager = ScannerManager(session_factory)
    scanner = BreakoutScanner(Settings())
    stats = await manager.run_scanner(scanner, symbols, run_id=1)

    assert stats.symbols_scanned == 1
    assert stats.qualified_count == 1
    assert stats.rejected_count == 0
    assert stats.error_count == 0

    async with session_factory() as session:
        results = await ScannerResultRepository(session).get_for_symbol(symbol_id)
        assert len(results) == 1
        assert results[0].status == "qualified"


async def test_scan_one_rejects_symbol_without_features(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="INFY", exchange="N", instrument_token="2")
        )
        await session.commit()

    async with session_factory() as session:
        symbols = await SymbolRepository(session).list_active()

    manager = ScannerManager(session_factory)
    scanner = BreakoutScanner(Settings())
    stats = await manager.run_scanner(scanner, symbols, run_id=1)

    assert stats.rejected_count == 1
    assert stats.qualified_count == 0

    async with session_factory() as session:
        results = await ScannerResultRepository(session).get_for_symbol(symbol_row.id)
        assert (
            results == []
        )  # rejection from a validation/missing-data failure writes no result row


async def test_scan_one_rejects_symbol_failing_validation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="WIPRO", exchange="N", instrument_token="3")
        )
        await session.commit()
        symbol_id = symbol_row.id

        # ema200 missing -> fails validate(), never reaches scan()/save_results().
        incomplete_values = {k: v for k, v in _QUALIFYING_FEATURE_VALUES.items() if k != "ema200"}
        await DailyFeatureRepository(session).upsert(symbol_id, date(2026, 1, 5), incomplete_values)
        await session.commit()

    async with session_factory() as session:
        symbols = await SymbolRepository(session).list_active()

    manager = ScannerManager(session_factory)
    scanner = BreakoutScanner(Settings())
    stats = await manager.run_scanner(scanner, symbols, run_id=1)

    assert stats.rejected_count == 1
    assert stats.qualified_count == 0

    async with session_factory() as session:
        results = await ScannerResultRepository(session).get_for_symbol(symbol_id)
        assert results == []


async def test_scan_one_skips_symbol_already_scanned_for_the_date(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_qualifying_symbol(session_factory)

    async with session_factory() as session:
        symbols = await SymbolRepository(session).list_active()

    manager = ScannerManager(session_factory)
    scanner = BreakoutScanner(Settings())
    await manager.run_scanner(scanner, symbols, run_id=1)
    second_stats = await manager.run_scanner(scanner, symbols, run_id=2)

    # Already scanned for that feature date — no new qualification/rejection counted.
    assert second_stats.qualified_count == 0
    assert second_stats.rejected_count == 0

    async with session_factory() as session:
        results = await ScannerResultRepository(session).list_results(scanner_name="breakout_v1")
        assert len(results) == 1  # still just the one row — no duplicate
