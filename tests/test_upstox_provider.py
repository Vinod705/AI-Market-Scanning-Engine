"""Tests for app.providers.upstox_provider.UpstoxProvider.

Uses httpx.MockTransport to simulate the Upstox REST API — no real network
calls, no live credentials required, same convention as
tests/test_telegram_provider.py and tests/test_trendlyne_provider.py.
"""

import contextlib
import gzip
import json
import time
from datetime import UTC, datetime

import httpx

from app.config.settings import Settings
from app.providers.base_provider import ProviderError
from app.providers.upstox_provider import UpstoxProvider

_INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = dict(
        upstox_access_token="test-token",
        upstox_max_retries=2,
        upstox_retry_backoff_seconds=0.01,
        upstox_request_timeout=1.0,
        upstox_rate_limit_per_sec=1000.0,
        upstox_instruments_url=_INSTRUMENTS_URL,
    )
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _provider(handler, settings: Settings | None = None) -> UpstoxProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return UpstoxProvider(settings or _settings(), client=client)


def _instruments_response(records: list[dict[str, object]]) -> httpx.Response:
    body = gzip.compress(json.dumps(records).encode())
    return httpx.Response(200, content=body)


_SAMPLE_INSTRUMENTS = [
    {
        "segment": "NSE_EQ",
        "name": "Tata Consultancy Services",
        "exchange": "NSE",
        "isin": "INE467B01029",
        "instrument_type": "EQ",
        "instrument_key": "NSE_EQ|INE467B01029",
        "trading_symbol": "TCS",
    },
    {
        "segment": "NSE_EQ",
        "name": "Infosys",
        "exchange": "NSE",
        "isin": "INE009A01021",
        "instrument_type": "EQ",
        "instrument_key": "NSE_EQ|INE009A01021",
        "trading_symbol": "INFY",
    },
    # Non-equity segment — must be filtered out of get_symbols(), but is
    # exactly what get_fno_symbol_roots() reads. Shape verified live
    # against Upstox's real instruments master this session.
    {
        "segment": "NSE_FO",
        "name": "TCS FUT",
        "exchange": "NSE",
        "instrument_type": "FUT",
        "instrument_key": "NSE_FO|12345",
        "trading_symbol": "TCS25AUGFUT",
        "underlying_symbol": "TCS",
        "underlying_type": "EQUITY",
    },
    {
        "segment": "NSE_FO",
        "name": "TCS 4000 CE",
        "exchange": "NSE",
        "instrument_type": "CE",
        "instrument_key": "NSE_FO|12346",
        "trading_symbol": "TCS 4000 CE 27 OCT 26",
        "underlying_symbol": "TCS",
        "underlying_type": "EQUITY",
    },
    {
        # Index derivative — no underlying cash-market row of its own;
        # get_fno_symbol_roots() includes it, callers intersect with
        # known equity symbols to exclude it (see docstring).
        "segment": "NSE_FO",
        "name": "NIFTY FUT",
        "exchange": "NSE",
        "instrument_type": "FUT",
        "instrument_key": "NSE_FO|99999",
        "trading_symbol": "NIFTY25AUGFUT",
        "underlying_symbol": "NIFTY",
        "underlying_type": "INDEX",
    },
]


def test_upstox_provider_starts_disconnected() -> None:
    provider = _provider(lambda r: httpx.Response(200))
    assert provider.is_connected() is False


async def test_connect_rejects_without_access_token() -> None:
    provider = _provider(lambda r: httpx.Response(200), _settings(upstox_access_token=""))
    try:
        await provider.connect()
        raise AssertionError("expected ProviderError")
    except ProviderError as exc:
        assert exc.retryable is False
    assert provider.is_connected() is False


async def test_connect_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-token"
        # Not /user/profile — that requires a static IP on the Upstox
        # account and 401s even for a genuinely valid Analytics Token.
        assert str(request.url) == "https://api.upstox.com/v3/feed/market-data-feed/authorize"
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"authorized_redirect_uri": "wss://example.invalid/feed"},
            },
        )

    provider = _provider(handler)
    await provider.connect()

    assert provider.is_connected() is True


