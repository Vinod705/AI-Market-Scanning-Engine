"""SignalFusionEngine: combines the 7 independent analytics engines built
across this project into one explainable, per-symbol composite score.

**Scope discipline (explicit in this phase's own instructions)**: this is
a standalone scoring layer. It does not call, get called by, or import
`app.scanner`, `app.decision`, or `app.notifications` — scanning,
scoring, deciding, and notifying stay separate responsibilities. Nothing
in `app/main.py` constructs or schedules this engine; it exists as
available, tested infrastructure, same "layer exists, not wired in"
precedent every prior analytics phase in this project has followed.

**The 7 inputs, and exactly what each reuses** (nothing here recomputes
an existing engine's logic):
1. Technical — `app.technical.scorer.TechnicalScorer` (unchanged, same
   scorer `app.candidates.builder` already uses for F&O/IPO candidates).
2. Volume/RVOL — `DailyFeature.relative_volume`, normalized with the
   exact `min(rv/2, 1) * 100` convention `BreakoutScanner`/`VcpScanner`/
   `MomentumScanner`/`OrbScanner` already use for their own volume term.
   Kept independent of (1) on purpose — Technical's own internal
   composite already blends a volume factor at its own weight, but this
   phase asks for Volume/RVOL as its own explicit input; the two are not
   computing the same thing (Technical's volume factor is one of six
   internally-blended categories at a fraction of its own weight), but
   there is a documented, known partial overlap — not hidden here.
3. OI — `app.repositories.oi_repository.OiObservationRepository`
   (Phase 5). Only ever available for F&O-eligible symbols — MISSING,
   correctly, for the rest of the LISTED universe.
4. Sector/RRG — `app.analytics.rrg.stock_rrg.compute_stock_rrg` (Phase 6)
   for the symbol's own relative strength/momentum vs. the benchmark.
   True sector-level scoring (Phase 7) needs a caller-supplied sector
   symbol list and has no stock->sector membership data to resolve one
   automatically (confirmed absent, repeatedly, this project) — so this
   component is the stock's own RRG state, not a sector aggregate.
5. Market regime — `app.analytics.market.regime.MarketRegimeEvidence`
   (Phase 8). Market-wide, not stock-specific, so it is *injected* by the
   caller (already computed once for the whole scan cycle) rather than
   recomputed per symbol — recomputing a full LISTED-universe breadth
   scan inside a per-symbol call would be a severe, easy-to-miss
   performance bug (see `app/scanner/scanner_manager.py`'s and
   `app/features/engine.py`'s own throughput-fix history in this
   project). Omit it and this component is honestly MISSING.
6. News/catalyst — `app.catalyst.news_provider.NewsProvider` (Phase 10).
   Phase 10 built no cache (unlike fundamentals), so this is the one
   component that, if a provider is injected, makes a real live network
   call. Omit the provider and this component is honestly MISSING —
   never silently skipped without being reported as missing.
7. Fundamentals — `app.repositories.fundamental_snapshot_repository.FundamentalSnapshotRepository.get_cached`
   (Phase 9's non-blocking cache read, never a live provider call) +
   `app.fundamentals.scorer.FundamentalScorer` (Phase 7, unchanged).
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.market.market_models import MarketRegimeEvidence
from app.analytics.rrg.stock_rrg import compute_stock_rrg
from app.catalyst.catalyst_models import Freshness
from app.catalyst.catalyst_scorer import normalize as normalize_news
from app.catalyst.news_provider import NewsProvider
from app.config.settings import Settings
from app.core.time import utc_now
from app.fundamentals.scorer import FundamentalScorer
from app.models.daily_feature import DailyFeature
from app.models.daily_price import DailyPrice
from app.repositories.feature_repository import DailyFeatureRepository, SessionFeatureRepository
from app.repositories.fundamental_snapshot_repository import FundamentalSnapshotRepository
from app.repositories.market_repository import PriceRepository, SymbolRepository
from app.repositories.oi_repository import OiObservationRepository
from app.signals.signal_models import ComponentScore, DataStatus, SignalFusionResult
from app.technical.inputs import TechnicalInputs
from app.technical.scorer import TechnicalScorer


def _missing(name: str, weight: float, reason: str) -> ComponentScore:
    return ComponentScore(
        name=name, status=DataStatus.MISSING, score=None, weight=weight, reasons=[reason]
    )


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


@dataclass
class _FusionContext:
    session: AsyncSession
    settings: Settings
    technical_scorer: TechnicalScorer
    fundamental_scorer: FundamentalScorer


class SignalFusionEngine:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        *,
        news_provider: NewsProvider | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._news_provider = news_provider

    async def compute(
        self,
        symbol: str,
        *,
        market_regime: MarketRegimeEvidence | None = None,
    ) -> SignalFusionResult:
        settings = self._settings
        async with self._session_factory() as session:
            ctx = _FusionContext(
                session=session,
                settings=settings,
                technical_scorer=TechnicalScorer(settings),
                fundamental_scorer=FundamentalScorer(settings),
            )

            symbol_row = await SymbolRepository(session).get_by_symbol(symbol)
            if symbol_row is None:
                unknown_components = {
                    name: _missing(name, weight, f"unknown symbol {symbol!r}")
                    for name, weight in _weights(settings).items()
                }
                return _assemble(symbol, unknown_components, utc_now())

            daily = await DailyFeatureRepository(session).get_latest(symbol_row.id)
            latest_price = await PriceRepository(session).get_latest_daily(symbol_row.id)

            components: dict[str, ComponentScore] = {}
            components["technical"] = await _score_technical(ctx, symbol_row.id, daily, latest_price)
            components["volume"] = _score_volume(settings, daily)
            components["oi"] = await _score_oi(ctx, symbol_row.id)
            components["sector_rrg"] = await _score_sector_rrg(ctx, symbol)
            components["market_regime"] = _score_market_regime(settings, market_regime)
            components["news"] = await _score_news(ctx, symbol, self._news_provider)
            components["fundamentals"] = await _score_fundamentals(ctx, symbol_row.id)

            return _assemble(symbol, components, utc_now())


def _weights(settings: Settings) -> dict[str, float]:
    return {
        "technical": settings.signal_fusion_weight_technical,
        "volume": settings.signal_fusion_weight_volume,
        "oi": settings.signal_fusion_weight_oi,
        "sector_rrg": settings.signal_fusion_weight_sector_rrg,
        "market_regime": settings.signal_fusion_weight_market_regime,
        "news": settings.signal_fusion_weight_news,
        "fundamentals": settings.signal_fusion_weight_fundamentals,
    }


async def _score_technical(
    ctx: _FusionContext,
    symbol_id: int,
    daily: DailyFeature | None,
    latest_price: DailyPrice | None,
) -> ComponentScore:
    weight = ctx.settings.signal_fusion_weight_technical
    if daily is None or latest_price is None:
        return _missing("technical", weight, "no daily features/price computed yet")

    session_feature = await SessionFeatureRepository(ctx.session).get_latest(symbol_id)
    inputs = TechnicalInputs.from_models(
        price=float(latest_price.close), daily=daily, session=session_feature
    )
    result = ctx.technical_scorer.score(inputs)
    reasons = [f"Technical: {r}" for r in result.positive_reasons + result.negative_reasons]
    return ComponentScore(
        name="technical",
        status=DataStatus.AVAILABLE,
        score=result.score,
        weight=weight,
        reasons=reasons,
    )


def _score_volume(settings: Settings, daily: DailyFeature | None) -> ComponentScore:
    weight = settings.signal_fusion_weight_volume
    if daily is None or daily.relative_volume is None:
        return _missing("volume", weight, "no relative_volume computed yet")
    relative_volume = float(daily.relative_volume)
    score = _clamp(min(relative_volume / 2.0, 1.0) * 100)
    reason = f"Volume: relative_volume={relative_volume:.2f}x average"
    return ComponentScore(
        name="volume", status=DataStatus.AVAILABLE, score=score, weight=weight, reasons=[reason]
    )


async def _score_oi(ctx: _FusionContext, symbol_id: int) -> ComponentScore:
    weight = ctx.settings.signal_fusion_weight_oi
    observations = await OiObservationRepository(ctx.session).list_for_symbol(symbol_id, limit=20)
    futures = next((o for o in observations if o.instrument_type == "FUT"), None)
    if futures is None:
        return _missing("oi", weight, "no futures OI observation available (not F&O-eligible, or not yet observed)")

    bullish = {"LONG_BUILDUP", "SHORT_COVERING"}
    bearish = {"SHORT_BUILDUP", "LONG_UNWINDING"}
    if futures.classification in bullish:
        score = 75.0
    elif futures.classification in bearish:
        score = 25.0
    else:
        score = 50.0
    reason = f"OI: futures {futures.classification}"
    return ComponentScore(
        name="oi", status=DataStatus.AVAILABLE, score=score, weight=weight, reasons=[reason]
    )


async def _score_sector_rrg(ctx: _FusionContext, symbol: str) -> ComponentScore:
    weight = ctx.settings.signal_fusion_weight_sector_rrg
    points = await compute_stock_rrg(ctx.session, ctx.settings, symbol)
    if not points:
        return _missing("sector_rrg", weight, "insufficient price history for RRG (own or benchmark)")

    latest = points[-1]
    if latest.rs_ratio is None or latest.rs_momentum is None or latest.quadrant is None:
        return _missing("sector_rrg", weight, "not enough history yet for a stable RRG reading")

    # rs_ratio/rs_momentum are Z-scores centered at 100 (see rrg_engine.py)
    # — average the two deviations and rescale [-1,1]-ish into 0-100 the
    # same bounded-linear way app.analytics.market.breadth's
    # new_highs_lows_net_pct does, clamped for outliers.
    ratio_z = float(latest.rs_ratio) - 100
    momentum_z = float(latest.rs_momentum) - 100
    avg_z = _clamp(50 + ((ratio_z + momentum_z) / 2) * 20)
    reason = f"Sector/RRG: {latest.quadrant.value} vs {latest.benchmark_symbol}"
    return ComponentScore(
        name="sector_rrg", status=DataStatus.AVAILABLE, score=avg_z, weight=weight, reasons=[reason]
    )


def _score_market_regime(settings: Settings, market_regime: MarketRegimeEvidence | None) -> ComponentScore:
    weight = settings.signal_fusion_weight_market_regime
    if market_regime is None:
        return _missing(
            "market_regime", weight, "no MarketRegimeEvidence supplied for this scan cycle"
        )
    if market_regime.score is None or market_regime.regime is None:
        return _missing("market_regime", weight, "market regime had no evidence at all")

    status = DataStatus.NEUTRAL if market_regime.regime.value == "NEUTRAL" else DataStatus.AVAILABLE
    reason = f"Market regime: {market_regime.regime.value}"
    return ComponentScore(
        name="market_regime",
        status=status,
        score=float(market_regime.score),
        weight=weight,
        reasons=[reason],
    )


async def _score_news(
    ctx: _FusionContext, symbol: str, news_provider: NewsProvider | None
) -> ComponentScore:
    weight = ctx.settings.signal_fusion_weight_news
    if news_provider is None:
        return _missing("news", weight, "no NewsProvider supplied (live-call opt-in)")

    raw_by_symbol = await news_provider.get_news([symbol])
    articles = raw_by_symbol.get(symbol, [])
    now = utc_now()
    items = [normalize_news(a, ctx.settings, now=now) for a in articles]
    active = [item for item in items if item.freshness != Freshness.STALE]
    if not active:
        return _missing("news", weight, "no recent (non-stale) news found")

    materiality_weight = {"HIGH": 3.0, "MEDIUM": 2.0, "LOW": 1.0}
    sentiment_value = {"POSITIVE": 1.0, "NEGATIVE": -1.0, "NEUTRAL": 0.0}
    total_weight = sum(materiality_weight[item.materiality.value] for item in active)
    weighted_sentiment = (
        sum(
            sentiment_value[item.sentiment.value] * materiality_weight[item.materiality.value]
            for item in active
        )
        / total_weight
    )
    score = _clamp(50 + weighted_sentiment * 50)
    reasons = [
        f"News: {item.event_type.value} ({item.sentiment.value}) — {item.headline} "
        f"[{item.source}, {item.published_at.strftime('%Y-%m-%d %H:%M UTC')}]"
        for item in active[:3]
    ]
    return ComponentScore(
        name="news", status=DataStatus.AVAILABLE, score=score, weight=weight, reasons=reasons
    )


async def _score_fundamentals(ctx: _FusionContext, symbol_id: int) -> ComponentScore:
    weight = ctx.settings.signal_fusion_weight_fundamentals
    cached = await FundamentalSnapshotRepository(ctx.session, ctx.settings).get_cached(symbol_id)
    if cached is None or cached.data is None:
        return _missing("fundamentals", weight, "no cached fundamental snapshot yet")

    result = ctx.fundamental_scorer.score(cached.data)
    if result.score is None:
        return _missing("fundamentals", weight, "fundamental tier is UNKNOWN (no usable factors)")

    freshness_note = "fresh" if cached.is_fresh else "stale cache, last known value"
    all_reasons = result.positive_reasons + result.negative_reasons
    reasons = [f"Fundamentals ({freshness_note}): {r}" for r in all_reasons]
    return ComponentScore(
        name="fundamentals",
        status=DataStatus.AVAILABLE,
        score=result.score,
        weight=weight,
        reasons=reasons,
    )


def _assemble(
    symbol: str, components: dict[str, ComponentScore], timestamp: datetime
) -> SignalFusionResult:
    available = {name: c for name, c in components.items() if c.status != DataStatus.MISSING}
    total_configured_weight = sum(c.weight for c in components.values())
    available_weight = sum(c.weight for c in available.values())

    overall_score: float | None = None
    if available and available_weight > 0:
        weighted_sum = sum((c.score or 0.0) * c.weight for c in available.values())
        overall_score = round(_clamp(weighted_sum / available_weight), 2)

    confidence = (
        round((available_weight / total_configured_weight) * 100, 2)
        if total_configured_weight > 0
        else 0.0
    )

    positive_factors: list[str] = []
    negative_factors: list[str] = []
    for component in available.values():
        if component.score is None:
            continue
        bucket = positive_factors if component.score >= 50 else negative_factors
        bucket.extend(component.reasons)

    missing_data = [name for name, c in components.items() if c.status == DataStatus.MISSING]

    return SignalFusionResult(
        symbol=symbol,
        overall_score=overall_score,
        component_scores=components,
        positive_factors=positive_factors,
        negative_factors=negative_factors,
        missing_data=missing_data,
        confidence=confidence,
        timestamp=timestamp,
    )
