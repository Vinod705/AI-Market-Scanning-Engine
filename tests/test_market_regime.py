"""Integration tests for app.analytics.market.regime against an in-memory
DB with deterministic market fixtures."""

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.market.market_models import MarketRegimeState
from app.analytics.market.regime import classify_regime, compute_market_regime
from app.config.settings import Settings
from app.providers.base_provider import Candle, ProviderSymbol
from app.repositories.feature_repository import DailyFeatureRepository
from app.repositories.market_repository import PriceRepository, SymbolRepository

_N = 40


# --- classify_regime (pure) -------------------------------------------


def test_classify_regime_none_when_score_is_none() -> None:
    assert classify_regime(None) is None


def test_classify_regime_supportive_at_top_third() -> None:
    assert classify_regime(Decimal("70")) == MarketRegimeState.SUPPORTIVE


def test_classify_regime_risk_off_at_bottom_third() -> None:
    assert classify_regime(Decimal("20")) == MarketRegimeState.RISK_OFF


def test_classify_regime_neutral_in_the_middle() -> None:
    assert classify_regime(Decimal("50")) == MarketRegimeState.NEUTRAL


def test_classify_regime_boundary_exactly_two_thirds_is_supportive() -> None:
    assert classify_regime(Decimal(200) / Decimal(3)) == MarketRegimeState.SUPPORTIVE


def test_classify_regime_boundary_exactly_one_third_is_risk_off() -> None:
    assert classify_regime(Decimal(100) / Decimal(3)) == MarketRegimeState.RISK_OFF


# --- compute_market_regime (integration) --------------------------------


async def _seed_index(
    session_factory: async_sessionmaker[AsyncSession],
    symbol: str,
    *,
    start_price: float,
    step: float,
    bb_width_values: list[float] | None = None,
) -> int:
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol, exchange="N", instrument_token=f"IDX|{symbol}")
        )
        await session.commit()
        symbol_id = symbol_row.id

        dates = [datetime(2026, 1, 1) + timedelta(days=i) for i in range(_N)]
        candles = [
            Candle(
                timestamp=d,
                open=start_price + i * step,
                high=start_price + i * step + 1,
                low=start_price + i * step - 1,
                close=start_price + i * step,
                volume=100_000,
            )
            for i, d in enumerate(dates)
        ]
        await PriceRepository(session).upsert_daily_many(symbol_id, candles)

        feature_repo = DailyFeatureRepository(session)
        for i, d in enumerate(dates):
            values: dict[str, object] = {
                "trend_direction": "up" if step > 0 else "down",
                "trend_strength": Decimal("70"),
            }
            if bb_width_values is not None:
                values["bb_width"] = bb_width_values[i]
            await feature_repo.upsert(symbol_id, d.date(), values)

        await session.commit()
        return symbol_id


async def _seed_stock(
    session_factory: async_sessionmaker[AsyncSession],
    symbol: str,
    *,
    prior_close: float,
    today_close: float,
) -> int:
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol, exchange="NSE", instrument_token=f"NSE_EQ|{symbol}")
        )
        await session.commit()
        symbol_id = symbol_row.id
        await PriceRepository(session).upsert_daily_many(
            symbol_id,
            [
                Candle(
                    timestamp=datetime(2026, 2, 8),
                    open=prior_close,
                    high=prior_close + 1,
                    low=prior_close - 1,
                    close=prior_close,
                    volume=10_000,
                ),
                Candle(
                    timestamp=datetime(2026, 2, 9),
                    open=today_close,
                    high=today_close + 1,
                    low=today_close - 1,
                    close=today_close,
                    volume=20_000,
                ),
            ],
        )
        await session.commit()
        return symbol_id


async def test_regime_computed_from_multiple_evidence_sources(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    bb_widths = [5.0 + (i % 3) * 0.1 for i in range(_N)]  # calm, low variance -> contraction-ish
    await _seed_index(
        session_factory, "NIFTY", start_price=100.0, step=0.4, bb_width_values=bb_widths
    )
    await _seed_stock(session_factory, "ADV1", prior_close=100, today_close=110)
    await _seed_stock(session_factory, "ADV2", prior_close=100, today_close=108)
    await _seed_stock(session_factory, "DEC1", prior_close=100, today_close=98)

    async with session_factory() as session:
        evidence = await compute_market_regime(session, Settings())

    assert evidence.score is not None
    assert evidence.regime is not None
    assert evidence.index_trend_direction == "up"
    assert evidence.index_trend_strength == 70
    assert "advance_decline" in evidence.evidence_sources_used
    assert "index_trend" in evidence.evidence_sources_used
    assert "sector_participation" in evidence.missing_evidence  # no sector_symbols supplied
    assert evidence.computed_at is not None
    assert evidence.as_of is not None


async def test_sector_participation_included_when_sector_symbols_supplied(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_index(session_factory, "NIFTY", start_price=100.0, step=0.2)
    await _seed_index(session_factory, "NIFTY IT", start_price=100.0, step=1.5)

    async with session_factory() as session:
        evidence = await compute_market_regime(
            session, Settings(), sector_symbols=["NIFTY IT"]
        )

    assert evidence.sector_leading_pct is not None
    assert "sector_participation" in evidence.evidence_sources_used


async def test_missing_index_symbol_still_computes_from_breadth_alone(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_stock(session_factory, "ADV1", prior_close=100, today_close=110)
    # No NIFTY symbol seeded at all.

    async with session_factory() as session:
        evidence = await compute_market_regime(session, Settings())

    assert evidence.index_trend_direction is None
    assert evidence.volatility_state is None
    assert "index_trend" in evidence.missing_evidence
    assert "volatility" in evidence.missing_evidence
    # Breadth evidence (from ADV1's real 2-bar history) still contributes.
    assert "advance_decline" in evidence.evidence_sources_used
    assert evidence.score is not None
