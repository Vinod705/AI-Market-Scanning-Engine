"""DecisionEngine: the scheduler's entry point for the Phase 5 decision +
alert layer.

Reads *qualified* scanner_results, runs each through `DecisionEvaluator`,
and hands ALERT-grade verdicts to `AlertManager` for persistence, dedup,
cooldown, and queueing. Never talks to a broker or a notification provider
directly — same role `ScannerEngine`/`FeatureEngine` play upstream of it.
"""

from dataclasses import dataclass, field
from datetime import datetime

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.manager import AlertManager
from app.config.settings import Settings
from app.decision.evaluator import DecisionEvaluator
from app.decision.models import Decision, DecisionCandidate, derive_signal_type
from app.repositories.market_repository import SymbolRepository
from app.repositories.scanner_repository import ScannerResultRepository


@dataclass
class DecisionEngineResult:
    candidates_evaluated: int = 0
    alerts_created: int = 0
    watch_count: int = 0
    reject_count: int = 0
    errors: list[str] = field(default_factory=list)


class DecisionEngine:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        alert_manager: AlertManager,
    ) -> None:
        self._session_factory = session_factory
        self._evaluator = DecisionEvaluator(settings)
        self._alert_manager = alert_manager

    async def run_all(self, *, now: datetime | None = None) -> DecisionEngineResult:
        result = DecisionEngineResult()
        moment = now or datetime.now()

        async with self._session_factory() as session:
            qualified = await ScannerResultRepository(session).list_results(
                status="qualified", limit=5000
            )
            symbol_ids = {r.symbol_id for r in qualified}
            symbols = {
                s.id: s.symbol
                for s in await SymbolRepository(session).list_by_ids(list(symbol_ids))
            }

        for scanner_result in qualified:
            symbol_name = symbols.get(scanner_result.symbol_id)
            if symbol_name is None:
                continue
            result.candidates_evaluated += 1
            try:
                candidate = DecisionCandidate(
                    symbol=symbol_name,
                    scanner_name=scanner_result.scanner_name,
                    signal_type=derive_signal_type(scanner_result.scanner_name),
                    score=float(scanner_result.score),
                    scan_date=scanner_result.date,
                    feature_snapshot=scanner_result.feature_snapshot,
                )
                decision_result = self._evaluator.evaluate(candidate, now=moment)

                logger.debug(
                    "Decision for {symbol}: {decision} (score={score}, passed={passed}, failed={failed})",
                    symbol=decision_result.symbol,
                    decision=decision_result.decision,
                    score=decision_result.score,
                    passed=decision_result.passed_rules,
                    failed=decision_result.failed_rules,
                )

                if decision_result.decision == Decision.ALERT:
                    alert_id = await self._alert_manager.process(
                        decision_result, symbol_id=scanner_result.symbol_id, now=moment
                    )
                    if alert_id is not None:
                        result.alerts_created += 1
                elif decision_result.decision == Decision.WATCH:
                    result.watch_count += 1
                else:
                    result.reject_count += 1
            except Exception as exc:  # noqa: BLE001 - isolate per-candidate failures
                logger.exception(
                    "Decision evaluation failed for symbol_id={id}", id=scanner_result.symbol_id
                )
                result.errors.append(f"{symbol_name}: {exc}")

        if result.candidates_evaluated:
            logger.info(
                "Decision engine run: {evaluated} evaluated, {alerts} alerts, {watch} watch, {reject} reject",
                evaluated=result.candidates_evaluated,
                alerts=result.alerts_created,
                watch=result.watch_count,
                reject=result.reject_count,
            )
        return result
