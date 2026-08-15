"""Tests for MultiSourceFundamentalProvider — priority-ordered, per-field
merge across multiple FundamentalDataProvider sources. Uses fake in-memory
providers (not real Trendlyne/network calls)."""

from app.fundamentals.models import FundamentalData
from app.fundamentals.orchestrator import MultiSourceFundamentalProvider
from app.fundamentals.provider import FundamentalDataProvider
from app.fundamentals.queue_models import FetchStatus


class _FakeProvider(FundamentalDataProvider):
    def __init__(self, name: str, data: FundamentalData | None) -> None:
        self.name = name
        self._data = data
        self.call_count = 0

    async def get_fundamentals(self, symbol: str) -> FundamentalData | None:
        self.call_count += 1
        return self._data


class _RaisingProvider(FundamentalDataProvider):
    name = "raising"

    async def get_fundamentals(self, symbol: str) -> FundamentalData | None:
        raise RuntimeError("boom")


async def test_returns_none_when_no_provider_has_data() -> None:
    orchestrator = MultiSourceFundamentalProvider(
        [_FakeProvider("A", None), _FakeProvider("B", None)]
    )
    assert await orchestrator.get_fundamentals("TCS") is None


async def test_single_provider_passthrough() -> None:
    orchestrator = MultiSourceFundamentalProvider(
        [_FakeProvider("Trendlyne", FundamentalData(symbol="TCS", pe=17.2))]
    )
    result = await orchestrator.get_fundamentals("TCS")
    assert result is not None
    assert result.pe == 17.2
    assert result.field_snapshots["pe"].source == "Trendlyne"


async def test_higher_priority_source_wins_on_conflict() -> None:
    orchestrator = MultiSourceFundamentalProvider(
        [
            _FakeProvider("Trendlyne", FundamentalData(symbol="RELIANCE", roe_pct=8.93)),
            _FakeProvider("TradingView", FundamentalData(symbol="RELIANCE", roe_pct=9.10)),
        ]
    )
    result = await orchestrator.get_fundamentals("RELIANCE")
    assert result is not None
    assert result.roe_pct == 8.93  # Trendlyne (first in the list) wins
    assert result.field_snapshots["roe_pct"].source == "Trendlyne"


async def test_conflicting_value_recorded_as_alternate_not_discarded() -> None:
    orchestrator = MultiSourceFundamentalProvider(
        [
            _FakeProvider("Trendlyne", FundamentalData(symbol="RELIANCE", roe_pct=8.93)),
            _FakeProvider("TradingView", FundamentalData(symbol="RELIANCE", roe_pct=9.10)),
        ]
    )
    result = await orchestrator.get_fundamentals("RELIANCE")
    assert result is not None
    assert result.field_snapshots["roe_pct"].alternates == [("TradingView", 9.10)]


async def test_falls_back_to_second_source_when_first_lacks_field() -> None:
    orchestrator = MultiSourceFundamentalProvider(
        [
            _FakeProvider("Trendlyne", FundamentalData(symbol="RELIANCE", pe=24.0)),  # no roce
            _FakeProvider("TradingView", FundamentalData(symbol="RELIANCE", roce_pct=17.8)),
        ]
    )
    result = await orchestrator.get_fundamentals("RELIANCE")
    assert result is not None
    assert result.pe == 24.0
    assert result.roce_pct == 17.8
    assert result.field_snapshots["roce_pct"].source == "TradingView"


async def test_does_not_choose_source_that_returned_nothing_for_field() -> None:
    """A source merely being configured/first-priority must not win a
    field it didn't actually report — only an explicit value counts."""
    orchestrator = MultiSourceFundamentalProvider(
        [
            _FakeProvider("Trendlyne", FundamentalData(symbol="RELIANCE")),  # nothing at all
            _FakeProvider("TradingView", FundamentalData(symbol="RELIANCE", pe=24.0)),
        ]
    )
    result = await orchestrator.get_fundamentals("RELIANCE")
    assert result is not None
    assert result.pe == 24.0
    assert result.field_snapshots["pe"].source == "TradingView"


