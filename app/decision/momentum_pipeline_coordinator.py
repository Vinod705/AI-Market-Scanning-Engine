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
"""

from dataclasses import dataclass, field
from datetime import datetime

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.manager import AlertManager
from app.analytics.market.market_models import MarketRegimeEvidence
from app.config.settings import Settings
from app.core.time import utc_now
from app.decision.momentum_decision_engine import MomentumDecisionEngine
from app.decision.momentum_decision_models import PipelineDecisionResult, PipelineVerdict
from app.momentum.momentum_engine import MomentumStateEngine
from app.momentum.momentum_models import MomentumState
from app.repositories.market_repository import SymbolRepository
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

        evidence: dict[str, object] = {
            "overall_score": fusion.overall_score,
            "confidence": fusion.confidence,
            "positive_factors": fusion.positive_factors,
            "negative_factors": fusion.negative_factors,
            "missing_data": fusion.missing_data,
        }
        technical_component = fusion.component_scores.get("technical")
        if technical_component is not None and technical_component.score is not None:
            evidence["technical_score"] = technical_component.score

        momentum_result = await self._momentum_engine.evaluate(
            symbol, fusion.overall_score, evidence, now=moment
        )

        momentum_state: MomentumState | None
        if momentum_result.transition is not None:
            momentum_state = momentum_result.transition.to_state
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
