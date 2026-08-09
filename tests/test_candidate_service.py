"""Integration tests for app.services.candidate_service.CandidateService —
exercises the full path from a real scanner run through to the
explainability API response, against an in-memory DB."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.candidates.fno_momentum_scanner import FnoMomentumScanner
from app.candidates.ipo_intraday_scanner import IpoIntradayScanner
from app.config.settings import Settings
from app.fundamentals.models import FundamentalData
from app.fundamentals.provider import FundamentalDataProvider
from app.fundamentals.unavailable_provider import UnavailableFundamentalDataProvider
from app.providers.base_provider import Candle, ProviderSymbol
from app.repositories.feature_repository import DailyFeatureRepository
from app.repositories.fno_universe_repository import FnoUniverseRepository
from app.repositories.market_repository import PriceRepository, SymbolRepository
from app.scanner.engine import ScannerEngine
from app.scanner.scanner_registry import ScannerRegistry
from app.services.candidate_service import CandidateService


async def _seed_qualifying_fno_candidate(
    session_factory: async_sessionmaker[AsyncSession], symbol_name: str = "HINDCO"
) -> None:
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
                    open=340,
                    high=345,
                    low=338,
                    close=340,
                    volume=500_000,
                )
            ],
        )
        await DailyFeatureRepository(session).upsert(
            symbol_id,
            date(2026, 1, 5),
            {
                "resistance_level": Decimal("300"),
                "relative_volume": Decimal("4.0"),
                "adx14": Decimal("30"),
                "ema20": Decimal("330"),
                "ema50": Decimal("310"),
                "trend_direction": "up",
                "rsi14": Decimal("62"),
                "macd_histogram": Decimal("1.5"),
                "higher_high": True,
                "higher_low": True,
            },
        )
        await session.commit()

        await FnoUniverseRepository(session).replace_all([symbol_id])
        await session.commit()


async def _run_fno_scanner(session_factory: async_sessionmaker[AsyncSession]) -> None:
    settings = Settings()
    registry = ScannerRegistry()
    registry.register(FnoMomentumScanner(settings, UnavailableFundamentalDataProvider()))
    engine = ScannerEngine(session_factory, registry)
    await engine.run_all()


async def test_list_candidates_returns_qualified_fno_candidate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_qualifying_fno_candidate(session_factory)
    await _run_fno_scanner(session_factory)

    async with session_factory() as session:
        summaries = await CandidateService(session, Settings()).list_candidates()

    assert len(summaries) == 1
    assert summaries[0].symbol == "HINDCO"
    assert summaries[0].universe == "FNO"
    assert summaries[0].fundamental_score is None
    assert summaries[0].scanner_sources == ["5PAISA"]
    assert summaries[0].scanner_confirmation_count == 1


async def test_explain_shows_scanner_sources(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_qualifying_fno_candidate(session_factory)
    await _run_fno_scanner(session_factory)

    async with session_factory() as session:
        result = await CandidateService(session, Settings()).get_explain("HINDCO")

    assert result is not None
    assert result.scanner_sources == ["5PAISA"]
    assert result.scanner_confirmation_count == 1


class _FakeTrendlyneLikeProvider(FundamentalDataProvider):
    name = "fake_trendlyne"

    async def get_fundamentals(self, symbol: str) -> FundamentalData | None:
        from app.fundamentals.models import FieldAvailability, FieldSnapshot

        data = FundamentalData(symbol=symbol, roe_pct=18.4, pe=24.1)
        data.field_snapshots["roe_pct"] = FieldSnapshot(
            field_name="roe_pct",
            value=18.4,
            source="Trendlyne",
            period="Annual",
            status=FieldAvailability.AVAILABLE,
        )
        data.field_snapshots["pe"] = FieldSnapshot(
            field_name="pe",
            value=24.1,
            source="Trendlyne",
            period="TTM",
            status=FieldAvailability.AVAILABLE,
        )
        return data


async def test_explain_shows_fundamental_field_sources(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_qualifying_fno_candidate(session_factory)
    settings = Settings()
    registry = ScannerRegistry()
    registry.register(FnoMomentumScanner(settings, _FakeTrendlyneLikeProvider()))
    engine = ScannerEngine(session_factory, registry)
    await engine.run_all()

    async with session_factory() as session:
        result = await CandidateService(session, settings).get_explain("HINDCO")

    assert result is not None
    sources_by_field = {s.field_name: s for s in result.fundamental_field_sources}
    assert sources_by_field["roe_pct"].value == 18.4
    assert sources_by_field["roe_pct"].source == "Trendlyne"
    assert sources_by_field["roe_pct"].period == "Annual"
    assert sources_by_field["roe_pct"].status == "AVAILABLE"


async def test_list_candidates_defaults_scanner_sources_for_pre_phase7_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A scanner_results row written before Phase 7 has no `scanner_sources`
    key in its feature_snapshot at all — must default to 5paisa-only
    rather than showing an empty discovery block."""
    from app.repositories.scanner_repository import ScannerResultRepository

    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="LEGACYCO", exchange="N", instrument_token="LEGACYCO")
        )
        await session.commit()
        await ScannerResultRepository(session).upsert(
            symbol_id=symbol_row.id,
            scanner_name="fno_momentum_v1",
            date=date(2026, 1, 5),
            score=Decimal("70.00"),
            status="qualified",
            reason="all conditions met",
            feature_snapshot={"universe": "FNO"},  # no scanner_sources key
        )
        await session.commit()

    async with session_factory() as session:
        summaries = await CandidateService(session, Settings()).list_candidates()

    assert len(summaries) == 1
    assert summaries[0].scanner_sources == ["5PAISA"]
    assert summaries[0].scanner_confirmation_count == 1


