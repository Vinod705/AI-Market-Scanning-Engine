"""Integration tests for app.scanner.engine.ScannerEngine against an in-memory DB."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.providers.base_provider import Candle, ProviderSymbol
from app.repositories.feature_repository import DailyFeatureRepository
from app.repositories.market_repository import PriceRepository, SymbolRepository
from app.repositories.scanner_repository import ScannerRunRepository
from app.scanner.breakout_scanner import BreakoutScanner
from app.scanner.engine import ScannerEngine
from app.scanner.scanner_registry import ScannerRegistry

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


async def _seed_qualifying_symbol(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="TCS", exchange="N", instrument_token="1")
        )
        await session.commit()

        await PriceRepository(session).upsert_daily_many(
            symbol_row.id,
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
            symbol_row.id, date(2026, 1, 5), _QUALIFYING_FEATURE_VALUES
        )
        await session.commit()


def _registry() -> ScannerRegistry:
    registry = ScannerRegistry()
    registry.register(BreakoutScanner(Settings()))
    return registry


async def test_run_all_records_a_finished_run_with_correct_counts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_qualifying_symbol(session_factory)

    engine = ScannerEngine(session_factory, _registry())
    result = await engine.run_all()

    assert result.scanners_run == 1
    assert result.symbols_scanned == 1
    assert result.qualified_count == 1
    assert result.rejected_count == 0
    assert result.error_count == 0

    async with session_factory() as session:
        runs = await ScannerRunRepository(session).get_recent(scanner_name="breakout_v1")
        assert len(runs) == 1
        assert runs[0].finish_time is not None
        assert runs[0].qualified_count == 1


async def test_run_all_with_no_active_symbols_still_records_a_run(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    engine = ScannerEngine(session_factory, _registry())
    result = await engine.run_all()

    assert result.scanners_run == 1
    assert result.symbols_scanned == 0


async def test_run_all_flags_stale_features_without_blocking_scan(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """daily_prices runs 3 days ahead of daily_features: the run must
    still execute honestly (never a silent empty result) and surface the
    staleness on its own result rather than hiding it."""
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="STALESYM", exchange="N", instrument_token="stale1")
        )
        await session.commit()
        await PriceRepository(session).upsert_daily_many(
            symbol_row.id,
            [
                Candle(timestamp=datetime(2026, 1, d), open=100, high=101, low=99, close=100, volume=1000)
                for d in (5, 6, 7, 8)
            ],
        )
        await DailyFeatureRepository(session).upsert(
            symbol_row.id, date(2026, 1, 5), _QUALIFYING_FEATURE_VALUES
        )
        await session.commit()

    engine = ScannerEngine(session_factory, _registry())
    result = await engine.run_all()

    assert result.features_stale is True
    assert "daily_features" in result.freshness_reason
    assert result.scanners_run == 1  # scanning still ran, honestly, not silently skipped
    assert result.qualified_count == 0
