"""Integration tests for app.candidates.builder.build_candidate against an
in-memory DB — exercises the setup-state detection, fundamental/technical
score blending, and feature-snapshot assembly together, the way a real
scanner run would."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.candidates.builder import build_candidate
from app.candidates.models import SetupState, Universe
from app.config.settings import Settings
from app.fundamentals.scorer import FundamentalScorer
from app.fundamentals.unavailable_provider import UnavailableFundamentalDataProvider
from app.providers.base_provider import Candle, ProviderSymbol
from app.repositories.feature_repository import DailyFeatureRepository
from app.repositories.market_repository import PriceRepository, SymbolRepository
from app.technical.scorer import TechnicalScorer


async def _seed_symbol(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    close: float,
    features: dict[str, object],
    symbol_name: str = "NEWCO",
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


async def _build(
    session_factory: async_sessionmaker[AsyncSession],
    symbol_id: int,
    settings: Settings | None = None,
) -> object:
    settings = settings or Settings()
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
    return result


async def test_build_candidate_returns_none_without_features(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="NOFEAT", exchange="N", instrument_token="1")
        )
        await session.commit()
        symbol_id = symbol_row.id

    result = await _build(session_factory, symbol_id)
    assert result.candidate is None
    assert result.skip_reason == "no features computed yet"


async def test_build_candidate_detects_pre_breakout_setup(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # price 2% below resistance -> within the default 5% proximity band.
    symbol_id = await _seed_symbol(
        session_factory,
        close=294.0,
        features={
            "resistance_level": Decimal("300"),
            "relative_volume": Decimal("1.0"),
            "adx14": Decimal("15"),
        },
    )
    result = await _build(session_factory, symbol_id)
    assert result.candidate is not None
    assert result.candidate.setup_state == SetupState.PRE_BREAKOUT


async def test_build_candidate_detects_breakout_confirmed_with_volume(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # price just above resistance, with rvol clearing fno_momentum_min_rvol.
    symbol_id = await _seed_symbol(
        session_factory,
        close=303.0,
        features={
            "resistance_level": Decimal("300"),
            "relative_volume": Decimal("2.0"),
            "adx14": Decimal("15"),
        },
    )
    result = await _build(session_factory, symbol_id)
    assert result.candidate is not None
    assert result.candidate.setup_state == SetupState.BREAKOUT_CONFIRMED


async def test_build_candidate_detects_momentum_continuation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # price well past resistance, ADX confirms sustained trend strength.
    symbol_id = await _seed_symbol(
        session_factory,
        close=340.0,
        features={
            "resistance_level": Decimal("300"),
            "relative_volume": Decimal("1.0"),
            "adx14": Decimal("25"),
        },
    )
    result = await _build(session_factory, symbol_id)
    assert result.candidate is not None
    assert result.candidate.setup_state == SetupState.MOMENTUM


async def test_build_candidate_returns_none_setup_state_without_resistance(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory, close=310.0, features={})
    result = await _build(session_factory, symbol_id)
    assert result.candidate is not None
    assert result.candidate.setup_state is None


async def test_overall_score_equals_technical_score_when_fundamental_unknown(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(
        session_factory,
        close=310.0,
        features={
            "resistance_level": Decimal("300"),
            "relative_volume": Decimal("2.0"),
            "adx14": Decimal("25"),
        },
    )
    result = await _build(session_factory, symbol_id)
    assert result.candidate is not None
    assert result.candidate.fundamental_score is None
    assert result.candidate.overall_score == round(result.candidate.technical_score, 2)


async def test_feature_snapshot_carries_candidate_fields(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(
        session_factory,
        close=340.0,
        features={
            "resistance_level": Decimal("300"),
            "relative_volume": Decimal("2.0"),
            "adx14": Decimal("25"),
        },
    )
    result = await _build(session_factory, symbol_id)
    assert result.candidate is not None
    snapshot = result.candidate.to_feature_snapshot()
    assert snapshot["universe"] == "FNO"
    assert snapshot["setup_state"] == "MOMENTUM"
    assert snapshot["fundamental_score"] is None
    assert "adx14" in snapshot  # raw daily-feature dump is merged in too