async def test_connect_401_raises_non_retryable_and_stays_disconnected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "status": "error",
                "errors": [{"error_code": "UDAPI100050", "message": "Invalid token"}],
            },
        )

    provider = _provider(handler)
    try:
        await provider.connect()
        raise AssertionError("expected ProviderError")
    except ProviderError as exc:
        assert exc.retryable is False
        assert "Invalid token" not in str(exc)  # connect() uses its own generic message
    assert provider.is_connected() is False


async def test_connect_recoverable_after_earlier_failure() -> None:
    """"Reconnect" for a static-token provider means re-validating the same
    token — must succeed cleanly once the transient issue clears, not get
    stuck in a permanently-failed state."""
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(500)
        return httpx.Response(200, json={"status": "success", "data": {}})

    provider = _provider(handler)
    with contextlib.suppress(ProviderError):
        await provider.connect()
    assert provider.is_connected() is False

    await provider.connect()
    assert provider.is_connected() is True


async def test_get_symbols_normalizes_and_filters_to_nse_eq() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _instruments_response(_SAMPLE_INSTRUMENTS)

    provider = _provider(handler)
    symbols = await provider.get_symbols()

    assert {s.symbol for s in symbols} == {"TCS", "INFY"}
    tcs = next(s for s in symbols if s.symbol == "TCS")
    assert tcs.instrument_token == "NSE_EQ|INE467B01029"
    assert tcs.exchange == "NSE"
    assert tcs.isin == "INE467B01029"
    assert tcs.company_name == "Tata Consultancy Services"


async def test_get_fno_symbol_roots_reads_underlying_symbol() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _instruments_response(_SAMPLE_INSTRUMENTS)

    provider = _provider(handler)
    roots = await provider.get_fno_symbol_roots()

    # TCS appears via both a FUT and a CE record -> deduped to one root.
    # NIFTY (index) is included -- callers are documented to intersect
    # with known equity symbols to exclude it, not this method.
    assert roots == {"TCS", "NIFTY"}


async def test_get_quote_normalizes_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "instruments" in str(request.url):
            return _instruments_response(_SAMPLE_INSTRUMENTS)
        assert request.url.params.get("instrument_key") == "NSE_EQ|INE467B01029"
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "NSE_EQ:TCS": {
                        "last_price": 3500.5,
                        "ohlc": {"open": 3480.0, "high": 3510.0, "low": 3470.0, "close": 3490.0},
                        "volume": 125000,
                    }
                },
            },
        )

    provider = _provider(handler)
    quote = await provider.get_quote("TCS")

    assert quote.symbol == "TCS"
    assert float(quote.ltp) == 3500.5
    assert float(quote.volume) == 125000
    assert float(quote.high) == 3510.0


async def test_get_daily_normalizes_candle_arrays() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "instruments" in str(request.url):
            return _instruments_response(_SAMPLE_INSTRUMENTS)
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "candles": [
                        ["2026-08-12T00:00:00+05:30", 100.0, 105.0, 99.0, 103.0, 50000, 0],
                        ["2026-08-11T00:00:00+05:30", 98.0, 101.0, 97.0, 100.0, 42000, 0],
                    ]
                },
            },
        )

    provider = _provider(handler)
    candles = await provider.get_daily("TCS")

    assert len(candles) == 2
    assert float(candles[0].open) == 100.0
    assert candles[0].volume == 50000


async def test_get_intraday_returns_empty_list_when_no_candles() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "instruments" in str(request.url):
            return _instruments_response(_SAMPLE_INSTRUMENTS)
        return httpx.Response(200, json={"status": "success", "data": {"candles": []}})

    provider = _provider(handler)
    candles = await provider.get_intraday("TCS")

    assert candles == []


