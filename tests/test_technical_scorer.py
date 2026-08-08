"""Tests for app.technical.scorer.TechnicalScorer."""

from app.config.settings import Settings
from app.technical.inputs import TechnicalInputs
from app.technical.scorer import TechnicalScorer


def _settings() -> Settings:
    return Settings()


def _bullish_inputs() -> TechnicalInputs:
    return TechnicalInputs(
        price=110.0,
        ema20=105.0,
        ema50=100.0,
        trend_direction="up",
        trend_strength=80.0,
        rsi14=60.0,
        macd_histogram=1.5,
        adx14=30.0,
        relative_volume=2.5,
        accumulation_score=60.0,
        distribution_score=10.0,
        volatility_squeeze=True,
        atr_expansion=False,
        session_vwap=108.0,
        higher_high=True,
        higher_low=True,
    )


def _empty_inputs() -> TechnicalInputs:
    return TechnicalInputs(
        price=110.0,
        ema20=None,
        ema50=None,
        trend_direction=None,
        trend_strength=None,
        rsi14=None,
        macd_histogram=None,
        adx14=None,
        relative_volume=None,
        accumulation_score=None,
        distribution_score=None,
        volatility_squeeze=False,
        atr_expansion=False,
        session_vwap=None,
        higher_high=False,
        higher_low=False,
    )


def test_bullish_inputs_score_high() -> None:
    result = TechnicalScorer(_settings()).score(_bullish_inputs())
    assert result.score >= 70.0
    assert result.data_completeness_pct == 100.0
    assert len(result.positive_reasons) > 0


def test_missing_fields_are_excluded_and_flagged_unknown() -> None:
    result = TechnicalScorer(_settings()).score(_empty_inputs())
    assert result.data_completeness_pct < 100.0
    assert len(result.unknown_reasons) > 0
    # market_structure (higher_high/higher_low) and volatility_state are
    # always known — booleans default False, never None.
    known_factor_names = {f.factor_name for f in result.factors if f.normalized_score is not None}
    assert "market_structure" in known_factor_names
    assert "volatility_state" in known_factor_names


def test_score_is_never_none_even_with_sparse_data() -> None:
    result = TechnicalScorer(_settings()).score(_empty_inputs())
    assert result.score is not None
    assert 0.0 <= result.score <= 100.0


def test_every_known_factor_carries_a_reason() -> None:
    result = TechnicalScorer(_settings()).score(_bullish_inputs())
    for factor in result.factors:
        assert factor.reason
