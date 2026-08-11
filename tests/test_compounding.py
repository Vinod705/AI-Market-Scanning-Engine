"""Tests for the Compounding/Opportunity layer (app/compounding/).

Covers: the six potential-upside bands (including boundaries), target/
risk arithmetic, the "must not rank purely by upside%" requirement (a
high-upside/poor-risk-reward/no-confirmation/weak-fundamentals setup must
not outrank a moderate-upside/strong-risk-reward/confirmed/good-
fundamentals one), weekly/monthly timeframe labeling, and every "missing
data -> UNKNOWN, never fabricated" path. Also confirms existing candidate
explainability keeps working unchanged with the new field wired in.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.candidates.fno_momentum_scanner import FnoMomentumScanner
from app.compounding.engine import evaluate_compounding
from app.compounding.models import (
    CompoundingDecision,
    HigherTimeframeConfirmation,
    TargetClassification,
    Timeframe,
)
from app.compounding.risk import derive_risk
from app.compounding.target import classify_upside, derive_target
from app.config.settings import Settings
from app.providers.base_provider import Candle, ProviderSymbol
from app.repositories.feature_repository import DailyFeatureRepository
from app.repositories.fno_universe_repository import FnoUniverseRepository
from app.repositories.market_repository import PriceRepository, SymbolRepository
from app.scanner.engine import ScannerEngine
from app.scanner.scanner_registry import ScannerRegistry
from app.services.candidate_service import CandidateService


def _snapshot(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "price": "100.0",
        "resistance_level": None,
        "support_level": None,
        "swing_high": None,
        "swing_low": None,
        "atr14": None,
        "adx14": None,
        "trend_direction": None,
        "fundamental_score": None,
        "setup_state": "MOMENTUM",
    }
    base.update(overrides)
    return base


# --- classify_upside band boundaries (items 1-6 of the checklist) ------


def test_classify_upside_bands_and_boundaries() -> None:
    assert classify_upside(None) == TargetClassification.UNKNOWN
    assert classify_upside(-1.0) == TargetClassification.NOT_ATTRACTIVE
    assert classify_upside(4.99) == TargetClassification.NOT_ATTRACTIVE
    assert classify_upside(5.0) == TargetClassification.MINIMUM_OPPORTUNITY
    assert classify_upside(7.99) == TargetClassification.MINIMUM_OPPORTUNITY
    assert classify_upside(8.0) == TargetClassification.GOOD
    assert classify_upside(11.99) == TargetClassification.GOOD
    assert classify_upside(12.0) == TargetClassification.STRONG
    assert classify_upside(14.99) == TargetClassification.STRONG
    assert classify_upside(15.0) == TargetClassification.VERY_STRONG
    assert classify_upside(19.99) == TargetClassification.VERY_STRONG
    assert classify_upside(20.0) == TargetClassification.EXCEPTIONAL
    assert classify_upside(35.0) == TargetClassification.EXCEPTIONAL


# --- derive_target: measured-move arithmetic ----------------------------


def test_derive_target_measured_move_from_resistance_and_support() -> None:
    # target = resistance + (resistance - support) = 100 + 10 = 110 -> 10% upside
    result = derive_target(price=100.0, resistance_level=100.0, support_level=90.0, swing_high=None)
    assert result.target_price == 110.0
    assert result.potential_upside_pct == 10.0
    assert result.target_classification == TargetClassification.GOOD
    assert "measured move" in result.basis


def test_derive_target_falls_back_to_swing_high() -> None:
    result = derive_target(price=100.0, resistance_level=None, support_level=None, swing_high=125.0)
    assert result.target_price == 125.0
    assert result.potential_upside_pct == 25.0
    assert result.target_classification == TargetClassification.EXCEPTIONAL
    assert "swing high" in result.basis


def test_derive_target_unknown_when_no_level_available() -> None:
    result = derive_target(price=100.0, resistance_level=None, support_level=None, swing_high=None)
    assert result.target_price is None
    assert result.potential_upside_pct is None
    assert result.target_classification == TargetClassification.UNKNOWN


def test_derive_target_unknown_when_price_missing() -> None:
    result = derive_target(price=None, resistance_level=110.0, support_level=90.0, swing_high=None)
    assert result.target_classification == TargetClassification.UNKNOWN


def test_derive_target_honestly_reports_small_upside_when_move_already_played_out() -> None:
    """A MOMENTUM stock that has already exceeded its original measured-move
    objective must show low/negative upside from here — not be silently
    reprojected to look attractive again."""
    result = derive_target(price=130.0, resistance_level=100.0, support_level=90.0, swing_high=None)
    assert result.target_price == 110.0  # already below current price
    assert result.potential_upside_pct is not None
    assert result.potential_upside_pct < 0
    assert result.target_classification == TargetClassification.NOT_ATTRACTIVE


# --- derive_risk ----------------------------------------------------------


def test_derive_risk_prefers_tighter_of_support_and_atr() -> None:
    # support stop = 90 (risk 10), ATR stop = 100 - 2*1.5 = 97 (risk 3) -> ATR tighter
    result = derive_risk(
        price=100.0, target_price=110.0, support_level=90.0, swing_low=None, atr14=2.0
    )
    assert result.stop_loss == 97.0
    assert result.risk_pct == 3.0
    assert result.reward_risk_ratio == round(10 / 3, 2)
    assert "ATR" in result.basis


def test_derive_risk_falls_back_to_swing_low_when_no_support_or_atr() -> None:
    result = derive_risk(
        price=100.0, target_price=110.0, support_level=None, swing_low=85.0, atr14=None
    )
    assert result.stop_loss == 85.0
    assert "swing low" in result.basis


def test_derive_risk_unknown_when_nothing_available() -> None:
    result = derive_risk(
        price=100.0, target_price=110.0, support_level=None, swing_low=None, atr14=None
    )
    assert result.stop_loss is None
    assert result.risk_pct is None
    assert result.reward_risk_ratio is None


# --- item 7+8: must not rank purely by upside% --------------------------


def test_high_upside_poor_risk_reward_does_not_rank_highly() -> None:
    """Stock A: 25% upside (EXCEPTIONAL), poor R:R, no higher-timeframe
    confirmation, weak fundamentals -> must not be a strong candidate."""
    settings = Settings()
    snapshot = _snapshot(
        price="100.0",
        swing_high="125.0",  # target 125 -> 25% upside, EXCEPTIONAL
        atr14="20.0",  # ATR stop = 100 - 30 = 70 -> risk 30%, R:R = 25/30 < 1 (poor)
        trend_direction="down",  # not confirmed
        fundamental_score="25.0",  # weak
    )
    result = evaluate_compounding(snapshot, settings)

    assert result.target_classification == TargetClassification.EXCEPTIONAL
    assert result.potential_upside_pct == 25.0
    assert result.reward_risk_ratio is not None and result.reward_risk_ratio < 1.0
    assert result.higher_timeframe_confirmation == HigherTimeframeConfirmation.NOT_CONFIRMED
    assert result.decision != CompoundingDecision.STRONG_CANDIDATE


def test_moderate_upside_strong_risk_reward_ranks_higher_than_high_upside_poor_setup() -> None:
    """Stock B: 9% upside (GOOD), strong R:R, confirmed weekly trend, good
    fundamentals -> must score higher than Stock A above despite the much
    smaller raw percentage. This is the spec's explicit worked example."""
    settings = Settings()

    stock_a = evaluate_compounding(
        _snapshot(
            price="100.0",
            swing_high="125.0",
            atr14="20.0",
            trend_direction="down",
            fundamental_score="25.0",
        ),
        settings,
    )
    stock_b = evaluate_compounding(
        _snapshot(
            price="100.0",
            resistance_level="99.5",
            support_level="90.0",  # target = 99.5+9.5=109 -> 9% upside, GOOD
            atr14="2.0",  # ATR stop = 97 -> risk 3%, R:R = 9/3 = 3.0 (strong)
            trend_direction="up",
            adx14="25.0",  # >= fno_momentum_min_adx (20) -> confirmed
            fundamental_score="80.0",  # good
        ),
        settings,
    )

    assert stock_a.potential_upside_pct == 25.0
    assert stock_b.potential_upside_pct == 9.0
    assert stock_a.target_classification == TargetClassification.EXCEPTIONAL
    assert stock_b.target_classification == TargetClassification.GOOD

    # The core requirement: raw upside% is NOT what determines ranking.
    assert stock_b.compounding_score > stock_a.compounding_score
    assert stock_b.decision == CompoundingDecision.STRONG_CANDIDATE
    assert stock_a.decision in (CompoundingDecision.WATCH, CompoundingDecision.TRADE_CANDIDATE)


