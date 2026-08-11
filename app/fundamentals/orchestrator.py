"""MultiSourceFundamentalProvider: merges `FundamentalData` from multiple
providers in priority order, field by field — not "pick one provider's
whole response," but "for each individual field, use the highest-priority
source that actually reported it."

Currently constructed (see `app/main.py`) with only
`TrendlyneFundamentalDataProvider` in the list: Trendlyne is the only
source with an authorized, server-callable interface. TradingView and
5paisa were both investigated for a fundamentals path a production
backend could call automatically — neither has one (see the Phase 7
multi-source final report) — so no provider for either exists to add
here. This orchestrator exists so a real second/third source can be
dropped into the list later without touching anything else in the
candidate/decision/alert pipeline, exactly like `TradingViewSourceProvider`
in `app.candidates.sources` does for scanner-level candidate discovery.

A provider that raises is treated the same as one that returns None for
that symbol — never lets one source's bug take down the others.
"""

from dataclasses import fields
from typing import Protocol, runtime_checkable

from loguru import logger

from app.fundamentals.models import FieldAvailability, FieldSnapshot, FundamentalData
from app.fundamentals.provider import FundamentalDataProvider
from app.fundamentals.queue_models import FetchStatus


@runtime_checkable
class StatusAwareFundamentalProvider(Protocol):
    """Providers that can distinguish *why* a fetch produced no data (rate
    limit vs. genuine failure vs. cache hit) implement this in addition to
    the plain `FundamentalDataProvider` interface — only the Fundamental
    Queue needs the distinction, so it's kept out of the base ABC."""

    async def get_fundamentals_with_status(
        self, symbol: str
    ) -> tuple[FundamentalData | None, FetchStatus, str | None]: ...


_NON_MERGEABLE_FIELDS = {"symbol", "as_of", "risk_notes", "field_snapshots"}
_MERGEABLE_FIELDS = [f.name for f in fields(FundamentalData) if f.name not in _NON_MERGEABLE_FIELDS]


class MultiSourceFundamentalProvider(FundamentalDataProvider):
    name = "multi_source"

    def __init__(self, providers_in_priority_order: list[FundamentalDataProvider]) -> None:
        self._providers = providers_in_priority_order

    async def get_fundamentals(self, symbol: str) -> FundamentalData | None:
        results: list[tuple[str, FundamentalData]] = []
        for provider in self._providers:
            try:
                data = await provider.get_fundamentals(symbol)
            except Exception as exc:  # noqa: BLE001 - one source's bug must never break the rest
                logger.warning(
                    "{provider} raised while fetching {symbol}: {error}",
                    provider=provider.name,
                    symbol=symbol,
                    error=exc,
                )
                data = None
            if data is not None:
                results.append((provider.name, data))

        if not results:
            return None

        merged = FundamentalData(symbol=symbol, as_of=results[0][1].as_of)
        for field_name in _MERGEABLE_FIELDS:
            snapshot = _merge_field(field_name, results)
            if snapshot is not None:
                setattr(merged, field_name, snapshot.value)
                merged.field_snapshots[field_name] = snapshot

        merged.risk_notes = _merge_risk_notes(results)
        return merged

    async def get_fundamentals_with_status(
        self, symbol: str
    ) -> tuple[FundamentalData | None, FetchStatus, str | None]:
        """Used by the Fundamental Queue instead of `get_fundamentals()` so
        it can tell a rate limit apart from "no data" and pause instead of
        continuing to hammer the source. Tries each provider in priority
        order; a RATE_LIMITED result from a higher-priority source doesn't
        stop a lower-priority one from being tried (a different source may
        not be rate-limited at all) — but if every provider reports the
        same rate-limited condition, that's what's returned so the queue
        pauses rather than treating it as a plain data gap."""
        worst_status = FetchStatus.FAILED
        last_error: str | None = None
        for provider in self._providers:
            if isinstance(provider, StatusAwareFundamentalProvider):
                try:
                    data, status, error = await provider.get_fundamentals_with_status(symbol)
                except (
                    Exception
                ) as exc:  # noqa: BLE001 - one source's bug must never break the rest
                    logger.warning(
                        "{provider} raised while fetching {symbol}: {error}",
                        provider=provider.name,
                        symbol=symbol,
                        error=exc,
                    )
                    status, error = FetchStatus.FAILED, str(exc)
                    data = None
            else:
                try:
                    data = await provider.get_fundamentals(symbol)
                    status, error = (
                        (FetchStatus.SUCCESS, None)
                        if data is not None
                        else (FetchStatus.FAILED, None)
                    )
                except Exception as exc:  # noqa: BLE001
                    data, status, error = None, FetchStatus.FAILED, str(exc)

            if data is not None:
                return data, status, error
            if status == FetchStatus.RATE_LIMITED:
                worst_status, last_error = status, error

        return None, worst_status, last_error


def _merge_field(
    field_name: str, results: list[tuple[str, FundamentalData]]
) -> FieldSnapshot | None:
    """Picks the highest-priority (first, since `results` is already in
    priority order) source that has a non-None value for `field_name`.
    Every other source's differing value is recorded as an alternate —
    never silently averaged, never silently discarded (Part 6: conflict
    handling must be visible, not hidden)."""
    chosen: FieldSnapshot | None = None
    alternates: list[tuple[str, float]] = []
    for source_name, data in results:
        value = getattr(data, field_name)
        if value is None:
            continue
        if chosen is None:
            provider_snapshot = data.field_snapshots.get(field_name)
            chosen = FieldSnapshot(
                field_name=field_name,
                value=value,
                source=provider_snapshot.source if provider_snapshot else source_name,
                period=provider_snapshot.period if provider_snapshot else None,
                status=FieldAvailability.AVAILABLE,
                as_of=provider_snapshot.as_of if provider_snapshot else None,
            )
        elif value != chosen.value:
            alternates.append((source_name, value))
    if chosen is not None:
        chosen.alternates = alternates
    return chosen


def _merge_risk_notes(results: list[tuple[str, FundamentalData]]) -> list[str]:
    """Union of every source's risk notes, not just the highest-priority
    source's — a risk flagged by any reliable source is still a risk."""
    seen: set[str] = set()
    combined: list[str] = []
    for _source_name, data in results:
        for note in data.risk_notes:
            if note not in seen:
                seen.add(note)
                combined.append(note)
    return combined
