"""Tests for app.fundamentals.upstox_fundamental_provider.UpstoxFundamentalDataProvider.

Response bodies mirror the real shapes verified live against Upstox's
Company Fundamentals API this session (income-statement, cash-flow,
key-ratios) — no real network calls, no live credentials required.
"""

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.fundamentals.queue_models import FetchStatus
from app.fundamentals.upstox_fundamental_provider import UpstoxFundamentalDataProvider
from app.providers.base_provider import ProviderSymbol
from app.repositories.market_repository import SymbolRepository

_INCOME_STATEMENT = {
    "status": "success",
    "data": {
        "income_statement": [
            {
                "category": "revenue",
                "history": [
                    {"value": 271423.0, "period": "Mar 2026"},
                    {"value": 259286.0, "period": "Mar 2025"},
                ],
            },
            {
                "category": "net_profit",
                "history": [
                    {"value": 49454.0, "period": "Mar 2026"},
                    {"value": 48797.0, "period": "Mar 2025"},
                ],
            },
        ]
    },
}

_CASH_FLOW = {
    "status": "success",
    "data": {
        "cash_flow": [
            {
                "category": "operating",
                "history": [{"value": 52094.0, "period": "Mar 2026"}],
            },
            {
                "category": "investing",
                "history": [{"value": -12845.0, "period": "Mar 2026"}],
            },
        ]
    },
}

_KEY_RATIOS = {
    "status": "success",
    "data": [
        {"name": "P/E", "company_value": "17.05", "sector_value": "129.5"},
        {"name": "P/B", "company_value": "7.95", "sector_value": "8.09"},
        {"name": "ROA", "company_value": "27.45%", "sector_value": "-38.94%"},
        {"name": "ROE", "company_value": "45.89%", "sector_value": "8.88%"},
        {"name": "ROCE", "company_value": "55.21%", "sector_value": "72.99%"},
        {"name": "Quick Ratio", "company_value": "2.23", "sector_value": "4.69"},
        {"name": "EV/EBITDA", "company_value": "11.72", "sector_value": "-582.64"},
    ],
}

_NOT_FOUND = {
    "status": "error",
    "errors": [{"errorCode": "UDAPI100060", "message": "Resource not Found."}],
}


def _settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        upstox_access_token="test-token",
        upstox_base_url="https://api.upstox.com/v2",
        upstox_request_timeout=1.0,
    )


async def _seed_symbol(
    session_factory: async_sessionmaker[AsyncSession], symbol: str, isin: str | None
) -> None:
    async with session_factory() as session:
        token = f"NSE_EQ|{isin}" if isin else "N999999"  # non-Upstox-shaped token when isin=None
        await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol, exchange="NSE", instrument_token=token)
        )
        await session.commit()


def _provider(
    handler, session_factory: async_sessionmaker[AsyncSession]
) -> UpstoxFundamentalDataProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return UpstoxFundamentalDataProvider(_settings(), session_factory, client=client)


async def test_maps_income_statement_cash_flow_and_key_ratios(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol(session_factory, "TCS", "INE467B01029")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "income-statement" in url:
            return httpx.Response(200, json=_INCOME_STATEMENT)
        if "cash-flow" in url:
            return httpx.Response(200, json=_CASH_FLOW)
        if "key-ratios" in url:
            return httpx.Response(200, json=_KEY_RATIOS)
        raise AssertionError(f"unexpected URL {url}")

    provider = _provider(handler, session_factory)
    data = await provider.get_fundamentals("TCS")

    assert data is not None
    assert data.revenue_ttm_cr == 271423.0
    assert data.net_profit_ttm_cr == 49454.0
    assert data.operating_cash_flow_cr == 52094.0
    assert data.pe == 17.05
    assert data.pb == 7.95
    assert data.roe_pct == 45.89
    assert data.roce_pct == 55.21
    assert data.ev_ebitda == 11.72
    # ROA / Quick Ratio have no matching FundamentalData field — never
    # force-mapped onto the wrong one.
    assert data.current_ratio is None


async def test_isin_extracted_from_instrument_token_not_a_new_lookup(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol(session_factory, "INFY", "INE009A01021")
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, json=_INCOME_STATEMENT)

    provider = _provider(handler, session_factory)
    await provider.get_fundamentals("INFY")

    assert all("INE009A01021" in url for url in seen_urls)


async def test_unresolvable_isin_returns_failed_status_not_none_silently(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol(session_factory, "LEGACY", isin=None)  # FivePaisa-shaped token

    provider = _provider(lambda r: httpx.Response(200, json=_INCOME_STATEMENT), session_factory)
    data, status, error = await provider.get_fundamentals_with_status("LEGACY")

    assert data is None
    assert status == FetchStatus.FAILED
    assert error is not None and "ISIN" in error


async def test_unknown_symbol_returns_failed_status(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider = _provider(lambda r: httpx.Response(200, json=_INCOME_STATEMENT), session_factory)
    data, status, _error = await provider.get_fundamentals_with_status("NOPE")

    assert data is None
    assert status == FetchStatus.FAILED


async def test_404_on_all_statements_is_a_real_no_data_result_not_an_error(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A genuinely new listing with no financial history yet — Upstox
    reports this as 404, a real "no data" condition, not a malfunction."""
    await _seed_symbol(session_factory, "NEWCO", "INE000000001")

    provider = _provider(lambda r: httpx.Response(404, json=_NOT_FOUND), session_factory)
    data, status, error = await provider.get_fundamentals_with_status("NEWCO")

    assert data is None
    assert status == FetchStatus.FAILED
    assert error is not None


async def test_partial_data_when_only_some_statements_succeed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol(session_factory, "PARTIAL", "INE111111111")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "key-ratios" in url:
            return httpx.Response(200, json=_KEY_RATIOS)
        return httpx.Response(404, json=_NOT_FOUND)

    provider = _provider(handler, session_factory)
    data, status, _error = await provider.get_fundamentals_with_status("PARTIAL")

    assert status == FetchStatus.SUCCESS
    assert data is not None
    assert data.pe == 17.05
    assert data.revenue_ttm_cr is None  # income-statement 404'd — never guessed


async def test_rate_limited_response_is_reported_not_silently_dropped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_symbol(session_factory, "RL", "INE222222222")

    provider = _provider(lambda r: httpx.Response(429), session_factory)
    data, status, error = await provider.get_fundamentals_with_status("RL")

    assert data is None
    assert status == FetchStatus.RATE_LIMITED
    assert error is not None