# --- item 9: weekly/monthly setups ----------------------------------------


def test_very_strong_upside_with_confirmed_trend_labeled_monthly() -> None:
    settings = Settings()
    snapshot = _snapshot(
        price="100.0",
        resistance_level="103.75",
        support_level="90.0",  # target 117.5 -> 17.5% upside, VERY_STRONG
        trend_direction="up",
        adx14="25.0",
    )
    result = evaluate_compounding(snapshot, settings)
    assert result.target_classification == TargetClassification.VERY_STRONG
    assert result.higher_timeframe_confirmation == HigherTimeframeConfirmation.CONFIRMED
    assert result.timeframe == Timeframe.MONTHLY


def test_very_strong_upside_without_confirmation_labeled_weekly_not_monthly() -> None:
    """Must not assert a longer horizon than the data actually supports."""
    settings = Settings()
    snapshot = _snapshot(
        price="100.0",
        resistance_level="103.75",
        support_level="90.0",
        trend_direction="down",
    )
    result = evaluate_compounding(snapshot, settings)
    assert result.target_classification == TargetClassification.VERY_STRONG
    assert result.higher_timeframe_confirmation == HigherTimeframeConfirmation.NOT_CONFIRMED
    assert result.timeframe == Timeframe.WEEKLY


def test_minimum_opportunity_always_labeled_daily() -> None:
    settings = Settings()
    snapshot = _snapshot(
        price="100.0",
        resistance_level="98.0",
        support_level="90.0",  # target 106 -> 6% upside, MINIMUM_OPPORTUNITY
        trend_direction="up",
        adx14="25.0",
    )
    result = evaluate_compounding(snapshot, settings)
    assert result.target_classification == TargetClassification.MINIMUM_OPPORTUNITY
    assert result.timeframe == Timeframe.DAILY


