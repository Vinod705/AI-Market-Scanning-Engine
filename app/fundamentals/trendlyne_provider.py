"""TrendlyneFundamentalDataProvider: the primary fundamental-data source
(Phase 7, extended in the multi-source follow-up).

Combines three Trendlyne MCP tool calls per symbol, each independently
fault-isolated (one failing never blanks out what the others returned):

1. `get_overview_news_corp_events(stock_code, type="overview")` — a fixed-
   shape, single-stock response. Two of its sections are parsed: the
   `fundamentalData` pipe table (PE, P/B, PEG, dividend yield, market cap,
   revenue/profit/CFO TTM, ROE) and the `summaryData` shareholding line
   (Promoter/FII).
2. `get_ownership_deals_insider_sast(stock_code, type="shareholding")` —
   a dedicated ownership endpoint with a cleaner shareholding breakdown
   than (1), including an explicit DII series and a per-quarter promoter
   pledge history (`chartData.Promoter`, a single JSON-array-literal line
   — reliably parseable, unlike the multi-line chart data in the overview
   response, which is why pledge wasn't parsed in the first Phase 7 pass).
3. `get_parameter_values_multi_stock` — used narrowly and only for two
   fields overview/shareholding don't provide at all: ROCE and
   Debt-to-Equity. This tool is semantic/RAG-style and returns a peer-
   company comparison table, not just the requested symbol — live testing
   (querying "RELIANCE INDUSTRIES only... no peer comparison") confirmed
   phrasing cannot suppress this. It is NOT used for broad scoring. It IS
   safe for this narrow use because every metric block is keyed by exact
   NSE code, so the target symbol's value can be extracted unambiguously
   as long as (a) the header row confirms the requested code was actually
   resolved, and (b) only an EXACT metric-label match ("ROCE Ann. %",
   "Total Debt to Total Equity Ann." — never a "1Y Ago"/"3Y Avg" variant)
   is accepted. If a call doesn't happen to return one of those two exact
   labels (the tool's field selection is non-deterministic per query),
   that field simply stays UNKNOWN — never guessed from a differently-
   labeled or differently-dated variant.

Only fields a source actually returned are ever set; anything absent
stays `None`/UNKNOWN, which `FundamentalScorer` already treats correctly
(never as zero — see `app.fundamentals.scorer`). Every populated field
also gets a `FieldSnapshot` (value/source/period/status) for the
dashboard/API/Telegram explainability surfaces — the scorer itself never
reads that, only the plain float fields.
"""

import ast
import json
import re
import time
from dataclasses import dataclass

from loguru import logger

from app.config.settings import Settings
from app.fundamentals.models import FieldAvailability, FieldSnapshot, FundamentalData
from app.fundamentals.provider import FundamentalDataProvider
from app.fundamentals.trendlyne_mcp_client import TrendlyneMcpClient, TrendlyneMcpError

_SOURCE_NAME = "Trendlyne"

# fundamentalData unique_name -> (FundamentalData attribute, display period).
# Only fields with a clear, unambiguous single-stock meaning are mapped.
_FUNDAMENTAL_FIELD_MAP: dict[str, tuple[str, str]] = {
    "MCAP_Q": ("market_cap_cr", "current"),
    "PE_TTM": ("pe", "TTM"),
    "PBV_A": ("pb", "TTM"),
    "PEG_TTM": ("peg", "TTM"),
    "DIVIDEND_YIELD_1_YR": ("dividend_yield_pct", "1Y"),
    "SR_TTM": ("revenue_ttm_cr", "TTM"),
    "NP_TTM": ("net_profit_ttm_cr", "TTM"),
    "CFO_A": ("operating_cash_flow_cr", "Annual"),
    "ROE_A": ("roe_pct", "Annual"),
}

_SUMMARY_FIELD_MAP: dict[str, tuple[str, str]] = {
    "Promoter": ("promoter_holding_pct", "latest quarter"),
    "FII": ("fii_holding_pct", "latest quarter"),
}

# Exact metric label -> (FundamentalData attribute, display period). Deliberately
# narrow — see module docstring for why only these two exact labels are trusted.
_TARGETED_MULTI_STOCK_FIELDS: dict[str, tuple[str, str]] = {
    "ROCE Ann. %": ("roce_pct", "Annual"),
    "Total Debt to Total Equity Ann.": ("debt_to_equity", "Annual"),
}

_SECTION_HEADER_RE = re.compile(r"^([ \t]*)([A-Za-z][A-Za-z0-9]*):[ \t]*$", re.MULTILINE)


