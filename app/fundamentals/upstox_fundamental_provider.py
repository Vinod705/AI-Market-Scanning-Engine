"""UpstoxFundamentalDataProvider: fundamentals sourced from Upstox's own
Company Fundamentals API — the primary fundamental source now that it's
been verified live (this session).

**Only 4 real endpoints exist** (verified live against
`GET https://api.upstox.com/v2/fundamentals/{isin}/...` with a real
access token — every other plausible name tried, including
`profit-loss`, `ratios`, `quarterly-results`, `shareholding`,
`shareholding-pattern`, `dividend`, `market-cap`, and `valuation`,
returned `404 UDAPI100060 Resource not Found`):

- `balance-sheet` — `total_asset`/`total_liability` per yearly period.
  No `FundamentalData` field matches this shape (no
  total-assets/liabilities field exists on that model), so this endpoint
  is confirmed real but not called — nothing to map it to.
- `income-statement` — `revenue`/`operating_profit`/`net_profit` per
  yearly period -> `revenue_ttm_cr`/`net_profit_ttm_cr` (latest period).
- `cash-flow` — operating/investing/financing cash flow per yearly period
  -> `operating_cash_flow_cr` (latest period, "operating" category).
- `key-ratios` — `P/E`, `P/B`, `ROA`, `ROE`, `ROCE`, `Quick Ratio`,
  `EV/EBITDA`, each with a `company_value` -> `pe`/`pb`/`roe_pct`/
  `roce_pct`/`ev_ebitda`. `ROA` and `Quick Ratio` are real values but have
  no matching `FundamentalData` field (that model has `current_ratio`,
  a different ratio, and no ROA field at all) — mapping them to the wrong
  field would misrepresent the data, so they're read but not used.

**Deliberately NOT computed here**: multi-year growth percentages
(`revenue_growth_3y_pct`, `profit_growth_3y_pct`, ...). Upstox's
`income-statement` response includes a `change` field, but it's a
1-year year-over-year change, not the 3-year figure those model fields
document — computing a 3-year CAGR from the 4 years of raw history Upstox
does return would be a *new* derived calculation this provider invents,
not a value the API itself reports, so those fields stay unset (UNKNOWN)
rather than backed into a number Upstox never actually stated.

**No ownership/shareholding data at all** (Promoter/FII/DII holding,
pledge) — confirmed unavailable above. `TrendlyneFundamentalDataProvider`
remains the only source for those fields; see its module docstring for
its now-legacy/fallback role in `MultiSourceFundamentalProvider`'s
priority list.

**ISIN resolution**: Upstox's fundamentals endpoints key by ISIN, not
trading symbol. `Symbol.instrument_token` already stores Upstox's own
`instrument_key` (`"NSE_EQ|<ISIN>"` for every equity — see
`app.providers.upstox_provider.UpstoxProvider.get_symbols`), so the ISIN
is extracted from that existing column — no new lookup, no new column,
nothing invented. A token that isn't in that exact shape (e.g. a
legacy/non-Upstox-sourced symbol) is treated as unresolvable, not guessed.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import date as date_

import httpx
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.fundamentals.models import FundamentalData
from app.fundamentals.provider import FundamentalDataProvider
from app.fundamentals.queue_models import FetchStatus
from app.repositories.market_repository import SymbolRepository

_ISIN_PREFIX = "NSE_EQ|"


@dataclass
class _FetchOutcome:
    """Result of one statement call — `payload` on success (including the
    "no data for this symbol" case, where `payload` stays `None` but
    `rate_limited`/`error` are also `False`/`None`), `rate_limited=True`
    on a 429, or `error` set for anything else."""

    payload: dict[str, object] | list[object] | None = None
    rate_limited: bool = False
    error: str | None = None


_KEY_RATIO_FIELD_MAP = {
    "P/E": "pe",
    "P/B": "pb",
    "ROE": "roe_pct",
    "ROCE": "roce_pct",
    "EV/EBITDA": "ev_ebitda",
}


def _parse_ratio_value(raw: str) -> float | None:
    """Upstox reports ratio values as strings, some with a trailing '%'
    (e.g. `"45.89%"`), some without (e.g. `"17.05"`) — strip and parse
    either shape; malformed values are skipped, never guessed."""
    try:
        return float(raw.rstrip("%"))
    except (TypeError, ValueError):
        return None


def _extract_isin(instrument_token: str) -> str | None:
    if not instrument_token.startswith(_ISIN_PREFIX):
        return None
    isin = instrument_token[len(_ISIN_PREFIX) :]
    return isin or None


class UpstoxFundamentalDataProvider(FundamentalDataProvider):
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

    async def get_fundamentals(self, symbol: str) -> FundamentalData | None:
        data, _status, _error = await self.get_fundamentals_with_status(symbol)
        return data

    async def get_fundamentals_with_status(
        self, symbol: str
    ) -> tuple[FundamentalData | None, FetchStatus, str | None]:
        isin = await self._resolve_isin(symbol)
        if isin is None:
            return None, FetchStatus.FAILED, f"no resolvable ISIN for {symbol}"

        data = FundamentalData(symbol=symbol)
        populated = False
        errors: list[str] = []
        any_rate_limited = False

        income_statement = await self._get(f"/fundamentals/{isin}/income-statement")
        if income_statement.rate_limited:
            any_rate_limited = True
        elif isinstance(income_statement.payload, dict):
            latest_period = _apply_income_statement(data, income_statement.payload)
            if latest_period is not None:
                data.as_of = latest_period
                populated = True
        elif income_statement.error is not None:
            errors.append(income_statement.error)

        cash_flow = await self._get(f"/fundamentals/{isin}/cash-flow")
        if cash_flow.rate_limited:
            any_rate_limited = True
        elif isinstance(cash_flow.payload, dict):
            if _apply_cash_flow(data, cash_flow.payload):
                populated = True
        elif cash_flow.error is not None:
            errors.append(cash_flow.error)

        key_ratios = await self._get(f"/fundamentals/{isin}/key-ratios")
        if key_ratios.rate_limited:
            any_rate_limited = True
        elif isinstance(key_ratios.payload, list):
            if _apply_key_ratios(data, key_ratios.payload):
                populated = True
        elif key_ratios.error is not None:
            errors.append(key_ratios.error)

        if populated:
            return data, FetchStatus.SUCCESS, None
        if any_rate_limited:
            return None, FetchStatus.RATE_LIMITED, "Upstox fundamentals API rate limited"
        if errors:
            return None, FetchStatus.FAILED, "; ".join(errors)[:500]
        return None, FetchStatus.FAILED, f"no fundamental data returned for {symbol}"

    # --- internals -----------------------------------------------------

    async def _resolve_isin(self, symbol: str) -> str | None:
        async with self._session_factory() as session:
            symbol_row = await SymbolRepository(session).get_by_symbol(symbol)
        if symbol_row is None:
            return None
        return _extract_isin(symbol_row.instrument_token)

    async def _get(self, path: str) -> "_FetchOutcome":
        """`payload` is set on success, `None` if the symbol genuinely has
        no data for this statement (404 — a real, non-error condition for
        e.g. a newly-listed company). `rate_limited`/`error` are mutually
        exclusive with `payload` — the caller aggregates across all three
        statement calls so a rate limit on one doesn't get lost just
        because another statement happened to succeed from cache/edge
        timing, and so the queue (see app.fundamentals.queue_service) can
        tell a real rate limit apart from a plain failure and back off,
        the same distinction TrendlyneFundamentalDataProvider already
        makes."""
        url = f"{self._settings.upstox_base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self._settings.upstox_access_token}",
            "Accept": "application/json",
        }
        try:
            response = await self._client.get(
                url, headers=headers, timeout=self._settings.upstox_request_timeout
            )
        except httpx.HTTPError as exc:
            logger.warning("Upstox fundamentals call {url} failed: {error}", url=url, error=exc)
            return _FetchOutcome(error=str(exc))

        if response.status_code == 404:
            return _FetchOutcome()
        if response.status_code == 429:
            return _FetchOutcome(rate_limited=True)
        if response.status_code != 200:
            return _FetchOutcome(error=f"HTTP {response.status_code}")

        try:
            body = response.json()
        except ValueError:
            return _FetchOutcome(error="invalid JSON response")
        if not isinstance(body, dict) or body.get("status") != "success":
            return _FetchOutcome(error="unexpected response shape")
        data = body.get("data")
        if isinstance(data, dict | list):
            return _FetchOutcome(payload=data)
        return _FetchOutcome()


def _apply_income_statement(data: FundamentalData, payload: dict[str, object]) -> date_ | None:
    categories = payload.get("income_statement")
    if not isinstance(categories, list):
        return None
    latest_period: date_ | None = None
    for category in categories:
        if not isinstance(category, dict):
            continue
        name = category.get("category")
        history = category.get("history")
        if not isinstance(history, list) or not history:
            continue
        latest = history[0]
        if not isinstance(latest, dict):
            continue
        value = latest.get("value")
        period_date = _parse_period(latest.get("period"))
        if period_date is not None and (latest_period is None or period_date > latest_period):
            latest_period = period_date
        if not isinstance(value, int | float):
            continue
        if name == "revenue":
            data.revenue_ttm_cr = float(value)
        elif name == "net_profit":
            data.net_profit_ttm_cr = float(value)
    return latest_period


def _apply_cash_flow(data: FundamentalData, payload: dict[str, object]) -> bool:
    categories = payload.get("cash_flow")
    if not isinstance(categories, list):
        return False
    for category in categories:
        if not isinstance(category, dict) or category.get("category") != "operating":
            continue
        history = category.get("history")
        if not isinstance(history, list) or not history:
            continue
        latest = history[0]
        if not isinstance(latest, dict):
            continue
        value = latest.get("value")
        if isinstance(value, int | float):
            data.operating_cash_flow_cr = float(value)
            return True
    return False


def _apply_key_ratios(data: FundamentalData, payload: list[object]) -> bool:
    populated = False
    for row in payload:
        if not isinstance(row, dict):
            continue
        field_name = _KEY_RATIO_FIELD_MAP.get(str(row.get("name")))
        if field_name is None:
            continue
        raw_value = row.get("company_value")
        if not isinstance(raw_value, str):
            continue
        parsed = _parse_ratio_value(raw_value)
        if parsed is not None:
            setattr(data, field_name, parsed)
            populated = True
    return populated


def _parse_period(period: object) -> date_ | None:
    """Upstox periods are like `"Mar 2026"` — parsed only to pick the
    latest history entry's date, never displayed/stored verbatim as a
    fabricated day-of-month. Reviewed for the IST-business-date audit:
    a financial reporting period (a fiscal quarter/year) is not an
    Indian trading-session date, so it's intentionally left as a
    UTC-tagged month/year marker used only for chronological ordering
    between periods — never compared against `daily_prices`/
    `daily_features` or any trading-calendar business date."""
    if not isinstance(period, str):
        return None
    try:
        return datetime.strptime(period, "%b %Y").replace(tzinfo=UTC).date()
    except ValueError:
        return None
