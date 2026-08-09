"""Tests for the Trendlyne MCP client and fundamental-data provider.

Uses httpx.MockTransport to simulate the Trendlyne MCP server's SSE-framed
responses — no real network calls, no live token required. The sample
response text mirrors the shape observed from a real, live call during
Phase 7 discovery (see app/fundamentals/trendlyne_provider.py's docstring).
"""

import httpx
import pytest

from app.config.settings import Settings
from app.fundamentals.trendlyne_mcp_client import TrendlyneMcpClient, TrendlyneMcpError
from app.fundamentals.trendlyne_provider import (
    TrendlyneFundamentalDataProvider,
    parse_overview_text,
)

_SAMPLE_OVERVIEW_TEXT = """technicalData:
  name | value | color | st | lt | unit | unique_name
  Relative Strength Index | 62.8 | neutral | RSI is mid-range | RSI is 62.8 | abs | rsi
asmData:
  url:
  lt:
summaryData:
  ["Type","Holding","holdingId"], ["Promoter",71.77,150050890], ["FII",9.07,213958618], ["Other Institutions",7.79,null], ["Public",5.69,150051026], ["Mutual Funds",5.68,213958600]
shBarChartData:
  Promoter:
    ["Quarter","Promoter Holding (%)"], ["Jun 2026",71.8,"71.8 %"]
fundamentalData:
  name | value | color | st | lt | unit | title | unique_name
  Market Cap | 858572.2 | positive | Market Leader | text | Cr. | Market Capitalization | MCAP_Q
  Price to Earnings | 17.2 | neutral | Below industry Median | text |  | PE TTM | PE_TTM
  Price to Book Value Adjusted | 8.0 | negative | High in industry | text |  | Price to Book | PBV_A
  Dividend Yield | 4.7 | positive | High in industry | text |  | Dividend yield 1yr % | DIVIDEND_YIELD_1_YR
  PE to Growth | 16.2 | negative | High in industry | text |  | PEG TTM | PEG_TTM
  Op Revenue TTM | 275859.0 | positive | 7.7% incr | text | Cr | Operating Revenue TTM | SR_TTM
  Net Profit TTM | 49799.0 | neutral | 1.1% incr | text | Cr | Net profit TTM | NP_TTM
  Cash From Operating Activity | 52094.0 | positive | 6.5% incr | text | Cr | Cash from Operating Activity Annual | CFO_A
  Return on Equity % | 45.88 | negative | 10% decr | text |  | ROE Annual % | ROE_A
  Some Unavailable Metric | None | neutral | n/a | text |  | title | UNMAPPED_NONE
SWOTData:
  tableHeaders:
    unique_name | type | name
"""


def _sse_body(request_id: int, text: str, *, is_error: bool = False) -> str:
    import json

    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"content": [{"type": "text", "text": text}], "isError": is_error},
    }
    return f"event: message\ndata: {json.dumps(payload)}\n\n"


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = dict(
        trendlyne_mcp_url="https://mcp.trendlyne.com/mcp?token=test-token",
        trendlyne_mcp_request_timeout=1.0,
        fundamental_cache_ttl_minutes=240,
    )
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _client(handler) -> TrendlyneMcpClient:
    transport = httpx.MockTransport(handler)
    return TrendlyneMcpClient(
        mcp_url="https://mcp.trendlyne.com/mcp?token=test-token",
        timeout_seconds=1.0,
        client=httpx.AsyncClient(transport=transport),
    )


# --- parse_overview_text (pure function) --------------------------------


def test_parse_overview_text_maps_known_fields() -> None:
    data = parse_overview_text("TCS", _SAMPLE_OVERVIEW_TEXT)
    assert data.market_cap_cr == 858572.2
    assert data.pe == 17.2
    assert data.pb == 8.0
    assert data.dividend_yield_pct == 4.7
    assert data.peg == 16.2
    assert data.revenue_ttm_cr == 275859.0
    assert data.net_profit_ttm_cr == 49799.0
    assert data.operating_cash_flow_cr == 52094.0
    assert data.roe_pct == 45.88


def test_parse_overview_text_maps_shareholding() -> None:
    data = parse_overview_text("TCS", _SAMPLE_OVERVIEW_TEXT)
    assert data.promoter_holding_pct == 71.77
    assert data.fii_holding_pct == 9.07