async def test_resolve_symbol_unknown_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _instruments_response(_SAMPLE_INSTRUMENTS)

    provider = _provider(handler)
    try:
        await provider.get_quote("NOPE")
        raise AssertionError("expected ProviderError")
    except ProviderError as exc:
        assert "Unknown symbol" in str(exc)
        assert exc.retryable is False


async def test_rate_limited_retries_then_succeeds() -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "instruments" in str(request.url):
            return _instruments_response(_SAMPLE_INSTRUMENTS)
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, json={"status": "error", "errors": []})
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"NSE_EQ:TCS": {"last_price": 100, "volume": 1}},
            },
        )

    provider = _provider(handler)
    quote = await provider.get_quote("TCS")

    assert len(attempts) == 2
    assert float(quote.ltp) == 100


async def test_rate_limited_exhausts_retries_then_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "instruments" in str(request.url):
            return _instruments_response(_SAMPLE_INSTRUMENTS)
        return httpx.Response(429, json={"status": "error", "errors": []})

    provider = _provider(handler, _settings(upstox_max_retries=2, upstox_retry_backoff_seconds=0.01))
    try:
        await provider.get_quote("TCS")
        raise AssertionError("expected ProviderError")
    except ProviderError as exc:
        assert exc.retryable is True  # exhausted-retries default is retryable=True


async def test_auth_failure_mid_call_raises_non_retryable_and_disconnects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "instruments" in str(request.url):
            return _instruments_response(_SAMPLE_INSTRUMENTS)
        return httpx.Response(
            401, json={"status": "error", "errors": [{"message": "Invalid token"}]}
        )

    provider = _provider(handler)
    try:
        await provider.get_quote("TCS")
        raise AssertionError("expected ProviderError")
    except ProviderError as exc:
        assert exc.retryable is False
        assert "Invalid token" in str(exc)
    assert provider.is_connected() is False


async def test_permanent_4xx_does_not_retry() -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "instruments" in str(request.url):
            return _instruments_response(_SAMPLE_INSTRUMENTS)
        attempts.append(1)
        return httpx.Response(
            404, json={"status": "error", "errors": [{"message": "Not found"}]}
        )

    provider = _provider(handler)
    try:
        await provider.get_quote("TCS")
        raise AssertionError("expected ProviderError")
    except ProviderError as exc:
        assert exc.retryable is False
    assert len(attempts) == 1  # never retried a permanent failure


async def test_timeout_retries_then_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "instruments" in str(request.url):
            return _instruments_response(_SAMPLE_INSTRUMENTS)
        raise httpx.ReadTimeout("timed out", request=request)

    provider = _provider(handler, _settings(upstox_max_retries=2, upstox_retry_backoff_seconds=0.01))
    try:
        await provider.get_quote("TCS")
        raise AssertionError("expected ProviderError")
    except ProviderError as exc:
        assert "timed out" in str(exc) or "failed after retries" in str(exc)


async def test_rate_limiter_spaces_out_calls() -> None:
    provider = _provider(
        lambda r: httpx.Response(200), _settings(upstox_rate_limit_per_sec=10.0)
    )  # 100ms min interval

    start = time.monotonic()
    await provider._respect_rate_limit()
    await provider._respect_rate_limit()
    elapsed = time.monotonic() - start

    assert elapsed >= 0.09  # allow small scheduling jitter below the 100ms floor


# --- DerivativesProvider (get_option_chain / get_futures_oi_history) -------
#
# Far-future expiry (2030) so these tests never go stale from `_nearest_fno_expiry`
# filtering out an expired contract as real time passes.
_FAR_FUTURE_EXPIRY_MS = int(datetime(2030, 1, 1, tzinfo=UTC).timestamp() * 1000)

