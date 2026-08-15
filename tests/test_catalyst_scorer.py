"""Tests for app.catalyst.catalyst_scorer — materiality + normalization."""

from datetime import UTC, datetime

from app.catalyst.catalyst_models import CatalystType, Freshness, Materiality, RawNewsArticle
from app.catalyst.catalyst_scorer import normalize, score_materiality
from app.config.settings import Settings


def test_materiality_covers_every_catalyst_type() -> None:
    for event_type in CatalystType:
        materiality = score_materiality(event_type)
        assert isinstance(materiality, Materiality)


def test_results_is_high_materiality() -> None:
    assert score_materiality(CatalystType.RESULTS) == Materiality.HIGH


def test_other_is_low_materiality() -> None:
    assert score_materiality(CatalystType.OTHER) == Materiality.LOW


def test_normalize_produces_all_eight_required_fields() -> None:
    article = RawNewsArticle(
        symbol="TCS",
        headline="TCS Q1 results: net profit rises 12%",
        summary="",
        source="upstox",
        published_at=datetime(2026, 8, 15, 11, 50, tzinfo=UTC),
        article_url="https://upstox.com/news/example",
    )
    now = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)

    item = normalize(article, Settings(), now=now)

    assert item.symbol == "TCS"
    assert item.headline == article.headline
    assert item.source == "upstox"
    assert item.published_at == article.published_at
    assert item.event_type == CatalystType.RESULTS
    assert item.materiality == Materiality.HIGH
    assert item.freshness == Freshness.BREAKING
    assert item.computed_at == now
    assert item.article_url == "https://upstox.com/news/example"


def test_normalize_is_deterministic_for_the_same_input() -> None:
    article = RawNewsArticle(
        symbol="INFY",
        headline="Company holds annual general meeting",
        summary="",
        source="upstox",
        published_at=datetime(2026, 8, 10, 9, 0, tzinfo=UTC),
    )
    now = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)

    first = normalize(article, Settings(), now=now)
    second = normalize(article, Settings(), now=now)

    assert first == second