def test_parse_overview_text_leaves_unmapped_fields_unknown() -> None:
    data = parse_overview_text("TCS", _SAMPLE_OVERVIEW_TEXT)
    # Not present in the fixed FUNDAMENTAL_FIELD_MAP or the response at all.
    assert data.roce_pct is None
    assert data.debt_to_equity is None
    assert data.ev_ebitda is None


def test_parse_overview_text_treats_none_value_as_unknown_not_zero() -> None:
    """The Trendlyne row with value 'None' (UNMAPPED_NONE) must never
    become a fabricated 0.0 — it's simply not in the parsed dict."""
    data = parse_overview_text("TCS", _SAMPLE_OVERVIEW_TEXT)
    # No FundamentalData field maps to UNMAPPED_NONE, so this is really
    # asserting the parser didn't choke on the "None" row.
    assert data.symbol == "TCS"


def test_parse_overview_text_handles_missing_sections_gracefully() -> None:
    data = parse_overview_text("NEWCO", "some unexpected format with no known sections")
    assert data.symbol == "NEWCO"
    assert data.pe is None
    assert data.promoter_holding_pct is None


# --- TrendlyneMcpClient ---------------------------------------------------


async def test_call_tool_parses_sse_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text=_sse_body(1, "hello world"), headers={"Content-Type": "text/event-stream"}
        )

    client = _client(handler)
    text = await client.call_tool(
        "get_overview_news_corp_events", {"stock_code": "TCS", "type": "overview"}
    )
    assert text == "hello world"


async def test_call_tool_raises_on_http_error_status() -> None:
    client = _client(lambda r: httpx.Response(403, text="Forbidden"))
    with pytest.raises(TrendlyneMcpError):
        await client.call_tool("get_overview_news_corp_events", {"stock_code": "TCS"})


async def test_call_tool_raises_on_is_error_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_sse_body(1, "bad stock code", is_error=True))

    client = _client(handler)
    with pytest.raises(TrendlyneMcpError):
        await client.call_tool("get_overview_news_corp_events", {"stock_code": "BOGUS"})


async def test_call_tool_raises_on_malformed_body() -> None:
    client = _client(lambda r: httpx.Response(200, text="not an sse response at all"))
    with pytest.raises(TrendlyneMcpError):
        await client.call_tool("get_overview_news_corp_events", {"stock_code": "TCS"})


async def test_call_tool_raises_on_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    client = _client(handler)
    with pytest.raises(TrendlyneMcpError):
        await client.call_tool("get_overview_news_corp_events", {"stock_code": "TCS"})


async def test_health_check_true_on_valid_tools_list() -> None:
    import json

    def handler(request: httpx.Request) -> httpx.Response:
        payload = {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}
        return httpx.Response(200, text=f"event: message\ndata: {json.dumps(payload)}\n\n")

    client = _client(handler)
    assert await client.health_check() is True


async def test_health_check_false_on_error() -> None:
    client = _client(lambda r: httpx.Response(500))
    assert await client.health_check() is False


# --- TrendlyneFundamentalDataProvider ------------------------------------


def _provider(handler, settings: Settings | None = None) -> TrendlyneFundamentalDataProvider:
    mcp_client = _client(handler)
    return TrendlyneFundamentalDataProvider(settings or _settings(), client=mcp_client)


async def test_get_fundamentals_returns_parsed_data() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_sse_body(1, _SAMPLE_OVERVIEW_TEXT))

    provider = _provider(handler)
    data = await provider.get_fundamentals("TCS")

    assert data is not None
    assert data.pe == 17.2
    assert data.roe_pct == 45.88


async def test_get_fundamentals_returns_none_on_mcp_failure() -> None:
    provider = _provider(lambda r: httpx.Response(503))
    data = await provider.get_fundamentals("TCS")
    assert data is None


async def test_get_fundamentals_caches_within_ttl() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, text=_sse_body(1, _SAMPLE_OVERVIEW_TEXT))

    provider = _provider(handler, _settings(fundamental_cache_ttl_minutes=240))
    await provider.get_fundamentals("TCS")
    await provider.get_fundamentals("TCS")

    assert call_count == 1


async def test_get_fundamentals_does_not_cache_failures() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(503)

    provider = _provider(handler)
    await provider.get_fundamentals("TCS")
    await provider.get_fundamentals("TCS")

    assert call_count == 2


async def test_health_check_reflects_mcp_status() -> None:
    import json

    def handler(request: httpx.Request) -> httpx.Response:
        payload = {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}
        return httpx.Response(200, text=f"event: message\ndata: {json.dumps(payload)}\n\n")

    provider = _provider(handler)
    assert await provider.health_check() is True
