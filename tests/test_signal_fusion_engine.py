"""Integration tests for app.signals.signal_fusion_engine.SignalFusionEngine
against an in-memory DB with deterministic fixtures."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.market.market_models import (
    MarketBreadthSnapshot,
    MarketRegimeEvidence,
    MarketRegimeState,
)
from app.catalyst.catalyst_models import RawNewsArticle
from app.catalyst.news_provider import NewsProvider
from app.config.settings import Settings
from app.fundamentals.models import FundamentalData
from app.fundamentals.queue_models import FetchStatus
from app.models.daily_feature import DailyFeature
from app.models.oi_observation import OiObservation
from app.providers.base_provider import Candle, ProviderSymbol
from app.repositories.feature_repository import DailyFeatureRepository
from app.repositories.fundamental_snapshot_repository import FundamentalSnapshotRepository
from app.repositories.market_repository import PriceRepository, SymbolRepository
from app.signals.signal_fusion_engine import SignalFusionEngine, _assemble, _missing, _score_volume
from app.signals.signal_models import ComponentScore, DataStatus

_QUALIFYING_FEATURES: dict[str, object] = {
    "trend_strength": Decimal("70"),
    "trend_direction": "up",
    "rsi14": Decimal("60"),
    "macd_histogram": Decimal("1.5"),
    "adx14": Decimal("30"),
    "relative_volume": Decimal("2.5"),
    "ema20": Decimal("100"),
    "ema50": Decimal("95"),
}


class _FakeNewsProvider(NewsProvider):
    name = "fake"

    def __init__(self, articles_by_symbol: dict[str, list[RawNewsArticle]] | None = None) -> None:
        self._articles = articles_by_symbol or {}

    async def get_news(self, symbols: list[str]) -> dict[str, list[RawNewsArticle]]:
        return {s: self._articles[s] for s in symbols if s in self._articles}


async def _seed_symbol(
    session_factory: async_sessionmaker[AsyncSession],
    symbol: str,
    *,
    with_price: bool = True,
    with_features: bool = True,
) -> int:
    async with session_factory() as session:
        row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol, exchange="NSE", instrument_token=f"NSE_EQ|{symbol}")
        )
        await session.commit()
        symbol_id = row.id

        if with_price:
            await PriceRepository(session).upsert_daily_many(
                symbol_id,
                [
                    Candle(
                        timestamp=datetime(2026, 1, 5),
                        open=100,
                        high=105,
                        low=99,
                        close=103,
                        volume=100_000,
                    )
                ],
            )
        if with_features:
            await DailyFeatureRepository(session).upsert(
                symbol_id, datetime(2026, 1, 5).date(), _QUALIFYING_FEATURES
            )
        await session.commit()
        return symbol_id


async def test_unknown_symbol_all_components_missing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    engine = SignalFusionEngine(session_factory, Settings())
    result = await engine.compute("NOSUCHSYMBOL")

    assert result.overall_score is None
    assert result.confidence == 0.0
    assert set(result.missing_data) == {
        "technical",
        "volume",
        "oi",
        "sector_rrg",
        "market_regime",
        "news",
        "fundamentals",
    }
    assert all(c.status == DataStatus.MISSING for c in result.component_scores.values())


async def test_symbol_with_no_data_at_all_is_missing_everywhere(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol(
        session_factory, "EMPTY", with_price=False, with_features=False
    )
    engine = SignalFusionEngine(session_factory, Settings())
    result = await engine.compute("EMPTY")

    assert result.overall_score is None
    assert result.component_scores["technical"].status == DataStatus.MISSING
    assert result.component_scores["volume"].status == DataStatus.MISSING


async def test_technical_and_volume_available_others_missing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol(session_factory, "PARTIAL")
    engine = SignalFusionEngine(session_factory, Settings())
    result = await engine.compute("PARTIAL")

    assert result.component_scores["technical"].status == DataStatus.AVAILABLE
    assert result.component_scores["volume"].status == DataStatus.AVAILABLE
    assert result.component_scores["oi"].status == DataStatus.MISSING
    assert result.component_scores["fundamentals"].status == DataStatus.MISSING
    assert result.overall_score is not None
    settings = Settings()
    expected_confidence = round(
        (settings.signal_fusion_weight_technical + settings.signal_fusion_weight_volume) * 100, 2
    )
    assert result.confidence == expected_confidence


async def test_volume_score_matches_relative_volume_normalization() -> None:
    """relative_volume=2.5 -> min(2.5/2, 1)*100 = 100, same formula
    BreakoutScanner/VcpScanner/MomentumScanner/OrbScanner already use."""
    daily = DailyFeature(symbol_id=1, date=datetime(2026, 1, 5).date(), relative_volume=Decimal("2.5"))
    component = _score_volume(Settings(), daily)
    assert component.status == DataStatus.AVAILABLE
    assert component.score == 100.0


async def test_oi_component_reflects_futures_classification(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory, "OISYM")
    async with session_factory() as session:
        session.add(
            OiObservation(
                symbol_id=symbol_id,
                instrument_key="NSE_FO|1",
                instrument_type="FUT",
                strike_price=None,
                expiry_date=datetime(2026, 9, 25).date(),
                observed_at=datetime.now(UTC),
                price=Decimal("100"),
                prev_price=Decimal("95"),
                price_change_pct=Decimal("5"),
                volume=1000,
                oi=Decimal("1100"),
                prev_oi=Decimal("1000"),
                oi_change=Decimal("100"),
                oi_change_pct=Decimal("10"),
                classification="LONG_BUILDUP",
            )
        )
        await session.commit()

    engine = SignalFusionEngine(session_factory, Settings())
    result = await engine.compute("OISYM")

    assert result.component_scores["oi"].status == DataStatus.AVAILABLE
    assert result.component_scores["oi"].score == 75.0
    assert "LONG_BUILDUP" in result.component_scores["oi"].reasons[0]


async def test_market_regime_injected_and_neutral_status(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol(session_factory, "REGSYM")
    breadth = MarketBreadthSnapshot(
        as_of=datetime(2026, 1, 5).date(),
        computed_at=datetime.now(UTC),
        total_symbols=1,
        advancing=0,
        declining=0,
        unchanged=0,
        advance_decline_pct=None,
        up_volume=0,
        down_volume=0,
        up_down_volume_pct=None,
        symbols_with_ema20=0,
        pct_above_ema20=None,
        symbols_with_ema50=0,
        pct_above_ema50=None,
        symbols_with_ema200=0,
        pct_above_ema200=None,
        symbols_with_52wk_range=0,
        new_highs=0,
        new_lows=0,
        new_highs_lows_net_pct=None,
    )
    regime = MarketRegimeEvidence(
        as_of=datetime(2026, 1, 5).date(),
        computed_at=datetime.now(UTC),
        breadth=breadth,
        index_symbol="NIFTY",
        index_trend_direction=None,
        index_trend_strength=None,
        volatility_index_symbol=None,
        volatility_state=None,
        sector_leading_pct=None,
        score=Decimal("50"),
        regime=MarketRegimeState.NEUTRAL,
    )

    engine = SignalFusionEngine(session_factory, Settings())
    result = await engine.compute("REGSYM", market_regime=regime)

    component = result.component_scores["market_regime"]
    assert component.status == DataStatus.NEUTRAL
    assert component.score == 50.0


async def test_market_regime_missing_when_not_supplied(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol(session_factory, "NOREGIME")
    engine = SignalFusionEngine(session_factory, Settings())
    result = await engine.compute("NOREGIME")
    assert result.component_scores["market_regime"].status == DataStatus.MISSING


async def test_news_component_missing_when_no_provider_injected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol(session_factory, "NONEWSPROVIDER")
    engine = SignalFusionEngine(session_factory, Settings())
    result = await engine.compute("NONEWSPROVIDER")
    assert result.component_scores["news"].status == DataStatus.MISSING


async def test_news_component_available_with_positive_articles(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol(session_factory, "NEWSSYM")
    article = RawNewsArticle(
        symbol="NEWSSYM",
        headline="Stock surges after strong quarterly results",
        summary="Net profit rises sharply",
        source="upstox",
        published_at=datetime.now(UTC) - timedelta(minutes=5),
        article_url="https://upstox.com/news/x",
    )
    provider = _FakeNewsProvider({"NEWSSYM": [article]})
    engine = SignalFusionEngine(session_factory, Settings(), news_provider=provider)
    result = await engine.compute("NEWSSYM")

    component = result.component_scores["news"]
    assert component.status == DataStatus.AVAILABLE
    assert component.score is not None
    assert component.score > 50.0  # positive sentiment article


async def test_news_component_missing_when_only_stale_news_exists(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol(session_factory, "STALENEWS")
    article = RawNewsArticle(
        symbol="STALENEWS",
        headline="Old news about the company",
        summary="",
        source="upstox",
        published_at=datetime.now(UTC) - timedelta(days=5),
    )
    provider = _FakeNewsProvider({"STALENEWS": [article]})
    engine = SignalFusionEngine(session_factory, Settings(), news_provider=provider)
    result = await engine.compute("STALENEWS")

    assert result.component_scores["news"].status == DataStatus.MISSING


async def test_fundamentals_component_from_cached_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory, "FUNDSYM")
    settings = Settings()
    async with session_factory() as session:
        data = FundamentalData(
            symbol="FUNDSYM",
            pe=15.0,
            pb=3.0,
            revenue_growth_3y_pct=18.0,
            eps_growth_3y_pct=15.0,
            roe_pct=25.0,
            roce_pct=22.0,
            operating_margin_pct=20.0,
            debt_to_equity=0.3,
            current_ratio=1.8,
            operating_cash_flow_cr=500.0,
            promoter_holding_pct=55.0,
        )
        await FundamentalSnapshotRepository(session, settings).upsert(
            symbol_id,
            data=data,
            source="upstox",
            status=FetchStatus.SUCCESS,
            error_message=None,
        )
        await session.commit()

    engine = SignalFusionEngine(session_factory, settings)
    result = await engine.compute("FUNDSYM")

    assert result.component_scores["fundamentals"].status == DataStatus.AVAILABLE
    assert result.component_scores["fundamentals"].score is not None


async def test_positive_and_negative_factors_bucketed_by_score() -> None:
    components = {
        "technical": ComponentScore(
            name="technical",
            status=DataStatus.AVAILABLE,
            score=80.0,
            weight=0.25,
            reasons=["Technical: strong uptrend"],
        ),
        "volume": ComponentScore(
            name="volume",
            status=DataStatus.AVAILABLE,
            score=20.0,
            weight=0.15,
            reasons=["Volume: relative_volume=0.30x average"],
        ),
    }
    result = _assemble("TEST", components, datetime.now(UTC))

    assert "Technical: strong uptrend" in result.positive_factors
    assert "Volume: relative_volume=0.30x average" in result.negative_factors


async def test_confidence_is_zero_when_nothing_available() -> None:
    components = {name: _missing(name, 0.1, "no data") for name in ["a", "b"]}
    result = _assemble("TEST", components, datetime.now(UTC))
    assert result.confidence == 0.0
    assert result.overall_score is None
