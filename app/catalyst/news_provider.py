"""NewsProvider: the abstraction the catalyst engine depends on instead of
any specific news vendor — mirrors `app.fundamentals.provider.FundamentalDataProvider`'s
role in this codebase.

`UpstoxNewsProvider` is the only implementation: `GET /v2/news?category=instrument_keys
&instrument_keys=...` — verified live this session against the same
licensed Upstox API access already used for market data and fundamentals
(no new subscription, no scraping). Confirmed via the endpoint's own
error messages (not guessed): `category` is required, and the only
allowed values are `instrument_keys`, `positions`, `holdings` — this
provider always uses `instrument_keys` (the only one meaningful for "get
news for these specific symbols"; `positions`/`holdings` are relative to
the API caller's own brokerage account, not a symbol list, and out of
scope here).

Real response shape per instrument_key (verified live, TCS/INFY):
`heading`, `summary`, `thumbnail`, `article_link`, `published_time`
(epoch milliseconds). No separate byline/wire-service field — `source`
is reported as `"upstox"` (the licensed API this data comes through);
`article_url` (from `article_link`) is kept so the original article is
always traceable, preserving attribution even though Upstox itself
aggregates/republishes rather than being the original wire service.

Symbol<->instrument_key resolution and response matching are delegated to
`news_matcher.py` (pure, independently testable) — this module is only
the HTTP transport.
"""

from abc import ABC, abstractmethod

import httpx
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.catalyst.catalyst_models import RawNewsArticle
from app.catalyst.news_matcher import match_articles, resolve_tokens
from app.config.settings import Settings
from app.repositories.market_repository import SymbolRepository


class NewsProvider(ABC):
    name: str

    @abstractmethod
    async def get_news(self, symbols: list[str]) -> dict[str, list[RawNewsArticle]]:
        """Returns whatever news each symbol actually has, keyed by the
        symbol string. A symbol with no news, or that can't be resolved
        to an instrument the provider understands, is simply absent from
        the result — never a fabricated empty/placeholder entry standing
        in for "no data.\""""
        raise NotImplementedError


class UpstoxNewsProvider(NewsProvider):
    name = "upstox"

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._client = client or httpx.AsyncClient()

    async def get_news(self, symbols: list[str]) -> dict[str, list[RawNewsArticle]]:
        if not symbols:
            return {}

        async with self._session_factory() as session:
            symbol_by_name = {s.symbol: s for s in await SymbolRepository(session).list_active()}
        token_by_symbol = resolve_tokens(symbols, symbol_by_name)
        if not token_by_symbol:
            return {}

        token_to_symbol = {token: symbol for symbol, token in token_by_symbol.items()}
        raw_by_token = await self._fetch(list(token_to_symbol.keys()))
        return match_articles(raw_by_token, token_to_symbol)

    async def _fetch(self, instrument_tokens: list[str]) -> dict[str, list[object]]:
        """Raw call — no batch-size limit has been verified for this
        endpoint (unlike the WS subscribe batching in
        `app.providers.upstox_websocket`, discovered via live binary
        search); callers should keep symbol lists to a reasonable
        watchlist/candidate size, not the full LISTED universe, which
        would also be a nonsensical use of a news feed."""
        url = f"{self._settings.upstox_base_url}/news"
        headers = {
            "Authorization": f"Bearer {self._settings.upstox_access_token}",
            "Accept": "application/json",
        }
        params = {"category": "instrument_keys", "instrument_keys": ",".join(instrument_tokens)}
        try:
            response = await self._client.get(
                url, headers=headers, params=params, timeout=self._settings.upstox_request_timeout
            )
        except httpx.HTTPError as exc:
            logger.warning("Upstox news call failed: {error}", error=exc)
            return {}

        if response.status_code != 200:
            logger.warning("Upstox news call returned HTTP {status}", status=response.status_code)
            return {}

        try:
            body = response.json()
        except ValueError:
            return {}
        if not isinstance(body, dict) or body.get("status") != "success":
            return {}
        data = body.get("data")
        if not isinstance(data, dict):
            return {}
        return {token: articles for token, articles in data.items() if isinstance(articles, list)}
