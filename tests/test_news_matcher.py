"""Tests for app.catalyst.news_matcher — pure matching logic, no I/O."""

from app.catalyst.news_matcher import match_articles, resolve_tokens
from app.models.symbol import Symbol

_TCS = Symbol(id=1, symbol="TCS", exchange="NSE", instrument_token="NSE_EQ|INE467B01029")
_INFY = Symbol(id=2, symbol="INFY", exchange="NSE", instrument_token="NSE_EQ|INE009A01021")


def test_resolve_tokens_maps_known_symbols() -> None:
    tokens = resolve_tokens(["TCS", "INFY"], {"TCS": _TCS, "INFY": _INFY})
    assert tokens == {"TCS": "NSE_EQ|INE467B01029", "INFY": "NSE_EQ|INE009A01021"}


def test_resolve_tokens_drops_unknown_symbol() -> None:
    tokens = resolve_tokens(["TCS", "NOSUCHSYMBOL"], {"TCS": _TCS})
    assert tokens == {"TCS": "NSE_EQ|INE467B01029"}


def test_resolve_tokens_empty_input() -> None:
    assert resolve_tokens([], {"TCS": _TCS}) == {}


_REAL_ARTICLE = {
    "heading": "TCS Q1 results: net profit rises 12%",
    "summary": "TCS reported strong quarterly results.",
    "article_link": "https://upstox.com/news/example",
    "published_time": 1786609988357,
}


def test_match_articles_produces_normalized_symbol_keyed_result() -> None:
    raw_by_token = {"NSE_EQ|INE467B01029": [_REAL_ARTICLE]}
    result = match_articles(raw_by_token, {"NSE_EQ|INE467B01029": "TCS"})

    assert list(result.keys()) == ["TCS"]
    assert len(result["TCS"]) == 1
    article = result["TCS"][0]
    assert article.symbol == "TCS"
    assert article.headline == _REAL_ARTICLE["heading"]
    assert article.source == "upstox"
    assert article.article_url == _REAL_ARTICLE["article_link"]


def test_match_articles_drops_unrecognized_instrument_key() -> None:
    """A key in the response that wasn't in token_to_symbol (e.g. Upstox
    returning something unexpected) is dropped, never guessed."""
    raw_by_token = {"NSE_EQ|UNKNOWN": [_REAL_ARTICLE]}
    result = match_articles(raw_by_token, {"NSE_EQ|INE467B01029": "TCS"})
    assert result == {}


def test_match_articles_drops_item_missing_published_time() -> None:
    bad_article = {**_REAL_ARTICLE}
    del bad_article["published_time"]
    raw_by_token = {"NSE_EQ|INE467B01029": [bad_article]}
    result = match_articles(raw_by_token, {"NSE_EQ|INE467B01029": "TCS"})
    assert result == {}


def test_match_articles_drops_item_missing_heading() -> None:
    bad_article = {**_REAL_ARTICLE}
    del bad_article["heading"]
    raw_by_token = {"NSE_EQ|INE467B01029": [bad_article]}
    result = match_articles(raw_by_token, {"NSE_EQ|INE467B01029": "TCS"})
    assert result == {}


def test_match_articles_one_article_can_appear_under_multiple_symbols() -> None:
    """Mirrors what Upstox itself does live: one article mentioning
    several companies is returned under each company's own key — this
    matcher doesn't second-guess that, only maps keys back to symbols."""
    raw_by_token = {
        "NSE_EQ|INE467B01029": [_REAL_ARTICLE],
        "NSE_EQ|INE009A01021": [_REAL_ARTICLE],
    }
    result = match_articles(
        raw_by_token, {"NSE_EQ|INE467B01029": "TCS", "NSE_EQ|INE009A01021": "INFY"}
    )
    assert set(result.keys()) == {"TCS", "INFY"}
    assert result["TCS"][0].symbol == "TCS"
    assert result["INFY"][0].symbol == "INFY"


def test_match_articles_empty_input() -> None:
    assert match_articles({}, {}) == {}
