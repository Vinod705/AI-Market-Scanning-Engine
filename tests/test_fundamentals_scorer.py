"""Tests for app.fundamentals.scorer.FundamentalScorer."""

from app.config.settings import Settings
from app.fundamentals.models import FundamentalData, FundamentalTier
from app.fundamentals.scorer import FundamentalScorer


def _settings() -> Settings:
    return Settings()


def test_none_data_is_reported_as_unknown_not_zero() -> None:
    result = FundamentalScorer(_settings()).score(None)
    assert result.tier == FundamentalTier.UNKNOWN
    assert result.score is None
    assert result.data_completeness_pct == 0.0
    assert result.factors == []


def test_fully_populated_strong_company_scores_good() -> None:
    data = FundamentalData(
        symbol="TCS",
        revenue_growth_3y_pct=18,
        eps_growth_3y_pct=15,
        profit_growth_3y_pct=16,
        roe_pct=25,
        roce_pct=28,
        operating_margin_pct=27,
        net_margin_pct=20,
        debt_to_equity=0.1,
        interest_coverage=20,
        operating_cash_flow_cr=5000,
        free_cash_flow_cr=3000,
        pe=22,
        promoter_holding_pct=72,
        promoter_pledge_pct=0,
    )
    result = FundamentalScorer(_settings()).score(data)
    assert result.tier == FundamentalTier.GOOD
    assert result.score is not None
    assert result.score >= 55.0
    assert result.data_completeness_pct == 100.0
    assert result.unknown_reasons == []


def test_missing_fields_are_excluded_not_penalized() -> None:
    """A company reporting only a handful of strong factors — enough to
    clear the data-completeness gate — should not be dragged down by
    treating every other, unreported factor as zero."""
    data = FundamentalData(
        symbol="NEWCO",
        roe_pct=30,
        roce_pct=30,
        operating_margin_pct=28,
        net_margin_pct=22,
        debt_to_equity=0.1,
        interest_coverage=15,
        pe=18,
        promoter_holding_pct=70,
    )
    result = FundamentalScorer(_settings()).score(data)
    assert result.score is not None
    assert result.score >= 80.0  # driven entirely by the known, strong factors
    assert result.data_completeness_pct < 100.0
    assert len(result.unknown_reasons) > 0


def test_low_completeness_reports_limited_risk_flag() -> None:
    settings = Settings(fundamental_min_data_completeness_pct=90.0)
    data = FundamentalData(symbol="NEWCO", roe_pct=30)
    result = FundamentalScorer(settings).score(data)
    assert result.tier == FundamentalTier.UNKNOWN  # below the completeness threshold
    assert any("Limited fundamental data" in flag for flag in result.risk_flags)


def test_high_promoter_pledge_is_flagged_as_risk() -> None:
    data = FundamentalData(symbol="RISKY", promoter_pledge_pct=45)
    result = FundamentalScorer(_settings()).score(data)
    assert any("pledge" in flag.lower() for flag in result.risk_flags)


def test_weak_company_scores_weak_not_good() -> None:
    # Enough fields populated to clear the data-completeness threshold, all
    # of them weak — this should land on WEAK, not UNKNOWN (completeness is
    # fine) and not GOOD.
    data = FundamentalData(
        symbol="WEAKCO",
        revenue_growth_3y_pct=-5,
        roe_pct=2,
        roce_pct=3,
        operating_margin_pct=1,
        net_margin_pct=0.5,
        debt_to_equity=3.5,
        interest_coverage=0.5,
        operating_cash_flow_cr=-100,
        pe=200,
        promoter_pledge_pct=60,
    )
    result = FundamentalScorer(_settings()).score(data)
    assert result.tier == FundamentalTier.WEAK
    assert len(result.negative_reasons) > 0


def test_every_factor_carries_a_reason() -> None:
    data = FundamentalData(symbol="TCS", roe_pct=25, pe=20)
    result = FundamentalScorer(_settings()).score(data)
    for factor in result.factors:
        assert factor.reason
        assert factor.weight > 0
