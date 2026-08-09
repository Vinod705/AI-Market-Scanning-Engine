"""Tests for the Trendlyne MCP client and fundamental-data provider.

Uses httpx.MockTransport to simulate the Trendlyne MCP server's SSE-framed
responses — no real network calls, no live token required. The sample
response text mirrors the shape observed from a real, live call during
Phase 7 discovery (see app/fundamentals/trendlyne_provider.py's docstring).
"""

import httpx
import pytest

from app.config.settings import Settings
from app.fundamentals.models import FundamentalData
from app.fundamentals.trendlyne_mcp_client import TrendlyneMcpClient, TrendlyneMcpError
from app.fundamentals.trendlyne_provider import (
    TrendlyneFundamentalDataProvider,
    apply_shareholding_text,
    apply_targeted_multi_stock_text,
    parse_overview_text,
)

_SAMPLE_SHAREHOLDING_TEXT = """summaryData:
  ["Type","Holding","holdingId"], ["Promoter",50.48,150078587], ["FII",17.19,213859411], ["Other Institutions",11.16,null], ["Public",11.05,150078632], ["Mutual Funds",10.11,213859397]
chartData:
  Promoter:
    ["Quarter","Promoter Holding (%)",{"role":"annotation"},"Pledges as % of promoter shares (%)",{"role":"annotation"}], ["Mar 2026",50.0,"50.0 %",0.0,"0.0 %"], ["Jun 2026",50.5,"50.5 %",1.5,"1.5 %"]
  DII:
    ["Quarter","Holding (%)",{"role":"annotation"}], ["Mar 2026",20.6,"20.6 %"], ["Jun 2026",21.3,"21.3 %"]
insights:
  Promoter:
    ["note","text","positive",2]
"""


def _multi_stock_response_text(content: str) -> str:
    """Mirrors the real, live-observed double-JSON-encoding: the tool's
    text content is `{"markdown_data": "<content, itself JSON-encoded>"}`."""
    import json

    return json.dumps({"markdown_data": json.dumps(content)})


