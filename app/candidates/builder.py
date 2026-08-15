"""Builds a fully-scored `StockCandidate` for one symbol.

This is the shared async prep step every candidate-based scanner's
`build_context` runs before handing off to its own synchronous
`validate`/`scan`/`score` — mirroring how `BaseScanner.build_context`'s
default implementation builds `ScanContext` for `breakout_v1`. Fetching
fundamentals, scoring technicals, and running the pre-breakout state
machine only needs to happen once per symbol, not once per scanner, so
every candidate scanner (`fno_momentum_v1`, `pre_breakout_v1`,
`ipo_intraday_v1`) calls this same function with its own `universe`/
`scanner_name`.
"""

from dataclasses import asdict, dataclass
from datetime import date as date_
from datetime import datetime as datetime_
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.candidates.models import SetupState, StockCandidate, Universe
from app.candidates.sources import CandidateSourceProvider, primary_scanner_source
from app.config.settings import Settings
from app.decision.models import Quality
from app.fundamentals.queue_models import FetchStatus
from app.fundamentals.scorer import FundamentalScorer
from app.models.daily_feature import DailyFeature
from app.models.symbol import Symbol
from app.repositories.feature_repository import DailyFeatureRepository, SessionFeatureRepository
from app.repositories.market_repository import PriceRepository
from app.technical.inputs import TechnicalInputs
from app.technical.scorer import TechnicalScorer

_SNAPSHOT_EXCLUDED_COLUMNS = {"id", "symbol_id", "created_at", "updated_at"}

_HIGH_QUALITY_SCORE = 75.0
_MEDIUM_QUALITY_SCORE = 55.0


@dataclass
class CandidateBuildResult:
    candidate: StockCandidate | None
    skip_reason: str | None


def _daily_feature_snapshot(daily: DailyFeature) -> dict[str, object]:
    snapshot: dict[str, object] = {}
    for column in daily.__table__.columns:
        if column.name in _SNAPSHOT_EXCLUDED_COLUMNS:
            continue
        value = getattr(daily, column.name)
        if isinstance(value, Decimal):
            value = str(value)
        elif isinstance(value, datetime_ | date_):
            value = value.isoformat()
        snapshot[column.name] = value
    return snapshot


def factor_to_dict(factor: object) -> dict[str, object]:
    """Converts a `FundamentalFactor`/`TechnicalFactor` dataclass (each
    carries its own `StrEnum` status) into a JSON-serializable dict."""
    result: dict[str, object] = asdict(factor)  # type: ignore[call-overload]
    if "status" in result:
        result["status"] = str(result["status"])
    return result


def field_snapshot_to_dict(snapshot: object) -> dict[str, object]:
    """Converts a `FieldSnapshot` (StrEnum status, optional date, list of
    (source, value) alternate tuples) into a JSON-serializable dict."""
    result: dict[str, object] = asdict(snapshot)  # type: ignore[call-overload]
    if "status" in result:
        result["status"] = str(result["status"])
    as_of = result.get("as_of")
    if as_of is not None and hasattr(as_of, "isoformat"):
        result["as_of"] = as_of.isoformat()
    return result


def quality_from_score(score: float) -> Quality:
    if score >= _HIGH_QUALITY_SCORE:
        return Quality.HIGH
    if score >= _MEDIUM_QUALITY_SCORE:
        return Quality.MEDIUM
    return Quality.LOW


def combine_scores(
    fundamental_score: float | None, technical_score: float, settings: Settings
) -> float:
    """Weighted blend of fundamental + technical, per `Settings.overall_*_weight`.
    When the Fundamental Score is UNKNOWN (no data source — see
    `app.fundamentals`), the overall score is carried entirely by the
    Technical Score rather than penalizing the candidate for a data gap
    that isn't its fault."""
    if fundamental_score is None:
        return technical_score
    return (
        fundamental_score * settings.overall_fundamental_weight
        + technical_score * settings.overall_technical_weight
    )


def _detect_setup_state(
    daily: DailyFeature, price: Decimal, settings: Settings
) -> SetupState | None:
    """A documented, configurable heuristic — not a claim of certainty.

    - PRE_BREAKOUT: price sits below `resistance_level`, within
      `pre_breakout_proximity_pct` of it — approaching, not there yet.
    - BREAKOUT_CONFIRMED: price is at/just through resistance (within the
      same proximity band, on the other side) *with* volume confirmation
      (relative_volume >= `fno_momentum_min_rvol`) — the breakout bar
      itself. Do NOT call this state without that volume confirmation;
      a low-volume poke through resistance is not a confirmed breakout.
    - MOMENTUM: price is well past resistance (beyond the proximity band)
      and the trend is still strong (ADX >= `fno_momentum_min_adx`) —
      continuation after an earlier breakout, not the breakout bar.
    - None: no resistance level known, or none of the above conditions hold.
    """
    resistance = daily.resistance_level
    if resistance is None or resistance == 0:
        return None

    price_f = float(price)
    resistance_f = float(resistance)
    pct_above = (price_f / resistance_f - 1) * 100
    proximity = settings.pre_breakout_proximity_pct

    rvol = float(daily.relative_volume) if daily.relative_volume is not None else 0.0
    adx = float(daily.adx14) if daily.adx14 is not None else 0.0

    if -proximity <= pct_above < 0:
        return SetupState.PRE_BREAKOUT
    if 0 <= pct_above <= proximity and rvol >= settings.fno_momentum_min_rvol:
        return SetupState.BREAKOUT_CONFIRMED
    if pct_above > proximity and adx >= settings.fno_momentum_min_adx:
        return SetupState.MOMENTUM
    return None