_FNO_INSTRUMENTS = [
    *_SAMPLE_INSTRUMENTS,
    {
        "segment": "NSE_FO",
        "name": "TCS FUT",
        "exchange": "NSE",
        "instrument_type": "FUT",
        "instrument_key": "NSE_FO|68797",
        "trading_symbol": "TCS FUT",
        "underlying_symbol": "TCS",
        "underlying_key": "NSE_EQ|INE467B01029",
        "expiry": _FAR_FUTURE_EXPIRY_MS,
    },
    {
        "segment": "NSE_FO",
        "name": "TCS 2400 CE",
        "exchange": "NSE",
        "instrument_type": "CE",
        "instrument_key": "NSE_FO|149196",
        "trading_symbol": "TCS 2400 CE",
        "underlying_symbol": "TCS",
        "underlying_key": "NSE_EQ|INE467B01029",
        "strike_price": 2400.0,
        "expiry": _FAR_FUTURE_EXPIRY_MS,
    },
]


async def test_get_option_chain_returns_readings_for_nearest_expiry() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "instruments" in str(request.url):
            return _instruments_response(_FNO_INSTRUMENTS)
        assert request.url.params.get("instrument_key") == "NSE_EQ|INE467B01029"
        assert request.url.params.get("expiry_date") == datetime.fromtimestamp(
            _FAR_FUTURE_EXPIRY_MS / 1000, tz=UTC
        ).date().isoformat()
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": [
                    {
                        "expiry": "2030-01-01",
                        "pcr": 1.0,
                        "strike_price": 2400.0,
                        "underlying_key": "NSE_EQ|INE467B01029",
                        "underlying_spot_price": 2380.0,
                        "call_options": {
                            "instrument_key": "NSE_FO|149196",
                            "market_data": {
                                "ltp": 50.0,
                                "volume": 1000,
                                "oi": 1100.0,
                                "close_price": 40.0,
                                "prev_oi": 1000.0,
                            },
                        },
                        "put_options": {
                            "instrument_key": "NSE_FO|149197",
                            "market_data": {
                                "ltp": 30.0,
                                "volume": 800,
                                "oi": 900.0,
                                "close_price": 35.0,
                                "prev_oi": 950.0,
                            },
                        },
                    }
                ],
            },
        )

    provider = _provider(handler)
    snapshots = await provider.get_option_chain("TCS", "NSE_EQ|INE467B01029")

    assert len(snapshots) == 1
    row = snapshots[0]
    assert row.strike_price == 2400
    assert row.call is not None and row.call.oi == 1100
    assert row.put is not None and row.put.prev_oi == 950


async def test_get_option_chain_returns_empty_when_no_fno_contracts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _instruments_response(_SAMPLE_INSTRUMENTS)  # no expiry-bearing records at all

    provider = _provider(handler)
    snapshots = await provider.get_option_chain("INFY", "NSE_EQ|INE009A01021")

    assert snapshots == []


async def test_get_futures_oi_history_extracts_open_interest() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "instruments" in str(request.url):
            return _instruments_response(_FNO_INSTRUMENTS)
        assert "NSE_FO%7C68797" in str(request.url) or "NSE_FO|68797" in str(request.url)
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "candles": [
                        ["2026-08-14T00:00:00+05:30", 2399.8, 2401.8, 2348.0, 2379.9, 220275, 1926900],
                        ["2026-08-13T00:00:00+05:30", 2379.3, 2390.0, 2346.7, 2382.7, 304650, 1887300],
                    ]
                },
            },
        )

    provider = _provider(handler)
    bars = await provider.get_futures_oi_history("TCS")

    assert len(bars) == 2
    # sorted oldest-first
    assert bars[0].timestamp < bars[1].timestamp
    assert bars[1].open_interest == 1926900
    assert float(bars[1].close) == 2379.9


async def test_get_futures_oi_history_returns_empty_when_no_futures_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _instruments_response(_SAMPLE_INSTRUMENTS)  # no expiry-bearing FUT record

    provider = _provider(handler)
    bars = await provider.get_futures_oi_history("INFY")

    assert bars == []