async def test_explain_returns_full_breakdown_for_qualified_candidate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_qualifying_fno_candidate(session_factory)
    await _run_fno_scanner(session_factory)

    async with session_factory() as session:
        result = await CandidateService(session, Settings()).get_explain("HINDCO")

    assert result is not None
    assert result.symbol == "HINDCO"
    assert result.fundamental_score is None
    assert result.fundamental_unavailable_reason is not None
    assert result.fundamental_breakdown == []
    assert len(result.technical_breakdown) > 0
    assert result.overall_score_breakdown.fundamental_contribution is None
    assert result.overall_score_breakdown.technical_weight == 1.0
    assert result.explanation.why_this_stock
    assert result.explanation.why_now
    assert isinstance(result.decision, str)
    assert len(result.decision_rules) > 0


async def test_list_candidates_filters_by_universe(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_qualifying_fno_candidate(session_factory, symbol_name="FNOCO")

    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="IPOCO", exchange="N", instrument_token="IPOCO")
        )
        await session.commit()
        symbol_id = symbol_row.id
        await PriceRepository(session).upsert_daily_many(
            symbol_id,
            [
                Candle(
                    timestamp=datetime(2026, 1, 5),
                    open=340,
                    high=345,
                    low=338,
                    close=340,
                    volume=500_000,
                )
            ],
        )
        await DailyFeatureRepository(session).upsert(
            symbol_id,
            date(2026, 1, 5),
            {
                "resistance_level": Decimal("300"),
                "relative_volume": Decimal("4.0"),
                "adx14": Decimal("30"),
                "pattern_ipo_base": True,
            },
        )
        await session.commit()

    settings = Settings()
    registry = ScannerRegistry()
    registry.register(FnoMomentumScanner(settings, UnavailableFundamentalDataProvider()))
    registry.register(IpoIntradayScanner(settings, UnavailableFundamentalDataProvider()))
    await ScannerEngine(session_factory, registry).run_all()

    async with session_factory() as session:
        fno_only = await CandidateService(session, settings).list_candidates(universe="FNO")
        ipo_only = await CandidateService(session, settings).list_candidates(universe="IPO")

    assert {c.symbol for c in fno_only} == {"FNOCO"}
    assert {c.symbol for c in ipo_only} == {"IPOCO"}


async def test_explain_returns_none_for_unknown_symbol(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        result = await CandidateService(session, Settings()).get_explain("NOSUCHSTOCK")
    assert result is None


async def test_explain_positive_and_negative_factors_are_split(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_qualifying_fno_candidate(session_factory)
    await _run_fno_scanner(session_factory)

    async with session_factory() as session:
        result = await CandidateService(session, Settings()).get_explain("HINDCO")

    assert result is not None
    for factor in result.positive_factors:
        assert factor.status == "POSITIVE"
    for factor in result.negative_factors:
        assert factor.status == "NEGATIVE"
