"""Read-side service backing GET /decisions/{symbol}.

Deliberately stateless: rather than persisting every WATCH/REJECT decision
to its own table, this re-evaluates the Decision Engine live against the
symbol's most recent scanner result. That keeps the endpoint always
current (no staleness risk from a cached decision) without a
`decisions` table duplicating data `scanner_results` already has.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.decision.evaluator import DecisionEvaluator
from app.decision.models import DecisionCandidate, RuleResult, derive_signal_type
from app.repositories.market_repository import SymbolRepository
from app.repositories.scanner_repository import ScannerResultRepository
from app.schemas.alerts import DecisionOut, RuleResultOut


class DecisionService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._evaluator = DecisionEvaluator(settings)

    async def get_latest_decision(self, symbol: str) -> DecisionOut | None:
        symbol_row = await SymbolRepository(self._session).get_by_symbol(symbol)
        if symbol_row is None:
            return None

        latest_results = await ScannerResultRepository(self._session).get_for_symbol(
            symbol_row.id, limit=1
        )
        if not latest_results:
            return None
        scanner_result = latest_results[0]

        candidate = DecisionCandidate(
            symbol=symbol_row.symbol,
            scanner_name=scanner_result.scanner_name,
            signal_type=derive_signal_type(scanner_result.scanner_name),
            score=float(scanner_result.score),
            scan_date=scanner_result.date,
            feature_snapshot=scanner_result.feature_snapshot,
        )
        decision = self._evaluator.evaluate(candidate)

        return DecisionOut(
            symbol=decision.symbol,
            scanner_name=decision.scanner_name,
            signal_type=decision.signal_type,
            decision=decision.decision.value,
            score=decision.score,
            quality=decision.quality.value if decision.quality else None,
            passed_rules=decision.passed_rules,
            failed_rules=decision.failed_rules,
            warnings=decision.warnings,
            timestamp=decision.timestamp,
            rules=[_to_rule_schema(rule) for rule in decision.rule_results],
        )


def _to_rule_schema(rule: RuleResult) -> RuleResultOut:
    return RuleResultOut(
        rule_name=rule.rule_name,
        status=rule.status.value,
        actual_value=rule.actual_value,
        required_value=rule.required_value,
        reason=rule.reason,
    )
