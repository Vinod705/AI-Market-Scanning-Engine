"""Deterministic classification — event type, sentiment, freshness. No
LLM anywhere (per the phase's own instruction: an LLM must never be
responsible for basic news timestamps, stock matching, or numerical
scoring) — pure keyword-rule matching and time arithmetic, same
discipline as `app.derivatives.oi_buildup`/`app.analytics.rrg.rrg_engine`
elsewhere in this project.

**event_type**: first matching keyword group wins, checked in a fixed
priority order (a headline mentioning both "results" and "dividend" is
classified RESULTS — the quarterly announcement is the primary event; a
dividend is very often announced alongside results, so DIVIDEND is
checked after RESULTS on purpose, not merely alphabetically).

**sentiment**: simple positive/negative keyword-count majority. Ties (or
no keywords matched) are NEUTRAL — never guessed toward either side.

**freshness**: pure function of elapsed time since `published_at`, using
the configurable windows on `Settings` (see `catalyst_scorer.py` for
where those are read) — always computed live, never a stored label, so a
stale item can never be presented as breaking news just because nothing
re-checked it recently.
"""

import re
from datetime import datetime, timedelta

from app.catalyst.catalyst_models import CatalystType, Freshness, Sentiment

# Checked in this order — see module docstring for why RESULTS precedes
# DIVIDEND, and REGULATORY precedes the broader SECTOR_CATALYST bucket.
_EVENT_TYPE_KEYWORDS: list[tuple[CatalystType, list[str]]] = [
    (
        CatalystType.RESULTS,
        [
            "quarterly results",
            "q1 results",
            "q2 results",
            "q3 results",
            "q4 results",
            "net profit",
            "net loss",
            "revenue rises",
            "revenue falls",
            "beats estimates",
            "misses estimates",
            "earnings",
        ],
    ),
    (
        CatalystType.MERGER_ACQUISITION,
        [
            "acquisition",
            "acquires",
            "to acquire",
            "merger",
            "merges with",
            "stake sale",
            "stake purchase",
            "takeover",
            "amalgamation",
        ],
    ),
    (
        CatalystType.REGULATORY,
        [
            "sebi",
            "rbi",
            "regulatory",
            "penalty",
            "show cause notice",
            "investigation",
            "probe",
            "compliance order",
            "ban on",
            "cci approval",
            "competition commission",
        ],
    ),
    (
        CatalystType.ORDER_WIN,
        [
            "order win",
            "bags order",
            "wins order",
            "wins contract",
            "secures contract",
            "receives order",
            "order from",
            "contract worth",
        ],
    ),
    (
        CatalystType.BROKER_ACTION,
        [
            "upgrade",
            "downgrade",
            "target price",
            "buy rating",
            "sell rating",
            "outperform",
            "underperform",
            "initiates coverage",
            "maintains buy",
            "maintains sell",
        ],
    ),
    (
        CatalystType.DIVIDEND,
        [
            "dividend",
            "interim dividend",
            "final dividend",
            "special dividend",
            "ex-dividend",
            "record date",
        ],
    ),
    (
        CatalystType.MANAGEMENT_COMMENTARY,
        [
            "ceo says",
            "md says",
            "chairman says",
            "management said",
            "guidance",
            "outlook comment",
            "in an interview",
        ],
    ),
    (
        CatalystType.SECTOR_CATALYST,
        [
            "sector",
            "industry",
            "nifty it",
            "nifty bank",
            "nifty auto",
            "nifty pharma",
            "nifty metal",
            "peers",
            "index surges",
            "index falls",
        ],
    ),
]

_POSITIVE_KEYWORDS = [
    "surge",
    "surges",
    "jump",
    "jumps",
    "soar",
    "soars",
    "rally",
    "rallies",
    "gain",
    "gains",
    "beats estimates",
    "profit rises",
    "profit up",
    "upgrade",
    "wins",
    "bags order",
    "record high",
    "outperform",
]
_NEGATIVE_KEYWORDS = [
    "fall",
    "falls",
    "falling",
    "drop",
    "drops",
    "tumble",
    "tumbles",
    "plunge",
    "plunges",
    "decline",
    "declines",
    "loss",
    "misses estimates",
    "profit falls",
    "downgrade",
    "penalty",
    "probe",
    "investigation",
    "underperform",
    "sell-off",
]


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(re.search(re.escape(keyword), text) for keyword in keywords)


def classify_event_type(headline: str, summary: str = "") -> CatalystType:
    text = f"{headline} {summary}".lower()
    for event_type, keywords in _EVENT_TYPE_KEYWORDS:
        if _contains_any(text, keywords):
            return event_type
    return CatalystType.OTHER


def classify_sentiment(headline: str, summary: str = "") -> Sentiment:
    text = f"{headline} {summary}".lower()
    positive_hits = sum(1 for kw in _POSITIVE_KEYWORDS if kw in text)
    negative_hits = sum(1 for kw in _NEGATIVE_KEYWORDS if kw in text)
    if positive_hits > negative_hits:
        return Sentiment.POSITIVE
    if negative_hits > positive_hits:
        return Sentiment.NEGATIVE
    return Sentiment.NEUTRAL


def classify_freshness(
    published_at: datetime,
    *,
    now: datetime,
    breaking_window: timedelta,
    stale_window: timedelta,
) -> Freshness:
    """`published_at` in the future (clock skew) is treated as BREAKING,
    not an error — never negative-age arithmetic feeding a wrong bucket."""
    age = max(timedelta(0), now - published_at)
    if age <= breaking_window:
        return Freshness.BREAKING
    if age <= stale_window:
        return Freshness.RECENT
    return Freshness.STALE
