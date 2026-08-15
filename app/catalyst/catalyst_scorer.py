"""Materiality scoring and final normalization into `NormalizedNewsItem` —
deterministic, no LLM.

**Materiality has no universal published standard** to reuse the way
RRG's quadrant convention or OI-buildup's sign rule do — ranking RESULTS
against DIVIDEND against BROKER_ACTION is a genuine judgment call, not a
verified external rule. `_DEFAULT_MATERIALITY_BY_EVENT_TYPE` below is a
minimal-judgment starting default (specific, company-level corporate
actions that structurally change financial position or legal status —
RESULTS, M&A, REGULATORY — are HIGH; narrower concrete events — ORDER_WIN,
DIVIDEND, BROKER_ACTION — are MEDIUM; broad/indirect signals —
MANAGEMENT_COMMENTARY, SECTOR_CATALYST, OTHER — are LOW), not a validated
business rule. Treat it as a starting point to adjust, not ground truth.
"""

from datetime import datetime, timedelta

from app.catalyst.catalyst_classifier import (
    classify_event_type,
    classify_freshness,
    classify_sentiment,
)
from app.catalyst.catalyst_models import (
    CatalystType,
    Materiality,
    NormalizedNewsItem,
    RawNewsArticle,
)
from app.config.settings import Settings
from app.core.time import utc_now

_DEFAULT_MATERIALITY_BY_EVENT_TYPE: dict[CatalystType, Materiality] = {
    CatalystType.RESULTS: Materiality.HIGH,
    CatalystType.MERGER_ACQUISITION: Materiality.HIGH,
    CatalystType.REGULATORY: Materiality.HIGH,
    CatalystType.ORDER_WIN: Materiality.MEDIUM,
    CatalystType.DIVIDEND: Materiality.MEDIUM,
    CatalystType.BROKER_ACTION: Materiality.MEDIUM,
    CatalystType.MANAGEMENT_COMMENTARY: Materiality.LOW,
    CatalystType.SECTOR_CATALYST: Materiality.LOW,
    CatalystType.OTHER: Materiality.LOW,
}


def score_materiality(event_type: CatalystType) -> Materiality:
    return _DEFAULT_MATERIALITY_BY_EVENT_TYPE[event_type]


def normalize(
    article: RawNewsArticle, settings: Settings, *, now: datetime | None = None
) -> NormalizedNewsItem:
    """Runs one `RawNewsArticle` through classification/scoring into the
    8-field normalized shape the spec requires. `now` is injectable for
    deterministic tests; defaults to the real current time."""
    resolved_now = now if now is not None else utc_now()

    event_type = classify_event_type(article.headline, article.summary)
    sentiment = classify_sentiment(article.headline, article.summary)
    materiality = score_materiality(event_type)
    freshness = classify_freshness(
        article.published_at,
        now=resolved_now,
        breaking_window=timedelta(minutes=settings.catalyst_breaking_news_window_minutes),
        stale_window=timedelta(hours=settings.catalyst_stale_news_window_hours),
    )

    return NormalizedNewsItem(
        symbol=article.symbol,
        headline=article.headline,
        source=article.source,
        published_at=article.published_at,
        event_type=event_type,
        sentiment=sentiment,
        materiality=materiality,
        freshness=freshness,
        computed_at=resolved_now,
        article_url=article.article_url,
    )