# --- item 10: missing target data -----------------------------------------


def test_missing_target_data_is_explicit_unknown_not_fabricated() -> None:
    settings = Settings()
    result = evaluate_compounding(_snapshot(price="100.0"), settings)
    assert result.target_price is None
    assert result.potential_upside_pct is None
    assert result.target_classification == TargetClassification.UNKNOWN
    assert result.timeframe == Timeframe.UNKNOWN
    assert result.decision == CompoundingDecision.WATCH
    assert any("target" in limitation for limitation in result.data_limitations)


# --- item 11: missing fundamental data -------------------------------------


def test_missing_fundamental_data_is_unknown_and_treated_neutrally() -> None:
    settings = Settings()
    base_kwargs = dict(
        price="100.0",
        resistance_level="100.0",
        support_level="90.0",
        trend_direction="up",
        adx14="25.0",
    )
    with_known = evaluate_compounding(_snapshot(**base_kwargs, fundamental_score="50.0"), settings)
    with_unknown = evaluate_compounding(_snapshot(**base_kwargs), settings)

    assert with_unknown.fundamental_quality == "UNKNOWN"
    # A neutral 50 default must score identically to an actual 50 score —
    # UNKNOWN is treated as neutral, never as zero (never a fabricated
    # penalty either).
    assert with_unknown.compounding_score == with_known.compounding_score
    assert "fundamental score unavailable" in " ".join(with_unknown.data_limitations)


# --- item 12: missing ATR/volatility data ----------------------------------


def test_missing_atr_falls_back_to_support_level_for_stop() -> None:
    settings = Settings()
    result = evaluate_compounding(
        _snapshot(price="100.0", resistance_level="100.0", support_level="90.0"), settings
    )
    assert result.stop_loss == 90.0


def test_missing_atr_and_support_reports_stop_as_unknown() -> None:
    settings = Settings()
    result = evaluate_compounding(
        _snapshot(price="100.0", resistance_level="110.0", support_level=None), settings
    )
    assert result.target_price is not None  # target still resolves (resistance-only path)
    assert result.stop_loss is None
    assert result.risk_pct is None
    assert result.reward_risk_ratio is None
    assert any("stop-loss" in limitation for limitation in result.data_limitations)


# --- item 13: existing candidates continue working unchanged --------------


async def _seed_qualifying_fno_candidate(
    session_factory: async_sessionmaker[AsyncSession], symbol_name: str = "COMPOUNDCO"
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
                "support_level": Decimal("270"),
                "relative_volume": Decimal("4.0"),
                "adx14": Decimal("30"),
                "ema20": Decimal("330"),
                "ema50": Decimal("310"),
                "ema200": Decimal("290"),
                "trend_direction": "up",
                "rsi14": Decimal("62"),
                "macd_histogram": Decimal("1.5"),
                "higher_high": True,
                "higher_low": True,
                "atr14": Decimal("8"),
            },
        )
        await session.commit()

        await FnoUniverseRepository(session).replace_all([symbol_id])
        await session.commit()


async def test_existing_explain_response_unaffected_and_gains_compounding_field(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_qualifying_fno_candidate(session_factory)
    settings = Settings()
    registry = ScannerRegistry()
    registry.register(FnoMomentumScanner(settings))
    await ScannerEngine(session_factory, registry).run_all()

    async with session_factory() as session:
        result = await CandidateService(session, settings).get_explain("COMPOUNDCO")

    assert result is not None
    # Pre-existing fields still populated exactly as before.
    assert result.symbol == "COMPOUNDCO"
    assert result.universe == "FNO"
    assert result.fundamental_score is None
    assert result.fundamental_unavailable_reason is not None
    assert isinstance(result.decision, str)

    # New field is present and well-formed.
    assert result.compounding is not None
    assert result.compounding.target_classification in {c.value for c in TargetClassification}
    assert result.compounding.decision in {d.value for d in CompoundingDecision}
    assert result.compounding.timeframe in {t.value for t in Timeframe}
