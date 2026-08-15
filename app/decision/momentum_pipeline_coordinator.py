"""MomentumPipelineCoordinator: wires the full

    Scanner -> Candidate -> Feature evidence -> SignalFusion ->
    Momentum state -> DecisionEngine -> AlertManager -> Telegram

pipeline together, calling each existing engine in order without
merging their logic into one another. Mirrors `app.decision.engine.DecisionEngine`'s
own shape (reads qualified `scanner_results`, iterates, per-candidate
error isolation) — this is the momentum-state-driven sibling to that
existing, unmodified, rule-based decision path.

**Responsibility boundaries, kept separate on purpose:**
- Scanner -> Candidate: reuses `ScannerResultRepository.list_results(status="qualified")`
  exactly like `DecisionEngine` does — no new candidate concept invented.
- Feature evidence -> SignalFusion: `app.signals.signal_fusion_engine.SignalFusionEngine`
  (Phase 11, untouched) computes the composite score; "feature evidence"
  is already fully encapsulated inside its own Technical component, not
  a separate step here.
- Momentum state: `app.momentum.momentum_engine.MomentumStateEngine`
  (Phase 12, untouched) evaluates the state transition and — this is the
  *only* place `AlertManager.process()` is called anywhere in this
  path — reuses queueing, deduplication, cooldown, and throttling
  exactly as Phase 12 built them. This coordinator never calls
  `AlertManager` a second time.
- DecisionEngine: `app.decision.momentum_decision_engine.MomentumDecisionEngine`
  (this phase) only *labels* the verdict from what already happened —
  it never itself alerts.
- Telegram: `app.notifications.manager.NotificationManager` consumes the
  same `AlertQueue` as always; nothing here or upstream changes it.

**No synchronous external news/fundamental calls**: `SignalFusionEngine`
is constructed below with no `news_provider` — this coordinator has no
parameter to supply one, so a caller cannot accidentally wire a live
network call into this path. Fundamentals only ever come from
`FundamentalSnapshotRepository`'s cache (see `SignalFusionEngine`'s own
docstring) — never a live provider call either.

**Phase 16 (live paper/simulation mode)**: `app.scheduler.momentum_pipeline_jobs`
is the only caller that runs this continuously against live Upstox data
— never automated trading, never a backtest; `_record_observation` below
just writes one factual `momentum_alert_observations` row per alert-worthy
transition (trigger time, score, evidence, and how stale the underlying
price already was), for `app.scheduler.momentum_observation_followup_jobs`
to later fill in with real subsequent prices. This coordinator still
never places an order and never will — there is no order-execution code
anywhere in this project.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.manager import AlertManager
from app.analytics.market.market_models import MarketRegimeEvidence
from app.config.settings import Settings
from app.core.time import utc_now
from app.decision.momentum_decision_engine import MomentumDecisionEngine
from app.decision.momentum_decision_models import PipelineDecisionResult, PipelineVerdict
from app.momentum.momentum_engine import MomentumStateEngine
from app.momentum.momentum_models import ALERT_WORTHY_STATES, MomentumState
from app.repositories.market_repository import PriceRepository, SymbolRepository
from app.repositories.momentum_alert_observation_repository import (
    MomentumAlertObservationRepository,
    NewObservation,
)
from app.repositories.momentum_state_repository import MomentumStateRepository
from app.repositories.scanner_repository import ScannerResultRepository
from app.signals.signal_fusion_engine import SignalFusionEngine


@dataclass
class PipelineRunResult:
    candidates_evaluated: int = 0
    trigger_count: int = 0
    watch_count: int = 0
    reject_count: int = 0
    invalidate_count: int = 0
    alerts_created: int = 0
    errors: list[str] = field(default_factory=list)


class MomentumPipelineCoordinator:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        alert_manager: AlertManager,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._signal_fusion = SignalFusionEngine(session_factory, settings)  # no news_provider
        self._momentum_engine = MomentumStateEngine(session_factory, settings, alert_manager)
        self._decision_engine = MomentumDecisionEngine(settings)

    async def run_all(
        self,
        *,
        market_regime: MarketRegimeEvidence | None = None,
        now: datetime | None = None,
    ) -> PipelineRunResult:
        result = PipelineRunResult()
        moment = now or utc_now()

        async with self._session_factory() as session:
            qualified = await ScannerResultRepository(session).list_results(
                status="qualified", limit=5000
            )
            symbol_ids = {r.symbol_id for r in qualified}
            symbols = {
                s.id: s.symbol
                for s in await SymbolRepository(session).list_by_ids(list(symbol_ids))
            }

        seen_symbols: set[str] = set()
        for scanner_result in qualified:
            symbol_name = symbols.get(scanner_result.symbol_id)
            if symbol_name is None or symbol_name in seen_symbols:
                continue
            seen_symbols.add(symbol_name)
            result.candidates_evaluated += 1

            try:
                decision = await self._evaluate_one(symbol_name, market_regime, moment)
                if decision.verdict == PipelineVerdict.TRIGGER:
                    result.trigger_count += 1
                    if decision.alert_id is not None:
                        result.alerts_created += 1
                elif decision.verdict == PipelineVerdict.WATCH:
                    result.watch_count += 1
                elif decision.verdict == PipelineVerdict.REJECT:
                    result.reject_count += 1
                else:
                    result.invalidate_count += 1
            except Exception as exc:  # noqa: BLE001 - isolate per-candidate failures
                logger.exception(
                    "Momentum pipeline failed for symbol={symbol}", symbol=symbol_name
                )
                result.errors.append(f"{symbol_name}: {exc}")

        if result.candidates_evaluated:
            logger.info(
                "Momentum pipeline run: {evaluated} evaluated, {trigger} trigger, "
                "{watch} watch, {reject} reject, {invalidate} invalidate, {alerts} alerts",
                evaluated=result.candidates_evaluated,
                trigger=result.trigger_count,
                watch=result.watch_count,
                reject=result.reject_count,
                invalidate=result.invalidate_count,
                alerts=result.alerts_created,
            )
        return result

    async def _evaluate_one(
        self, symbol: str, market_regime: MarketRegimeEvidence | None, moment: datetime
    ) -> PipelineDecisionResult:
        fusion = await self._signal_fusion.compute(symbol, market_regime=market_regime)

        below_confidence = fusion.confidence < self._settings.pipeline_min_confidence_pct
        if fusion.overall_score is None or below_confidence:
            reason = (
                "no signal fusion evidence available at all"
                if fusion.overall_score is None
                else f"confidence={fusion.confidence:.2f}% below minimum "
                f"({self._settings.pipeline_min_confidence_pct:.2f}%)"
            )
            return PipelineDecisionResult(
                symbol=symbol,
                verdict=PipelineVerdict.REJECT,
                reason=reason,
                momentum_state=None,
                signal_score=fusion.overall_score,
                confidence=fusion.confidence,
                alert_id=None,
                timestamp=moment,
            )

        # Full per-component breakdown (status/score/weight/reasons for
        # each of SignalFusionEngine's 7 inputs), not just the rolled-up
        # positive/negative factor lists — this is what lets
        # app.alerts.formatter answer "why did this stock trigger?"
        # section-by-section (TECHNICAL/VOLUME/OI/SECTOR/MARKET/
        # FUNDAMENTALS/NEWS) using only deterministic, already-computed
        # values. Persisted as-is via StateTransition.evidence -> the
        # alert's feature_snapshot, no separate query needed later.
        evidence: dict[str, object] = {
            "overall_score": fusion.overall_score,
            "confidence": fusion.confidence,
            "positive_factors": fusion.positive_factors,
            "negative_factors": fusion.negative_factors,
            "missing_data": fusion.missing_data,
            "component_scores": {
                name: {
                    "status": component.status.value,
                    "score": component.score,
                    "weight": component.weight,
                    "reasons": component.reasons,
                }
                for name, component in fusion.component_scores.items()
            },
        }

        momentum_result = await self._momentum_engine.evaluate(
            symbol, fusion.overall_score, evidence, now=moment
        )

        momentum_state: MomentumState | None
        if momentum_result.transition is not None:
            momentum_state = momentum_result.transition.to_state
            if momentum_state in ALERT_WORTHY_STATES and momentum_result.transition_id is not None:
                await self._record_observation(
                    symbol=symbol,
                    transition_id=momentum_result.transition_id,
                    alert_id=momentum_result.alert_id,
                    momentum_state=momentum_state,
                    score=fusion.overall_score,
                    confidence=fusion.confidence,
                    moment=moment,
                )
        else:
            # No new transition this cycle (e.g. still holding at CONFIRMED
            # or WATCH) — the verdict must still reflect the symbol's real
            # *current* standing state, not be mislabeled REJECT just
            # because nothing changed on this particular evaluation.
            async with self._session_factory() as session:
                symbol_row = await SymbolRepository(session).get_by_symbol(symbol)
                current_record = (
                    await MomentumStateRepository(session).get_current(symbol_row.id)
                    if symbol_row is not None
                    else None
                )
                momentum_state = MomentumStateRepository.state_of(current_record)

        verdict, reason = self._decision_engine.decide(
            confidence=fusion.confidence, momentum_state=momentum_state
        )
        return PipelineDecisionResult(
            symbol=symbol,
            verdict=verdict,
            reason=reason,
            momentum_state=momentum_state,
            signal_score=fusion.overall_score,
            confidence=fusion.confidence,
            alert_id=momentum_result.alert_id,
            timestamp=moment,
        )

    async def _record_observation(
        self,
        *,
        symbol: str,
        transition_id: int,
        alert_id: int | None,
        momentum_state: MomentumState,
        score: float | None,
        confidence: float,
        moment: datetime,
    ) -> None:
        """Phase 16 operational validation: one row per alert-worthy
        transition, capturing exactly what the trigger looked like right
        now — the real price backing it and how stale that price already
        was — so a later, separate follow-up job can record what price
        actually did afterward. Never blocks or fails the pipeline itself:
        a symbol row missing here just means no observation is recorded,
        not a pipeline error (the alert/transition already happened and
        is unaffected)."""
        if score is None:
            return
        async with self._session_factory() as session:
            symbol_row = await SymbolRepository(session).get_by_symbol(symbol)
            if symbol_row is None:
                return

            latest_intraday = await PriceRepository(session).get_latest_intraday(symbol_row.id)
            as_of_data_at = latest_intraday.datetime if latest_intraday is not None else None
            # SQLite (this project's test backend) doesn't enforce
            # tz-aware storage for DateTime(timezone=True) — a value
            # written tz-aware can come back naive. Same fix as
            # FundamentalFetchLogRepository.most_recent_rate_limit.
            if as_of_data_at is not None and as_of_data_at.tzinfo is None:
                as_of_data_at = as_of_data_at.replace(tzinfo=UTC)
            data_age_seconds = (
                (moment - as_of_data_at).total_seconds() if as_of_data_at is not None else None
            )
            is_stale = (
                data_age_seconds is not None
                and data_age_seconds > self._settings.momentum_observation_stale_data_threshold_seconds
            )
            price_at_trigger = (
                Decimal(str(latest_intraday.close)) if latest_intraday is not None else None
            )

            await MomentumAlertObservationRepository(session).insert(
                NewObservation(
                    transition_id=transition_id,
                    symbol_id=symbol_row.id,
                    alert_id=alert_id,
                    momentum_state=momentum_state.value,
                    trigger_at=moment,
                    signal_score=Decimal(str(round(score, 2))),
                    signal_confidence=Decimal(str(round(confidence, 2))),
                    as_of_data_at=as_of_data_at,
                    data_age_seconds=data_age_seconds,
                    is_stale=is_stale,
                    price_at_trigger=price_at_trigger,
                )
            )
            await session.commit()
