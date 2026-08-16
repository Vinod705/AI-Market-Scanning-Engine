"""MomentumStateEngine: orchestrates the state machine against real
persistence, and reuses the existing alert infrastructure verbatim when a
transition lands on an alert-worthy state.

**Reuse, not reimplementation**: when the state machine moves a symbol
into TRIGGERED or CONFIRMED, this constructs an `app.decision.models.DecisionResult`
and calls the *existing* `AlertManager.process()` — the same class every
scanner's decision already goes through. That single call already
carries `AlertDeduplicator` (fingerprint-based "is this already active")
and `AlertThrottler` (per-symbol/signal cooldown) internally, and pushes
onto the same `AlertQueue` a caller passes in — nothing here reimplements
dedup, cooldown, or queueing.

**"No repeated alerts for the same unchanged state"** has two layers:
1. `state_machine.evaluate_transition()` returns `None` when the score
   doesn't justify a transition — this engine then does nothing at all
   (no DB write, no `AlertManager` call) when that happens.
2. Even on a genuine transition into TRIGGERED/CONFIRMED,
   `AlertManager.process()`'s own fingerprint dedup (keyed on symbol +
   scanner + signal_type + level + date) provides a second, independent
   safety net against a duplicate alert within the same trading day.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.manager import AlertManager
from app.config.settings import Settings
from app.core.time import to_market_time, utc_now
from app.decision.models import Decision, DecisionResult, Quality
from app.momentum import state_machine
from app.momentum.momentum_models import (
    ALERT_WORTHY_STATES,
    MOMENTUM_SCANNER_NAME,
    StateTransition,
)
from app.repositories.market_repository import SymbolRepository
from app.repositories.momentum_state_repository import MomentumStateRepository


@dataclass
class MomentumEvaluationResult:
    transition: StateTransition | None
    alert_id: int | None
    transition_id: int | None = None


class MomentumStateEngine:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        alert_manager: AlertManager,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._alert_manager = alert_manager

    async def evaluate(
        self,
        symbol: str,
        score: float,
        evidence: dict[str, object],
        *,
        now: datetime | None = None,
    ) -> MomentumEvaluationResult:
        moment = now or utc_now()

        async with self._session_factory() as session:
            symbol_row = await SymbolRepository(session).get_by_symbol(symbol)
            if symbol_row is None:
                return MomentumEvaluationResult(transition=None, alert_id=None)

            repo = MomentumStateRepository(session)
            current_record = await repo.get_current(symbol_row.id)
            current_state = MomentumStateRepository.state_of(current_record)

            outcome = state_machine.evaluate_transition(current_state, score, self._settings)
            if outcome is None:
                return MomentumEvaluationResult(transition=None, alert_id=None)
            next_state, reason = outcome

            state_machine.apply_transition(current_state, next_state)  # raises if illegal

            transition = StateTransition(
                symbol=symbol,
                from_state=current_state,
                to_state=next_state,
                timestamp=moment,
                reason=reason,
                score=score,
                evidence=evidence,
            )
            _record, transition_id = await repo.apply_transition(symbol_row.id, transition)
            await session.commit()
            symbol_id = symbol_row.id

        alert_id: int | None = None
        if next_state in ALERT_WORTHY_STATES:
            alert_id = await self._raise_alert(symbol, symbol_id, transition)

        return MomentumEvaluationResult(
            transition=transition, alert_id=alert_id, transition_id=transition_id
        )

    async def _raise_alert(
        self, symbol: str, symbol_id: int, transition: StateTransition
    ) -> int | None:
        settings = self._settings
        quality = (
            Quality.HIGH
            if transition.score >= settings.alert_high_priority_score
            else Quality.MEDIUM
        )
        decision = DecisionResult(
            symbol=symbol,
            scanner_name=MOMENTUM_SCANNER_NAME,
            signal_type=transition.to_state.value,
            decision=Decision.ALERT,
            score=transition.score,
            quality=quality,
            passed_rules=[transition.reason],
            feature_snapshot={
                "momentum_state": transition.to_state.value,
                "from_state": transition.from_state.value if transition.from_state else None,
                "reason": transition.reason,
                "evidence": transition.evidence,
                # This is `AlertDeduplicator`'s primary `signal_date`
                # source (see app.alerts.deduplicator) — an Indian market
                # business date, so it must be IST, not a bare `.date()`
                # on the UTC-stored transition instant.
                "date": to_market_time(transition.timestamp, settings.market_timezone)
                .date()
                .isoformat(),
            },
            timestamp=transition.timestamp,
        )
        return await self._alert_manager.process(
            decision, symbol_id=symbol_id, now=transition.timestamp
        )