def _extract_section(text: str, section_name: str) -> str | None:
    """Returns the raw text between `{section_name}:` and the next header
    at the SAME OR SHALLOWER indentation (or end of string), None if not
    present. Indentation-aware so this works both for top-level headers
    (`fundamentalData:`, no indent — correctly includes deeper nested
    content like `chartData:`'s indented `Promoter:`/`DII:` sub-headers)
    and for extracting one of those nested sub-headers itself (bounded by
    its next sibling, not by every header regardless of nesting)."""
    match = re.search(rf"^([ \t]*){section_name}:[ \t]*$", text, re.MULTILINE)
    if match is None:
        return None
    indent = len(match.group(1))
    start = match.end()
    end = len(text)
    for next_match in _SECTION_HEADER_RE.finditer(text, pos=start):
        if len(next_match.group(1)) <= indent:
            end = next_match.start()
            break
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


def _set_field(data: FundamentalData, attr: str, value: float, *, source: str, period: str) -> None:
    setattr(data, attr, value)
    data.field_snapshots[attr] = FieldSnapshot(
        field_name=attr,
        value=value,
        source=source,
        period=period,
        status=FieldAvailability.AVAILABLE,
        as_of=data.as_of,
    )


def parse_overview_text(symbol: str, text: str) -> FundamentalData:
    """Pure parsing function for `get_overview_news_corp_events` (type=
    overview), kept separate from the network call so it's unit-testable
    against fixed sample text."""
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
            for unique_name, (attr, period) in _FUNDAMENTAL_FIELD_MAP.items():
                if unique_name in values:
                    _set_field(data, attr, values[unique_name], source=_SOURCE_NAME, period=period)

    summary_section = _extract_section(text, "summaryData")
    if summary_section is not None:
        try:
            holdings = _parse_summary_holdings(summary_section)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Trendlyne summaryData parse failed for {symbol}: {error}", symbol=symbol, error=exc
            )
        else:
            for holding_name, (attr, period) in _SUMMARY_FIELD_MAP.items():
                if holding_name in holdings:
                    _set_field(
                        data, attr, holdings[holding_name], source=_SOURCE_NAME, period=period
                    )

    return data


def apply_shareholding_text(data: FundamentalData, text: str) -> None:
    """Pure parsing function for `get_ownership_deals_insider_sast`
    (type=shareholding). Mutates `data` in place, adding DII holding and
    promoter pledge on top of whatever the overview call already set —
    never overwrites a field the overview call already populated with a
    different (potentially stale) value from a different endpoint."""
    summary_section = _extract_section(text, "summaryData")
    if summary_section is not None:
        try:
            holdings = _parse_summary_holdings(summary_section)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Trendlyne shareholding summaryData parse failed for {symbol}: {error}",
                symbol=data.symbol,
                error=exc,
            )
        else:
            if data.fii_holding_pct is None and "FII" in holdings:
                _set_field(
                    data,
                    "fii_holding_pct",
                    holdings["FII"],
                    source=_SOURCE_NAME,
                    period="latest quarter",
                )
            if data.promoter_holding_pct is None and "Promoter" in holdings:
                _set_field(
                    data,
                    "promoter_holding_pct",
                    holdings["Promoter"],
                    source=_SOURCE_NAME,
                    period="latest quarter",
                )
            # No DII row in summaryData (only chartData has a dedicated
            # DII series) — that's why this call exists at all.

    chart_section = _extract_section(text, "chartData")
    if chart_section is None:
        return
    try:
        dii_pct, dii_period = _parse_chart_series_latest(chart_section, "DII")
        promoter_pledge_pct, pledge_period = _parse_chart_promoter_pledge(chart_section)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Trendlyne shareholding chartData parse failed for {symbol}: {error}",
            symbol=data.symbol,
            error=exc,
        )
        return
    if dii_pct is not None:
        _set_field(
            data,
            "dii_holding_pct",
            dii_pct,
            source=_SOURCE_NAME,
            period=dii_period or "latest quarter",
        )
    if promoter_pledge_pct is not None:
        _set_field(
            data,
            "promoter_pledge_pct",
            promoter_pledge_pct,
            source=_SOURCE_NAME,
            period=pledge_period or "latest quarter",
        )


def _parse_chart_series_latest(
    chart_section: str, series_name: str
) -> tuple[float | None, str | None]:
    """`chartData` has one sub-section per holder type (`Promoter:`, `FII:`,
    `DII:`, ...), each a single `["Quarter","Holding (%)"], [...], ...`
    line — returns the LAST (most recent) quarter's holding % and label."""
    sub_section = _extract_section(chart_section, series_name)
    if sub_section is None:
        return None, None
    content = sub_section.strip().replace("null", "None")
    try:
        rows = ast.literal_eval(f"[{content}]")
    except (ValueError, SyntaxError):
        return None, None
    data_rows = [r for r in rows if isinstance(r, list) and len(r) >= 2 and isinstance(r[0], str)][
        1:
    ]
    if not data_rows:
        return None, None
    quarter, value = data_rows[-1][0], data_rows[-1][1]
    if not isinstance(value, int | float):
        return None, None
    return float(value), str(quarter)


