"""End-to-end check that a candidate-based scanner, run through the real
`ScannerEngine`/`ScannerManager` pipeline, only evaluates its own universe
— not every active symbol (that's `breakout_v1`'s behavior, unchanged) —
and produces a real qualified `scanner_results` row with the extended
`StockCandidate` fields in its snapshot."""

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.candidates.fno_momentum_scanner import FnoMomentumScanner
from app.candidates.ipo_intraday_scanner import IpoIntradayScanner
from app.config.settings import Settings
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
    registry.register(FnoMomentumScanner(settings))
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


async def test_ipo_intraday_scanner_persists_52w_high_low_and_qualifies(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """End-to-end through the real ScannerEngine/ScannerManager pipeline:
    IpoIntradayScanner.build_context's 52-week-high/low/volume enrichment
    (see app.candidates.ipo_intraday_scanner) must reach the persisted
    scanner_results row, and a candidate satisfying all four new checks
    plus the pre-existing setup/volume/score checks must qualify. Dates
    are recent (relative to "today") on purpose -- get_52_week_high_low
    bounds its window to the trailing 365 days, so bars older than that
    would silently drop out of the aggregate."""
    today = date.today()
    async with session_factory() as session:
        symbol = await SymbolRepository(session).upsert(
            ProviderSymbol(
                symbol="FRESHIPO",
                exchange="N",
                instrument_token="FRESHIPO",
                listing_date=datetime.combine(today - timedelta(days=3), datetime.min.time()),
            )
        )
        await session.commit()
        symbol_id = symbol.id

        await PriceRepository(session).upsert_daily_many(
            symbol_id,
            [
                # 3 days ago: near the recent low.
                Candle(
                    timestamp=datetime.combine(today - timedelta(days=3), datetime.min.time()),
                    open=58,
                    high=60,
                    low=55,
                    close=59,
                    volume=200_000,
                ),
                # 2 days ago: explosive move up, sets the 52-week high.
                Candle(
                    timestamp=datetime.combine(today - timedelta(days=2), datetime.min.time()),
                    open=59,
                    high=130,
                    low=58,
                    close=125,
                    volume=400_000,
                ),
                # Latest: slight pullback from the high.
                Candle(
                    timestamp=datetime.combine(today - timedelta(days=1), datetime.min.time()),
                    open=125,
                    high=128,
                    low=118,
                    close=122,
                    volume=300_000,
                ),
            ],
        )
        await DailyFeatureRepository(session).upsert(
            symbol_id,
            today - timedelta(days=1),
            {
                "resistance_level": Decimal("110"),
                "relative_volume": Decimal("3.0"),
                "adx14": Decimal("30"),
                "ema20": Decimal("115"),
                "ema50": Decimal("100"),
                "trend_direction": "up",
                "rsi14": Decimal("65"),
                "macd_histogram": Decimal("1.5"),
                "higher_high": True,
                "higher_low": True,
            },
        )
        await session.commit()

    settings = Settings()
    registry = ScannerRegistry()
    registry.register(IpoIntradayScanner(settings))
    engine = ScannerEngine(session_factory, registry)

    result = await engine.run_all()

    assert result.symbols_scanned == 1
    assert result.qualified_count == 1

    async with session_factory() as session:
        results = await ScannerResultRepository(session).list_results(
            scanner_name="ipo_intraday_v1"
        )

    assert len(results) == 1
    row = results[0]
    assert row.status == "qualified"
    snapshot = row.feature_snapshot
    assert float(snapshot["ipo_52w_high"]) == 130.0  # type: ignore[arg-type]
    assert float(snapshot["ipo_52w_low"]) == 55.0  # type: ignore[arg-type]
    assert snapshot["ipo_latest_volume"] == 300_000
    assert snapshot["universe"] == "IPO"
    assert snapshot["setup_state"] == "MOMENTUM"
    assert snapshot["alert_category"] == "IPO_MOMENTUM"
