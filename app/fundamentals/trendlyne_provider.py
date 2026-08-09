"""TrendlyneFundamentalDataProvider: the first real (non-Unavailable)
`FundamentalDataProvider` implementation, added in Phase 7.

Uses `get_overview_news_corp_events(stock_code, type="overview")` — one of
five tools discovered live on Trendlyne's MCP server — deliberately, not
`get_parameter_values_multi_stock`. The latter is a semantic/RAG-style
search tool: live testing showed it can return peer-company data the query
didn't ask for and has no fixed field set, which is a poor foundation for
a deterministic, auditable scorer. `get_overview_news_corp_events` returns
a fixed-shape, single-stock response instead, and only its two most
reliably-structured sections are parsed here:

- `fundamentalData`: a pipe-delimited table keyed by a stable `unique_name`
  column (e.g. `PE_TTM`, `ROE_A`, `CFO_A`) — every field mapped below comes
  from this table.
- `summaryData`: a single shareholding-percentage list (Promoter/FII/...).

Two further sections Trendlyne returns (`shBarChartData`'s per-quarter
promoter-pledge series, `technicalData`) are NOT parsed in this pass —
their nesting is more complex to parse reliably, and getting a pledge
percentage wrong would be worse than reporting it as UNKNOWN. This is a
documented, deliberate scope limit, not an oversight — see the Phase 7
final report.

Only fields the source actually returned are ever set; anything absent
stays `None`, which `FundamentalScorer` already treats as UNKNOWN, never
as zero (see `app.fundamentals.scorer`).
"""

import ast
import re
import time
from dataclasses import dataclass

from loguru import logger

from app.config.settings import Settings
from app.fundamentals.models import FundamentalData
from app.fundamentals.provider import FundamentalDataProvider
from app.fundamentals.trendlyne_mcp_client import TrendlyneMcpClient, TrendlyneMcpError

# fundamentalData unique_name -> FundamentalData attribute. Only fields with
# a clear, unambiguous single-stock meaning are mapped — see module
# docstring for what was deliberately left out.
_FUNDAMENTAL_FIELD_MAP: dict[str, str] = {
    "MCAP_Q": "market_cap_cr",
    "PE_TTM": "pe",
    "PBV_A": "pb",
    "PEG_TTM": "peg",
    "DIVIDEND_YIELD_1_YR": "dividend_yield_pct",
    "SR_TTM": "revenue_ttm_cr",
    "NP_TTM": "net_profit_ttm_cr",
    "CFO_A": "operating_cash_flow_cr",
    "ROE_A": "roe_pct",
}

_SUMMARY_FIELD_MAP: dict[str, str] = {
    "Promoter": "promoter_holding_pct",
    "FII": "fii_holding_pct",
}

_SECTION_HEADER_RE = re.compile(r"^([A-Za-z][A-Za-z0-9]*):$", re.MULTILINE)


def _extract_section(text: str, section_name: str) -> str | None:
    """Returns the raw text between `{section_name}:` and the next
    top-level section header (or end of string), or None if not present."""
    match = re.search(rf"^{section_name}:$", text, re.MULTILINE)
    if match is None:
        return None
    start = match.end()
    next_header = _SECTION_HEADER_RE.search(text, pos=start)
    end = next_header.start() if next_header else len(text)
    return text[start:end]


def _parse_fundamental_table(section: str) -> dict[str, float]:
    """Parses the `name | value | ... | unique_name` pipe table into
    {unique_name: value}. Rows with a non-numeric value (Trendlyne reports
    these as the literal string "None") are skipped, not zeroed."""
    values: dict[str, float] = {}
    lines = [line.strip() for line in section.strip().splitlines() if line.strip()]
    for line in lines[1:]:  # lines[0] is the header row
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue
        raw_value, unique_name = parts[1], parts[-1]
        try:
            values[unique_name] = float(raw_value)
        except ValueError:
            continue
    return values


def _parse_summary_holdings(section: str) -> dict[str, float]:
    """Parses the `["Type","Holding","holdingId"], ["Promoter",71.77,...], ...`
    line into {type_name: holding_pct}."""
    content = section.strip().replace("null", "None")
    try:
        rows = ast.literal_eval(f"[{content}]")
    except (ValueError, SyntaxError):
        return {}
    holdings: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        name, value = row[0], row[1]
        if isinstance(name, str) and isinstance(value, int | float):
            holdings[name] = float(value)
    return holdings


def parse_overview_text(symbol: str, text: str) -> FundamentalData:
    """Pure parsing function, kept separate from the network call so it can
    be unit-tested against fixed sample text without a live MCP call."""
    data = FundamentalData(symbol=symbol)

    fundamental_section = _extract_section(text, "fundamentalData")
    if fundamental_section is not None:
        try:
            values = _parse_fundamental_table(fundamental_section)
        except Exception as exc:  # noqa: BLE001 - a parse bug must never break scanning
            logger.warning(
                "Trendlyne fundamentalData parse failed for {symbol}: {error}",
                symbol=symbol,
                error=exc,
            )
        else:
            for unique_name, attr in _FUNDAMENTAL_FIELD_MAP.items():
                if unique_name in values:
                    setattr(data, attr, values[unique_name])

    summary_section = _extract_section(text, "summaryData")
    if summary_section is not None:
        try:
            holdings = _parse_summary_holdings(summary_section)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Trendlyne summaryData parse failed for {symbol}: {error}", symbol=symbol, error=exc
            )
        else:
            for holding_name, attr in _SUMMARY_FIELD_MAP.items():
                if holding_name in holdings:
                    setattr(data, attr, holdings[holding_name])

    return data


@dataclass
class _CacheEntry:
    data: FundamentalData | None
    fetched_at: float


class TrendlyneFundamentalDataProvider(FundamentalDataProvider):
    name = "trendlyne_mcp"

    def __init__(self, settings: Settings, client: TrendlyneMcpClient | None = None) -> None:
        self._settings = settings
        self._client = client or TrendlyneMcpClient(
            mcp_url=settings.trendlyne_mcp_url,
            timeout_seconds=settings.trendlyne_mcp_request_timeout,
        )
        self._cache: dict[str, _CacheEntry] = {}

    async def get_fundamentals(self, symbol: str) -> FundamentalData | None:
        cached = self._cache.get(symbol)
        ttl_seconds = self._settings.fundamental_cache_ttl_minutes * 60
        if cached is not None and (time.monotonic() - cached.fetched_at) < ttl_seconds:
            return cached.data

        try:
            text = await self._client.call_tool(
                "get_overview_news_corp_events", {"stock_code": symbol, "type": "overview"}
            )
            data = parse_overview_text(symbol, text)
        except TrendlyneMcpError as exc:
            logger.warning(
                "Trendlyne MCP fundamentals unavailable for {symbol}: {error}",
                symbol=symbol,
                error=exc,
            )
            # Not cached: a transient failure (rate limit, timeout) should
            # not deny fundamentals for the full cache TTL — retry next call.
            return None

        self._cache[symbol] = _CacheEntry(data=data, fetched_at=time.monotonic())
        return data

    async def health_check(self) -> bool:
        return await self._client.health_check()