def _parse_chart_promoter_pledge(chart_section: str) -> tuple[float | None, str | None]:
    """The `Promoter:` sub-section carries pledge % as the 4th element of
    each row: [quarter, holding_pct, annotation, pledge_pct, annotation, ...]."""
    sub_section = _extract_section(chart_section, "Promoter")
    if sub_section is None:
        return None, None
    content = sub_section.strip().replace("null", "None")
    try:
        rows = ast.literal_eval(f"[{content}]")
    except (ValueError, SyntaxError):
        return None, None
    data_rows = [r for r in rows if isinstance(r, list) and len(r) >= 4 and isinstance(r[0], str)][
        1:
    ]
    if not data_rows:
        return None, None
    quarter, pledge = data_rows[-1][0], data_rows[-1][3]
    if not isinstance(pledge, int | float):
        return None, None
    return float(pledge), str(quarter)


def apply_targeted_multi_stock_text(data: FundamentalData, nse_code: str, text: str) -> None:
    """Pure parsing function for a `get_parameter_values_multi_stock` call
    targeted at ROCE/Debt-to-Equity. Mutates `data` in place. See module
    docstring for exactly why this narrow use is safe despite the tool
    returning peer-company data."""
    try:
        outer = json.loads(text)
        markdown_data = outer.get("markdown_data") if isinstance(outer, dict) else None
        content = json.loads(markdown_data) if isinstance(markdown_data, str) else markdown_data
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Trendlyne multi_stock response parse failed for {symbol}: {error}",
            symbol=data.symbol,
            error=exc,
        )
        return
    if not isinstance(content, str):
        return

    blocks = content.split("---")
    if not blocks:
        return

    # The first block starts with the header rows: "id|name|nse_code|
    # bse_code|date" per line (5 pipe-separated fields — 4 "|" characters).
    # There is NOT always a "---" between the header and the first metric
    # block, so a metric can legitimately be bundled into blocks[0] too —
    # this must not be treated as pure header and discarded.
    first_block_lines = [line.strip() for line in blocks[0].strip().splitlines() if line.strip()]
    header_lines = [line for line in first_block_lines if line.count("|") >= 4]
    resolved_codes = {line.split("|")[2].strip() for line in header_lines}
    if nse_code not in resolved_codes:
        logger.warning(
            "Trendlyne multi_stock did not resolve requested symbol {symbol} ({code}) — skipping",
            symbol=data.symbol,
            code=nse_code,
        )
        return

    remaining_first_block = [line for line in first_block_lines if line not in header_lines]
    metric_blocks = [remaining_first_block] + [
        [line.strip() for line in block.strip().splitlines() if line.strip()]
        for block in blocks[1:]
    ]

    for lines in metric_blocks:
        if not lines:
            continue
        label = lines[0]
        mapped = _TARGETED_MULTI_STOCK_FIELDS.get(label)
        if mapped is None:
            continue
        attr, period = mapped
        target_line = next((line for line in lines[1:] if line.startswith(f"{nse_code}:")), None)
        if target_line is None:
            continue
        raw_value = target_line.split(":", 1)[1].strip()
        try:
            value = float(raw_value)
        except ValueError:
            continue  # "None" or malformed — stays UNKNOWN, not zeroed
        _set_field(data, attr, value, source=_SOURCE_NAME, period=period)


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

        data: FundamentalData | None = None
        try:
            text = await self._client.call_tool(
                "get_overview_news_corp_events", {"stock_code": symbol, "type": "overview"}
            )
            data = parse_overview_text(symbol, text)
        except TrendlyneMcpError as exc:
            logger.warning(
                "Trendlyne overview unavailable for {symbol}: {error}", symbol=symbol, error=exc
            )

        if data is None:
            # The primary call failed outright — nothing to enrich further.
            # Not cached: a transient failure should not deny fundamentals
            # for the full cache TTL.
            return None

        try:
            shareholding_text = await self._client.call_tool(
                "get_ownership_deals_insider_sast", {"stock_code": symbol, "type": "shareholding"}
            )
            apply_shareholding_text(data, shareholding_text)
        except TrendlyneMcpError as exc:
            logger.warning(
                "Trendlyne shareholding unavailable for {symbol}: {error}", symbol=symbol, error=exc
            )

        try:
            multi_stock_text = await self._client.call_tool(
                "get_parameter_values_multi_stock",
                {"query": f"{symbol} ROCE annual percentage and total debt to equity ratio"},
            )
            apply_targeted_multi_stock_text(data, symbol, multi_stock_text)
        except TrendlyneMcpError as exc:
            logger.warning(
                "Trendlyne targeted ROCE/D-E lookup unavailable for {symbol}: {error}",
                symbol=symbol,
                error=exc,
            )

        self._cache[symbol] = _CacheEntry(data=data, fetched_at=time.monotonic())
        return data

    async def health_check(self) -> bool:
        return await self._client.health_check()
