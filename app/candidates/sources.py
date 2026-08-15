"""Candidate-discovery sources — Phase 7's dual-scanner architecture.

The base scanner pipeline (`app.scanner`/`app.candidates.builder`) is not
a separate lookup with its own provider instance: it's whichever
`MarketDataProvider` is configured as `Settings.active_market_data_provider`
(Upstox by default since Phase 2; 5paisa remains selectable as the legacy
option) — every candidate it produces is attributed to that provider by
construction, resolved via `primary_scanner_source()` below rather than
hardcoded, so the label stays correct if the active provider changes.
`CandidateSourceProvider` is the extension point for an *additional*,
independently-flagging source (see TradingView below) on top of that base
attribution — a symbol stays eligible whether one or both flag it, and
the existing Technical Analysis engine still computes the technical score
exactly once per symbol regardless of how many sources found it.

TradingView status (see `TradingViewSourceProvider`): investigated and
confirmed NOT wireable as an automated backend source in this deployment
— see that class's docstring for exactly why. This stub exists so the
aggregation/dedup machinery, the dashboard's Scanner Discovery block, and
the health check all have a real (if currently empty) second source to
work against, and so a genuine TradingView provider can be dropped in
later without touching anything else in this file, `builder.py`, or the
scanners.
"""

from abc import ABC, abstractmethod
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from app.candidates.models import Universe
from app.config.settings import Settings


class ScannerSource(StrEnum):
    FIVEPAISA = "5PAISA"
    UPSTOX = "UPSTOX"
    TRADINGVIEW = "TRADINGVIEW"


def primary_scanner_source(settings: Settings) -> ScannerSource:
    """The base scanner pipeline's own attribution — mirrors whichever
    provider `Settings.active_market_data_provider` actually selects, so
    this never drifts out of sync with reality the way a hardcoded label
    would (as it did before this function existed: every candidate was
    labeled "5PAISA" even after Upstox became the default provider)."""
    return (
        ScannerSource.UPSTOX
        if settings.active_market_data_provider == "upstox"
        else ScannerSource.FIVEPAISA
    )


class CandidateSourceProvider(ABC):
    """An additional (non-5paisa) candidate-discovery source.

    `discover()` returns the set of symbol strings this source
    independently flags as worth considering for `universe`, on top of
    whatever the existing 5paisa-driven scanner pipeline already found.
    It must never raise for "no data available" — return an empty set
    instead, exactly like `FundamentalDataProvider` returns `None` rather
    than fabricating zeros (see `app.fundamentals.provider`).

    Since Phase 1, `ScannerEngine.run_all()` (which calls this) runs inside
    `app.pipeline.worker.PipelineWorker`'s hot loop — a real implementation
    here must stay non-blocking/local (cached/pre-fetched data, no
    synchronous external HTTP call made from inside `discover()` itself),
    the same "no slow external API calls in the hot path" constraint the
    rest of that pipeline already holds to.
    """

    name: ScannerSource

    @abstractmethod
    async def discover(self, universe: Universe, session: AsyncSession) -> set[str]:
        raise NotImplementedError


class TradingViewSourceProvider(CandidateSourceProvider):
    """Architectural placeholder — always returns an empty set.

    Investigated during Phase 7 discovery: the `mcp__tradingview__*` tool
    surface automates an interactive TradingView Desktop client attached
    to a live Claude conversation session. It is not a network-callable
    API this deployed FastAPI backend can invoke on its own scheduler
    cycle — there is no server-side credential, endpoint, or webhook to
    call from `app/scheduler/`. Independent of that, the tool surface
    itself has no bulk screener/scanner endpoint either: `symbol_search`
    is a single-query name lookup, `watchlist_get` reads a manually
    curated desktop watchlist, and `batch_run` requires a symbol list to
    already be known — none of these discover new candidates from the
    market the way `UniverseProvider` + the scanner pipeline does for
    5paisa.

    No scraping, browser automation, or undocumented-endpoint use was
    considered or attempted to work around this. Per the spec: preserve
    the architecture so a real TradingView scanner provider — e.g. once
    an official server-callable screener API exists — can replace this
    class without any change to `builder.py`, the scanners, the
    aggregation/dedup logic, the dashboard, or the health check.
    """

    name = ScannerSource.TRADINGVIEW

    async def discover(self, universe: Universe, session: AsyncSession) -> set[str]:
        return set()
