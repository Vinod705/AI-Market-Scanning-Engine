"""End-to-end check that a candidate-based scanner, run through the real
`ScannerEngine`/`ScannerManager` pipeline, only evaluates its own universe
— not every active symbol (that's `breakout_v1`'s behavior, unchanged) —
and produces a real qualified `scanner_results` row with the extended
`StockCandidate` fields in its snapshot."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.candidates.fno_momentum_scanner import FnoMomentumScanner
from app.config.settings import Settings
from app.fundamentals.unavailable_provider import UnavailableFundamentalDataProvider
from app.providers.base_provider import Candle, ProviderSymbol
from app.repositories.feature_repository import DailyFeatureRepository
from app.repositories.fno_universe_repository import FnoUniverseRepository
from app.repositories.market_repository import PriceRepository, SymbolRepository
from app.repositories.scanner_repository import ScannerResultRepository
from app.scanner.engine import ScannerEngine
from app.scanner.scanner_registry import ScannerRegistry


async def _seed_symbol(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    symbol_name: str,
    close: float,
    features: dict[str, object],
) -> int:
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol_name, exchange="N", instrument_token=symbol_name)
        )
        await session.commit()
        symbol_id = symbol_row.id

        await PriceRepository(session).upsert_daily_many(
            symbol_id,
            [
                Candle(
                    timestamp=datetime(2026, 1, 5),
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=500_000,
                )
            ],
        )
        await DailyFeatureRepository(session).upsert(symbol_id, date(2026, 1, 5), features)
        await session.commit()
        return symbol_id


async def test_fno_momentum_scanner_only_evaluates_fno_universe(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    fno_symbol_id = await _seed_symbol(
        session_factory,
        symbol_name="FNOCO",
        close=340.0,
        features={
            "resistance_level": Decimal("300"),
            "relative_volume": Decimal("2.0"),
            "adx14": Decimal("25"),
            "ema20": Decimal("330"),
            "ema50": Decimal("310"),
            "trend_direction": "up",
            "rsi14": Decimal("62"),
            "macd_histogram": Decimal("1.5"),
            "higher_high": True,
            "higher_low": True,
        },
    )
    # A symbol with an equally-qualifying setup, but never added to the F&O
    # universe — it must be skipped entirely, not just rejected.
    await _seed_symbol(
        session_factory,
        symbol_name="NOTFNOCO",
        close=340.0,
        features={
            "resistance_level": Decimal("300"),
            "relative_volume": Decimal("2.0"),
            "adx14": Decimal("25"),
        },
    )

    async with session_factory() as session:
        await FnoUniverseRepository(session).replace_all([fno_symbol_id])
        await session.commit()

    settings = Settings()
    registry = ScannerRegistry()
    registry.register(FnoMomentumScanner(settings, UnavailableFundamentalDataProvider()))
    engine = ScannerEngine(session_factory, registry)

    result = await engine.run_all()

    assert result.symbols_scanned == 1  # only the F&O-universe symbol
    assert result.qualified_count == 1

    async with session_factory() as session:
        results = await ScannerResultRepository(session).list_results(
            scanner_name="fno_momentum_v1"
        )

    assert len(results) == 1
    row = results[0]
    assert row.status == "qualified"
    assert row.feature_snapshot["universe"] == "FNO"
    assert row.feature_snapshot["setup_state"] == "MOMENTUM"
    assert row.feature_snapshot["alert_category"] == "FNO_MOMENTUM"
    assert row.feature_snapshot["fundamental_score"] is None
