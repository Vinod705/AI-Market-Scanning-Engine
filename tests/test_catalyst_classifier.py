"""Tests for app.catalyst.catalyst_classifier — deterministic keyword-rule
classification, no LLM, no I/O."""

from datetime import UTC, datetime, timedelta

from app.catalyst.catalyst_classifier import (
    classify_event_type,
    classify_freshness,
    classify_sentiment,
)
from app.catalyst.catalyst_models import CatalystType, Freshness, Sentiment

# --- classify_event_type ------------------------------------------------


def test_results_headline() -> None:
    assert (
        classify_event_type("TCS Q1 results: net profit rises 12%")
        == CatalystType.RESULTS
    )


def test_order_win_headline() -> None:
    assert (
        classify_event_type("L&T bags order worth Rs 2,000 crore from NHAI")
        == CatalystType.ORDER_WIN
    )


def test_merger_acquisition_headline() -> None:
    assert (
        classify_event_type("Adani Group to acquire majority stake in target company")
        == CatalystType.MERGER_ACQUISITION
    )


def test_regulatory_headline() -> None:
    assert (
        classify_event_type("SEBI issues show cause notice to company promoters")
        == CatalystType.REGULATORY
    )


def test_broker_action_headline() -> None:
    assert (
        classify_event_type("Morgan Stanley upgrades stock, raises target price to Rs 500")
        == CatalystType.BROKER_ACTION
    )


def test_management_commentary_headline() -> None:
    assert (
        classify_event_type("CEO says company on track to double revenue by FY28")
        == CatalystType.MANAGEMENT_COMMENTARY
    )


def test_dividend_headline() -> None:
    assert (
        classify_event_type("Board declares interim dividend of Rs 12 per share")
        == CatalystType.DIVIDEND
    )


def test_sector_catalyst_headline() -> None:
    assert (
        classify_event_type("NIFTY IT surges 1.3% as IT stocks rally")
        == CatalystType.SECTOR_CATALYST
    )


def test_other_when_no_keywords_match() -> None:
    assert classify_event_type("Company holds annual general meeting") == CatalystType.OTHER


def test_results_takes_priority_over_dividend_in_same_headline() -> None:
    """Both keyword groups match — RESULTS is checked first, since a
    dividend is often announced alongside results and the results
    announcement is the primary event (see module docstring)."""
    headline = "Company Q1 results: net profit rises, board declares interim dividend"
    assert classify_event_type(headline) == CatalystType.RESULTS


def test_classification_uses_summary_too() -> None:
    assert (
        classify_event_type("Stock in focus", summary="SEBI launches investigation into company")
        == CatalystType.REGULATORY
    )


# --- classify_sentiment --------------------------------------------------


def test_positive_sentiment() -> None:
    assert classify_sentiment("Stock surges 8% after profit rises") == Sentiment.POSITIVE


def test_negative_sentiment() -> None:
    assert classify_sentiment("Shares tumble after company misses estimates") == Sentiment.NEGATIVE


def test_neutral_sentiment_when_no_keywords() -> None:
    assert classify_sentiment("Company holds annual general meeting") == Sentiment.NEUTRAL


def test_neutral_sentiment_when_tied() -> None:
    assert classify_sentiment("Stock gains then falls in volatile session") == Sentiment.NEUTRAL


# --- classify_freshness --------------------------------------------------

_NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
_BREAKING_WINDOW = timedelta(minutes=30)
_STALE_WINDOW = timedelta(hours=24)


def test_breaking_within_window() -> None:
    published = _NOW - timedelta(minutes=10)
    result = classify_freshness(
        published, now=_NOW, breaking_window=_BREAKING_WINDOW, stale_window=_STALE_WINDOW
    )
    assert result == Freshness.BREAKING


def test_breaking_at_exact_boundary() -> None:
    published = _NOW - _BREAKING_WINDOW
    result = classify_freshness(
        published, now=_NOW, breaking_window=_BREAKING_WINDOW, stale_window=_STALE_WINDOW
    )
    assert result == Freshness.BREAKING


def test_recent_just_past_breaking_boundary() -> None:
    published = _NOW - _BREAKING_WINDOW - timedelta(minutes=1)
    result = classify_freshness(
        published, now=_NOW, breaking_window=_BREAKING_WINDOW, stale_window=_STALE_WINDOW
    )
    assert result == Freshness.RECENT


def test_recent_at_exact_stale_boundary() -> None:
    published = _NOW - _STALE_WINDOW
    result = classify_freshness(
        published, now=_NOW, breaking_window=_BREAKING_WINDOW, stale_window=_STALE_WINDOW
    )
    assert result == Freshness.RECENT


def test_stale_news_never_reported_as_breaking() -> None:
    published = _NOW - timedelta(days=3)
    result = classify_freshness(
        published, now=_NOW, breaking_window=_BREAKING_WINDOW, stale_window=_STALE_WINDOW
    )
    assert result == Freshness.STALE
    assert result != Freshness.BREAKING


def test_future_timestamp_clock_skew_treated_as_breaking_not_negative_age() -> None:
    published = _NOW + timedelta(minutes=5)
    result = classify_freshness(
        published, now=_NOW, breaking_window=_BREAKING_WINDOW, stale_window=_STALE_WINDOW
    )
    assert result == Freshness.BREAKING
