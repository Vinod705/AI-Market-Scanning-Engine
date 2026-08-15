"""Domain types for the News and Catalyst Engine.

`RawNewsArticle` is what a `NewsProvider` returns (see `news_provider.py`)
— already matched to a symbol (see `news_matcher.py`), not yet classified.
`NormalizedNewsItem` is the fully classified, scored, timestamped shape
(see `catalyst_classifier.py`/`catalyst_scorer.py`) — the 8 fields the
project spec requires: symbol, headline, source, published_at, event_type,
sentiment, materiality, freshness.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class CatalystType(StrEnum):
    """Deterministic, keyword-rule classification (see
    `catalyst_classifier.classify_event_type`) — never an LLM call."""

    RESULTS = "RESULTS"
    ORDER_WIN = "ORDER_WIN"
    MERGER_ACQUISITION = "M&A"
    REGULATORY = "REGULATORY"
    BROKER_ACTION = "BROKER_ACTION"
    MANAGEMENT_COMMENTARY = "MANAGEMENT_COMMENTARY"
    DIVIDEND = "DIVIDEND"
    SECTOR_CATALYST = "SECTOR_CATALYST"
    OTHER = "OTHER"


class Sentiment(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NEUTRAL = "NEUTRAL"


class Materiality(StrEnum):
    """No universal, published standard exists for ranking catalyst
    materiality (unlike e.g. RRG's quadrant convention) — this is a
    judgment call, not a verified external rule. Default tiers (see
    `catalyst_scorer.py`) are configurable via `Settings`, not hardcoded,
    and should be treated as a starting point to adjust, not a validated
    business rule."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Freshness(StrEnum):
    """Computed live from `published_at` vs. the current time on every
    read (see `catalyst_classifier.classify_freshness`) — never a stored
    flag that could itself go stale, same discipline as
    `app.fundamentals.snapshot_models.CachedFundamentalSnapshot.is_fresh`."""

    BREAKING = "BREAKING"
    RECENT = "RECENT"
    STALE = "STALE"


@dataclass
class RawNewsArticle:
    """One article, already matched to a symbol by `news_matcher.py`, not
    yet classified. `published_at` is mandatory (not `datetime | None`) —
    a provider that can't produce a real publication timestamp for an
    article must drop that article, never fabricate one (e.g. "now")."""

    symbol: str
    headline: str
    summary: str
    source: str
    published_at: datetime
    article_url: str | None = None


@dataclass
class NormalizedNewsItem:
    """The 8-field normalized shape the spec requires, plus `computed_at`
    for when classification/scoring ran (distinct from `published_at`,
    when the underlying event actually happened)."""

    symbol: str
    headline: str
    source: str
    published_at: datetime
    event_type: CatalystType
    sentiment: Sentiment
    materiality: Materiality
    freshness: Freshness
    computed_at: datetime
    article_url: str | None = None
