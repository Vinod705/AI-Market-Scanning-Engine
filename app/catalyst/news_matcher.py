"""Matches raw news payloads to the correct stock — pure, deterministic,
no I/O, no LLM. Upstox's `/v2/news` endpoint already keys articles by
`instrument_key` (it does the underlying relevance-matching server-side,
verified live: a single article about several companies is returned
under each mentioned company's own key) — this module's job is narrower
but still real: resolve a requested symbol to the instrument_key Upstox
needs, and, on the way back, refuse to trust any key in the response that
doesn't map to a symbol we actually asked about or know. An unrecognized
key is dropped, never guessed into "probably meant this symbol."
"""

from datetime import UTC, datetime

from app.catalyst.catalyst_models import RawNewsArticle
from app.models.symbol import Symbol


def resolve_tokens(symbols: list[str], symbol_by_name: dict[str, Symbol]) -> dict[str, str]:
    """`symbol -> instrument_token` for whichever requested symbols are
    actually known — an unknown symbol is silently absent from the
    result (the caller has nothing to ask Upstox for), not an error."""
    return {
        symbol: symbol_by_name[symbol].instrument_token
        for symbol in symbols
        if symbol in symbol_by_name
    }


def match_articles(
    raw_by_token: dict[str, list[object]], token_to_symbol: dict[str, str]
) -> dict[str, list[RawNewsArticle]]:
    """Turns Upstox's `{instrument_key: [raw_item, ...]}` response into
    `{symbol: [RawNewsArticle, ...]}`, using only `token_to_symbol` (built
    from our own `symbols` table via `resolve_tokens`) to decide what a
    key means — never Upstox's own labeling of the article, which this
    project has no way to independently verify."""
    results: dict[str, list[RawNewsArticle]] = {}
    for token, raw_items in raw_by_token.items():
        symbol = token_to_symbol.get(token)
        if symbol is None:
            continue  # a key we didn't ask about / can't resolve — never guessed
        parsed = [
            article
            for item in raw_items
            if (article := _parse_article(symbol, item)) is not None
        ]
        if parsed:
            results[symbol] = parsed
    return results


def _parse_article(symbol: str, item: object) -> RawNewsArticle | None:
    """`published_at` is mandatory — an item with no valid
    `published_time` is dropped here, never passed through with a
    fabricated timestamp (e.g. "now")."""
    if not isinstance(item, dict):
        return None
    heading = item.get("heading")
    published_time = item.get("published_time")
    if not isinstance(heading, str) or not heading:
        return None
    if not isinstance(published_time, int | float):
        return None

    summary = item.get("summary")
    article_link = item.get("article_link")
    return RawNewsArticle(
        symbol=symbol,
        headline=heading,
        summary=summary if isinstance(summary, str) else "",
        source="upstox",
        published_at=datetime.fromtimestamp(published_time / 1000, tz=UTC),
        article_url=article_link if isinstance(article_link, str) else None,
    )