async def test_raising_provider_does_not_break_the_others() -> None:
    orchestrator = MultiSourceFundamentalProvider(
        [_RaisingProvider(), _FakeProvider("Trendlyne", FundamentalData(symbol="TCS", pe=17.2))]
    )
    result = await orchestrator.get_fundamentals("TCS")
    assert result is not None
    assert result.pe == 17.2


class _StatusAwareFakeProvider(FundamentalDataProvider):
    """Test double implementing the `StatusAwareFundamentalProvider`
    protocol structurally (duck-typed — `MultiSourceFundamentalProvider`
    checks via `isinstance` against a `runtime_checkable` Protocol, so no
    explicit inheritance is needed)."""

    def __init__(
        self, name: str, *, data: FundamentalData | None, status: FetchStatus, error: str | None
    ) -> None:
        self.name = name
        self._data = data
        self._status = status
        self._error = error

    async def get_fundamentals(self, symbol: str) -> FundamentalData | None:
        return self._data

    async def get_fundamentals_with_status(
        self, symbol: str
    ) -> tuple[FundamentalData | None, FetchStatus, str | None]:
        return self._data, self._status, self._error


async def test_get_fundamentals_with_status_preserves_failed_reason() -> None:
    """Regression test: a single provider reporting FAILED with a real
    reason (e.g. "no resolvable ISIN") must have that reason surfaced,
    not silently discarded — this is exactly what let 350+ real fetch
    failures land in fundamental_fetch_log with an empty error_message."""
    orchestrator = MultiSourceFundamentalProvider(
        [
            _StatusAwareFakeProvider(
                "upstox", data=None, status=FetchStatus.FAILED, error="no resolvable ISIN for X"
            )
        ]
    )
    data, status, error = await orchestrator.get_fundamentals_with_status("X")
    assert data is None
    assert status == FetchStatus.FAILED
    assert error == "no resolvable ISIN for X"


async def test_get_fundamentals_with_status_rate_limited_wins_over_later_failed() -> None:
    orchestrator = MultiSourceFundamentalProvider(
        [
            _StatusAwareFakeProvider(
                "upstox", data=None, status=FetchStatus.RATE_LIMITED, error="rate limited"
            ),
            _StatusAwareFakeProvider(
                "trendlyne", data=None, status=FetchStatus.FAILED, error="no data"
            ),
        ]
    )
    data, status, error = await orchestrator.get_fundamentals_with_status("X")
    assert data is None
    assert status == FetchStatus.RATE_LIMITED
    assert error == "rate limited"


async def test_get_fundamentals_with_status_failed_does_not_override_earlier_rate_limit() -> None:
    """A later provider merely being FAILED (not rate-limited) must not
    demote an already-seen RATE_LIMITED verdict back to FAILED."""
    orchestrator = MultiSourceFundamentalProvider(
        [
            _StatusAwareFakeProvider(
                "a", data=None, status=FetchStatus.RATE_LIMITED, error="rate limited by a"
            ),
            _StatusAwareFakeProvider("b", data=None, status=FetchStatus.FAILED, error="b failed"),
        ]
    )
    data, status, error = await orchestrator.get_fundamentals_with_status("X")
    assert data is None
    assert status == FetchStatus.RATE_LIMITED
    assert error == "rate limited by a"


async def test_get_fundamentals_with_status_returns_data_when_a_source_succeeds() -> None:
    orchestrator = MultiSourceFundamentalProvider(
        [
            _StatusAwareFakeProvider(
                "upstox",
                data=FundamentalData(symbol="X", pe=20.0),
                status=FetchStatus.SUCCESS,
                error=None,
            )
        ]
    )
    data, status, error = await orchestrator.get_fundamentals_with_status("X")
    assert data is not None
    assert data.pe == 20.0
    assert status == FetchStatus.SUCCESS
    assert error is None


async def test_risk_notes_merged_across_sources() -> None:
    orchestrator = MultiSourceFundamentalProvider(
        [
            _FakeProvider("Trendlyne", FundamentalData(symbol="X", risk_notes=["High pledge"])),
            _FakeProvider(
                "TradingView",
                FundamentalData(symbol="X", risk_notes=["High pledge", "Auditor concern"]),
            ),
        ]
    )
    result = await orchestrator.get_fundamentals("X")
    assert result is not None
    assert result.risk_notes == ["High pledge", "Auditor concern"]
