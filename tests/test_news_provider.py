"""Tests for app.catalyst.news_provider.UpstoxNewsProvider.

Response bodies mirror the real shape verified live against Upstox's
`/v2/news?category=instrument_keys` endpoint this session — no real
network calls, no live credentials required.
"""

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.catalyst.news_provider import UpstoxNewsProvider
from app.config.settings import Settings
from app.providers.base_provider import ProviderSymbol
from app.repositories.market_repository import SymbolRepository

_REAL_NEWS_RESPONSE = {
    "status": "success",
    "data": {
        "NSE_EQ|INE467B01029": [
            {
                "heading": "TCS tumbles 4% among top laggards",
                "summary": "TCS shares declined amid broader market weakness.",
                "thumbnail": "https://assets.upstox.com/content/x.webp",
                "article_link": "https://upstox.com/news/market-news/stocks/tcs-tumbles/article-1/",
                "published_time": 1786533002675,
            }
        ]
    },
}


def _settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        upstox_access_token="test-token",
        upstox_base_url="https://api.upstox.com/v2",
        upstox_request_timeout=1.0,
    )


async def _seed_symbol(session_factory: async_sessionmaker[AsyncSession], symbol: str) -> None:
    async with session_factory() as session:
        await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol, exchange="NSE", instrument_token="NSE_EQ|INE467B01029")
        )
        await session.commit()


def _provider(handler, session_factory: async_sessionmaker[AsyncSession]) -> UpstoxNewsProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return UpstoxNewsProvider(_settings(), session_factory, client=client)


async def test_get_news_returns_matched_articles_for_known_symbol(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol(session_factory, "TCS")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("category") == "instrument_keys"
        assert request.url.params.get("instrument_keys") == "NSE_EQ|INE467B01029"
        return httpx.Response(200, json=_REAL_NEWS_RESPONSE)

    provider = _provider(handler, session_factory)
    result = await provider.get_news(["TCS"])

    assert list(result.keys()) == ["TCS"]
    assert result["TCS"][0].headline == "TCS tumbles 4% among top laggards"
    assert result["TCS"][0].source == "upstox"


async def test_get_news_empty_symbol_list_makes_no_request(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should never be called for an empty symbol list")

    provider = _provider(handler, session_factory)
    result = await provider.get_news([])
    assert result == {}


async def test_get_news_unknown_symbol_makes_no_request(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should never be called when no symbol resolves")

    provider = _provider(handler, session_factory)
    result = await provider.get_news(["NOSUCHSYMBOL"])
    assert result == {}


async def test_get_news_handles_http_error_gracefully(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol(session_factory, "TCS")

    provider = _provider(lambda r: httpx.Response(500), session_factory)
    result = await provider.get_news(["TCS"])
    assert result == {}


async def test_get_news_handles_malformed_response_gracefully(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol(session_factory, "TCS")

    provider = _provider(
        lambda r: httpx.Response(200, content=b"not json"), session_factory
    )
    result = await provider.get_news(["TCS"])
    assert result == {}