_SAMPLE_MULTI_STOCK_CONTENT = (
    "1127|Reliance Industries|RELIANCE|500325|2026-08-07\n"
    "1118|Reliance Comm|RCOM|532712|2026-08-07\n"
    "\n"
    "ROCE Ann. %\nRELIANCE:9.17\nRCOM:0.10\n"
    "---\n"
    "ROCE Ann. 1Y Ago %\nRELIANCE:8.70\nRCOM:0.05\n"
    "---\n"
    "Total Debt to Total Equity Ann.\nRELIANCE:0.41\nRCOM:-0.46\n"
    "---\n"
    "Non Interest Income Ann. %\nRELIANCE:None\nRCOM:None\n"
    "---"
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


# --- apply_shareholding_text (pure function) -----------------------------


def test_apply_shareholding_text_sets_dii_and_pledge() -> None:
    data = FundamentalData(symbol="RELIANCE")
    apply_shareholding_text(data, _SAMPLE_SHAREHOLDING_TEXT)
    assert data.dii_holding_pct == 21.3
    assert data.promoter_pledge_pct == 1.5
    snap = data.field_snapshots["promoter_pledge_pct"]
    assert snap.source == "Trendlyne"
    assert snap.period == "Jun 2026"


def test_apply_shareholding_text_fills_promoter_fii_only_if_missing() -> None:
    data = FundamentalData(
        symbol="RELIANCE", promoter_holding_pct=99.0
    )  # pretend overview already set it
    apply_shareholding_text(data, _SAMPLE_SHAREHOLDING_TEXT)
    assert data.promoter_holding_pct == 99.0  # not overwritten
    assert data.fii_holding_pct == 17.19  # was None, now filled


def test_apply_shareholding_text_handles_missing_sections_gracefully() -> None:
    data = FundamentalData(symbol="NEWCO")
    apply_shareholding_text(data, "unexpected format")
    assert data.dii_holding_pct is None
    assert data.promoter_pledge_pct is None


# --- apply_targeted_multi_stock_text (pure function) ----------------------


def test_apply_targeted_multi_stock_extracts_exact_symbol_and_label() -> None:
    data = FundamentalData(symbol="RELIANCE")
    text = _multi_stock_response_text(_SAMPLE_MULTI_STOCK_CONTENT)
    apply_targeted_multi_stock_text(data, "RELIANCE", text)
    assert data.roce_pct == 9.17  # current, not the "1Y Ago" variant
    assert data.debt_to_equity == 0.41


def test_apply_targeted_multi_stock_ignores_peer_company_values() -> None:
    data = FundamentalData(symbol="RELIANCE")
    text = _multi_stock_response_text(_SAMPLE_MULTI_STOCK_CONTENT)
    apply_targeted_multi_stock_text(data, "RELIANCE", text)
    # RCOM's 0.10/-0.46 must never leak into a RELIANCE-requested lookup.
    assert data.roce_pct != 0.10
    assert data.debt_to_equity != -0.46


def test_apply_targeted_multi_stock_skips_unresolved_symbol() -> None:
    """If the requested symbol's NSE code never appears in the header
    block, nothing in the response can be trusted as belonging to it."""
    data = FundamentalData(symbol="UNKNOWNCO")
    text = _multi_stock_response_text(_SAMPLE_MULTI_STOCK_CONTENT)
    apply_targeted_multi_stock_text(data, "UNKNOWNCO", text)
    assert data.roce_pct is None
    assert data.debt_to_equity is None


def test_apply_targeted_multi_stock_never_maps_non_exact_label() -> None:
    """ "ROCE Ann. 1Y Ago %" must never be mistaken for "ROCE Ann. %"."""
    content = "1127|Reliance Industries|RELIANCE|500325|2026-08-07\n\nROCE Ann. 1Y Ago %\nRELIANCE:8.70\n---"
    data = FundamentalData(symbol="RELIANCE")
    apply_targeted_multi_stock_text(data, "RELIANCE", _multi_stock_response_text(content))
    assert data.roce_pct is None


def test_apply_targeted_multi_stock_treats_none_value_as_unknown() -> None:
    data = FundamentalData(symbol="RELIANCE")
    text = _multi_stock_response_text(_SAMPLE_MULTI_STOCK_CONTENT)
    apply_targeted_multi_stock_text(data, "RELIANCE", text)
    # "Non Interest Income Ann. %" isn't in the mapped fields at all, and
    # even if it were, RELIANCE's value there is "None" — must never
    # become a fabricated field.
    assert not hasattr(data, "non_interest_income")


def test_apply_targeted_multi_stock_handles_malformed_response_gracefully() -> None:
    data = FundamentalData(symbol="RELIANCE")
    apply_targeted_multi_stock_text(data, "RELIANCE", "not valid json at all")
    assert data.roce_pct is None


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

    # 3 calls (overview + shareholding + targeted multi_stock) for the
    # first fetch; the second is served entirely from cache.
    assert call_count == 3


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


async def test_get_fundamentals_combines_all_three_calls() -> None:
    """Overview + shareholding + targeted multi_stock, routed by tool name
    — the real end-to-end shape once deployed."""
    import json

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        tool = body["params"]["name"]
        if tool == "get_overview_news_corp_events":
            return httpx.Response(200, text=_sse_body(1, _SAMPLE_OVERVIEW_TEXT))
        if tool == "get_ownership_deals_insider_sast":
            return httpx.Response(200, text=_sse_body(2, _SAMPLE_SHAREHOLDING_TEXT))
        if tool == "get_parameter_values_multi_stock":
            multi_text = _multi_stock_response_text(_SAMPLE_MULTI_STOCK_CONTENT)
            return httpx.Response(200, text=_sse_body(3, multi_text))
        return httpx.Response(500)

    provider = _provider(handler)
    data = await provider.get_fundamentals("RELIANCE")

    assert data is not None
    assert data.pe == 17.2  # from overview
    assert data.dii_holding_pct == 21.3  # from shareholding
    assert data.promoter_pledge_pct == 1.5  # from shareholding
    assert data.roce_pct == 9.17  # from targeted multi_stock
    assert data.debt_to_equity == 0.41  # from targeted multi_stock
    assert data.field_snapshots["roce_pct"].source == "Trendlyne"


async def test_get_fundamentals_survives_shareholding_and_multi_stock_failure() -> None:
    """Overview succeeds; the other two calls fail — must still return the
    overview-sourced data rather than None."""
    import json

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        tool = body["params"]["name"]
        if tool == "get_overview_news_corp_events":
            return httpx.Response(200, text=_sse_body(1, _SAMPLE_OVERVIEW_TEXT))
        return httpx.Response(503)

    provider = _provider(handler)
    data = await provider.get_fundamentals("RELIANCE")

    assert data is not None
    assert data.pe == 17.2
    assert data.roce_pct is None
    assert data.dii_holding_pct is None