async def _resolve_scanner_sources(
    *,
    symbol: Symbol,
    universe: Universe,
    session: AsyncSession,
    source_providers: list[CandidateSourceProvider] | None,
    settings: Settings,
) -> list[str]:
    """Every candidate this pipeline produces is attributed to whichever
    provider is actually driving the scanner pipeline right now (see
    `app.candidates.sources.primary_scanner_source`) — the only thing
    left to resolve is whether any *additional* configured source (e.g.
    `TradingViewSourceProvider`) independently flagged the same symbol.
    This does not re-run or duplicate technical scoring; it only adds to
    `scanner_sources` metadata on the single candidate already being
    built."""
    sources = {primary_scanner_source(settings).value}
    for provider in source_providers or []:
        found = await provider.discover(universe, session)
        if symbol.symbol in found:
            sources.add(provider.name.value)
    return sorted(sources)


async def build_candidate(
    *,
    symbol: Symbol,
    universe: Universe,
    scanner_name: str,
    session: AsyncSession,
    fundamental_scorer: FundamentalScorer,
    technical_scorer: TechnicalScorer,
    settings: Settings,
    source_providers: list[CandidateSourceProvider] | None = None,
) -> CandidateBuildResult:
    daily = await DailyFeatureRepository(session).get_latest(symbol.id)
    if daily is None:
        return CandidateBuildResult(None, "no features computed yet")

    latest_price = await PriceRepository(session).get_latest_daily(symbol.id)
    price = latest_price.close if latest_price else None
    if price is None:
        return CandidateBuildResult(None, "no price available")

    session_feature = await SessionFeatureRepository(session).get_latest(symbol.id)

    technical_inputs = TechnicalInputs.from_models(
        price=float(price), daily=daily, session=session_feature
    )
    technical_result = technical_scorer.score(technical_inputs)
    setup_state = _detect_setup_state(daily, price, settings)

    # Fundamentals are fetched by the Fundamental Queue (see
    # app/fundamentals/queue_service.py), never inline here — a scan can
    # discover 500-700+ candidates, and firing a Trendlyne request per
    # candidate synchronously exhausted the account's rate limit in
    # minutes. build_candidate() only ever marks a candidate PENDING;
    # the queue processes candidates later, in paced, prioritized
    # batches, and updates the persisted row directly. This is also why
    # the technical scanner never blocks on Trendlyne.
    fundamental_result = fundamental_scorer.score(None)

    overall_score = combine_scores(fundamental_result.score, technical_result.score, settings)
    scanner_sources = await _resolve_scanner_sources(
        symbol=symbol,
        universe=universe,
        session=session,
        source_providers=source_providers,
        settings=settings,
    )

    snapshot = _daily_feature_snapshot(daily)
    if session_feature is not None and session_feature.session_vwap is not None:
        snapshot["session_vwap"] = str(session_feature.session_vwap)

    candidate = StockCandidate(
        symbol=symbol.symbol,
        instrument_type="EQUITY",
        universe=universe,
        scanner_type=scanner_name,
        price=price,
        breakout_level=daily.breakout_level,
        support_level=daily.support_level,
        resistance_level=daily.resistance_level,
        fundamental_score=fundamental_result.score,
        technical_score=technical_result.score,
        overall_score=round(min(max(overall_score, 0.0), 100.0), 2),
        quality=quality_from_score(overall_score).value,
        scanner_sources=scanner_sources,
        # Only the *positive* reasons — this feeds the alert's "Why" bullets
        # directly (see app/alerts/formatter.py), and mixing in unfavorable
        # factors there would read as contradictory on an ALERT message.
        # The full breakdown (favorable, unfavorable, and unknown factors
        # alike) is still available via the API for audit — it's just not
        # in `fundamental_reasons`/`technical_reasons`.
        fundamental_reasons=list(fundamental_result.positive_reasons),
        technical_reasons=list(technical_result.positive_reasons),
        risk_flags=list(fundamental_result.risk_flags),
        passed_rules=[],
        failed_rules=[],
        data_completeness_pct=fundamental_result.data_completeness_pct,
        technical_data_completeness_pct=technical_result.data_completeness_pct,
        setup_state=setup_state,
        alert_category=None,
        reason="",
        fundamental_factors=[factor_to_dict(f) for f in fundamental_result.factors],
        technical_factors=[factor_to_dict(f) for f in technical_result.factors],
        fundamental_field_sources=[],
        fundamental_status=FetchStatus.PENDING.value,
        technical_feature_snapshot=snapshot,
    )
    return CandidateBuildResult(candidate, None)
