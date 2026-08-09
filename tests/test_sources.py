"""Tests for app.candidates.sources — the Phase 7 dual-scanner
(5paisa + TradingView) candidate-discovery architecture.

Covers: TradingViewSourceProvider's honest empty-set behavior, scanner_sources
resolution/merging in build_candidate, and — via a fake test-only source
provider standing in for a real TradingView integration — that a symbol
found by two independent sources is still represented as exactly ONE
candidate/ScannerResult row with BOTH sources recorded, and the technical
score is computed exactly once regardless of how many sources flagged it.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.candidates.builder import build_candidate
from app.candidates.fno_momentum_scanner import FnoMomentumScanner
from app.candidates.models import Universe
from app.candidates.sources import CandidateSourceProvider, ScannerSource, TradingViewSourceProvider
from app.config.settings import Settings
from app.fundamentals.scorer import FundamentalScorer
from app.fundamentals.unavailable_provider import UnavailableFundamentalDataProvider
from app.providers.base_provider import Candle, ProviderSymbol
from app.repositories.feature_repository import DailyFeatureRepository
from app.repositories.fno_universe_repository import FnoUniverseRepository
from app.repositories.market_repository import PriceRepository, SymbolRepository
from app.repositories.scanner_repository import ScannerResultRepository
from app.scanner.engine import ScannerEngine
from app.scanner.scanner_registry import ScannerRegistry
from app.technical.scorer import TechnicalScorer


class _FakeAlwaysFindsProvider(CandidateSourceProvider):
    """Test double standing in for a real TradingView provider — flags
    every symbol it's asked about. Not a real integration; exists only to
    exercise the merge/dedup path without a live TradingView backend."""

    name = ScannerSource.TRADINGVIEW

    def __init__(self) -> None:
        self.call_count = 0

    async def discover(self, universe: Universe, session: AsyncSession) -> set[str]:
        self.call_count += 1
        return {"HINDCO"}


class _FakeNeverFindsProvider(CandidateSourceProvider):
    name = ScannerSource.TRADINGVIEW

    async def discover(self, universe: Universe, session: AsyncSession) -> set[str]:
        return set()


async def test_tradingview_source_provider_always_returns_empty_set(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        found = await TradingViewSourceProvider().discover(Universe.FNO, session)
    assert found == set()


async def _seed_symbol(
    session_factory: async_sessionmaker[AsyncSession], symbol_name: str = "HINDCO"
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
            },
        )
        await session.commit()
        return symbol_id


async def test_build_candidate_defaults_to_5paisa_only(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory)
    settings = Settings()
    async with session_factory() as session:
        symbol = await SymbolRepository(session).get_by_id(symbol_id)
        assert symbol is not None
        result = await build_candidate(
            symbol=symbol,
            universe=Universe.FNO,
            scanner_name="fno_momentum_v1",
            session=session,
            fundamental_provider=UnavailableFundamentalDataProvider(),
            fundamental_scorer=FundamentalScorer(settings),
            technical_scorer=TechnicalScorer(settings),
            settings=settings,
        )
    assert result.candidate is not None
    assert result.candidate.scanner_sources == ["5PAISA"]


async def test_build_candidate_merges_second_source_when_it_flags_the_symbol(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory)
    settings = Settings()
    fake_source = _FakeAlwaysFindsProvider()
    async with session_factory() as session:
        symbol = await SymbolRepository(session).get_by_id(symbol_id)
        assert symbol is not None
        result = await build_candidate(
            symbol=symbol,
            universe=Universe.FNO,
            scanner_name="fno_momentum_v1",
            session=session,
            fundamental_provider=UnavailableFundamentalDataProvider(),
            fundamental_scorer=FundamentalScorer(settings),
            technical_scorer=TechnicalScorer(settings),
            settings=settings,
            source_providers=[fake_source],
        )
    assert result.candidate is not None
    assert result.candidate.scanner_sources == ["5PAISA", "TRADINGVIEW"]
    assert fake_source.call_count == 1


async def test_build_candidate_does_not_add_source_that_did_not_flag_symbol(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory)
    settings = Settings()
    async with session_factory() as session:
        symbol = await SymbolRepository(session).get_by_id(symbol_id)
        assert symbol is not None
        result = await build_candidate(
            symbol=symbol,
            universe=Universe.FNO,
            scanner_name="fno_momentum_v1",
            session=session,
            fundamental_provider=UnavailableFundamentalDataProvider(),
            fundamental_scorer=FundamentalScorer(settings),
            technical_scorer=TechnicalScorer(settings),
            settings=settings,
            source_providers=[_FakeNeverFindsProvider()],
        )
    assert result.candidate is not None
    assert result.candidate.scanner_sources == ["5PAISA"]


async def test_scanner_sources_persisted_in_feature_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory)
    settings = Settings()
    async with session_factory() as session:
        symbol = await SymbolRepository(session).get_by_id(symbol_id)
        assert symbol is not None
        result = await build_candidate(
            symbol=symbol,
            universe=Universe.FNO,
            scanner_name="fno_momentum_v1",
            session=session,
            fundamental_provider=UnavailableFundamentalDataProvider(),
            fundamental_scorer=FundamentalScorer(settings),
            technical_scorer=TechnicalScorer(settings),
            settings=settings,
            source_providers=[_FakeAlwaysFindsProvider()],
        )
    assert result.candidate is not None
    snapshot = result.candidate.to_feature_snapshot()
    assert snapshot["scanner_sources"] == ["5PAISA", "TRADINGVIEW"]


async def test_dual_source_discovery_produces_one_row_not_two(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A symbol found by both 5paisa (the scanner pipeline itself) and a
    second source must still be exactly one scanner_results row with the
    technical score computed once — not a duplicate alert/row per source."""
    symbol_id = await _seed_symbol(session_factory)
    async with session_factory() as session:
        await FnoUniverseRepository(session).replace_all([symbol_id])
        await session.commit()

    settings = Settings()
    fake_source = _FakeAlwaysFindsProvider()
    registry = ScannerRegistry()
    registry.register(
        FnoMomentumScanner(
            settings, UnavailableFundamentalDataProvider(), source_providers=[fake_source]
        )
    )
    engine = ScannerEngine(session_factory, registry)
    result = await engine.run_all()

    assert result.symbols_scanned == 1
    assert fake_source.call_count == 1  # one technical build -> one discover() call

    async with session_factory() as session:
        rows = await ScannerResultRepository(session).list_results(scanner_name="fno_momentum_v1")
    assert len(rows) == 1
    assert rows[0].feature_snapshot["scanner_sources"] == ["5PAISA", "TRADINGVIEW"]
