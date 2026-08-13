"""Read-side service backing the Phase 6 explainability API
(`GET /candidates`, `GET /candidates/{symbol}/explain`).

Deliberately stateless, same pattern as `DecisionService`: re-reads the
latest `scanner_results` row for the IPO/F&O candidate scanners and
re-evaluates the Decision Engine live rather than persisting a second
copy of the verdict. No AI calls anywhere in this file — every field
returned is a direct read or arithmetic derivation of data the scanner/
decision engine already computed.
"""

from datetime import date as date_

from sqlalchemy.ext.asyncio import AsyncSession

from app.candidates.explainer import (
    CategoryBreakdown,
    explain_decision,
    fundamental_breakdown,
    technical_breakdown,
)
from app.compounding.engine import evaluate_compounding
from app.compounding.models import CompoundingResult
from app.config.settings import Settings
from app.core.time import utc_now
from app.decision.evaluator import DecisionEvaluator
from app.decision.models import DecisionCandidate, RuleResult, derive_signal_type
from app.decision.validator import DecisionValidator
from app.models.scanner_result import ScannerResult
from app.repositories.market_repository import SymbolRepository
from app.repositories.scanner_repository import ScannerResultRepository
from app.schemas.candidates import (
    CandidateExplainOut,
    CandidateSummaryOut,
    CategoryBreakdownOut,
    CompoundingOut,
    DataFreshnessOut,
    DecisionExplanationOut,
    DecisionRuleOut,
    FactorOut,
    FieldSourceOut,
    OverallScoreBreakdownOut,
)

CANDIDATE_SCANNER_NAMES = {"fno_momentum_v1", "pre_breakout_v1", "ipo_intraday_v1"}


def _str_list(value: object) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def _scanner_sources(snapshot: dict[str, object]) -> list[str]:
    """Pre-Phase-7 rows have no `scanner_sources` key at all — they were
    all 5paisa-discovered by construction, so default accordingly rather
    than showing an empty discovery block for historical candidates."""
    sources = _str_list(snapshot.get("scanner_sources"))
    return sources or ["5PAISA"]


def _factor_list(value: object) -> list[dict[str, object]]:
    return [f for f in value if isinstance(f, dict)] if isinstance(value, list) else []


def _to_field_source_out(snapshot: dict[str, object]) -> FieldSourceOut:
    raw_alternates = snapshot.get("alternates")
    alternates: list[tuple[str, float]] = []
    if isinstance(raw_alternates, list):
        for item in raw_alternates:
            if isinstance(item, list | tuple) and len(item) == 2:
                source, value = item
                as_float = DecisionValidator.as_float({"v": value}, "v")
                if isinstance(source, str) and as_float is not None:
                    alternates.append((source, as_float))
    return FieldSourceOut(
        field_name=str(snapshot.get("field_name", "")),
        value=DecisionValidator.as_float(snapshot, "value"),
        source=snapshot.get("source") if isinstance(snapshot.get("source"), str) else None,
        period=snapshot.get("period") if isinstance(snapshot.get("period"), str) else None,
        status=str(snapshot.get("status", "UNKNOWN")),
        alternates=alternates,
    )


def _to_factor_out(factor: dict[str, object]) -> FactorOut:
    return FactorOut(
        factor_name=str(factor.get("factor_name", "")),
        category=str(factor.get("category", "")),
        value=factor.get("value") if isinstance(factor.get("value"), str) else None,
        normalized_score=DecisionValidator.as_float(factor, "normalized_score"),
        weight=DecisionValidator.as_float(factor, "weight") or 0.0,
        contribution=DecisionValidator.as_float(factor, "contribution") or 0.0,
        status=str(factor.get("status", "UNKNOWN")),
        reason=str(factor.get("reason", "")),
    )


class CandidateService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._evaluator = DecisionEvaluator(settings)

    async def list_candidates(
        self,
        *,
        universe: str | None = None,
        alert_category: str | None = None,
        status: str = "qualified",
        limit: int = 100,
    ) -> list[CandidateSummaryOut]:
        results = await ScannerResultRepository(self._session).list_results(
            status=status, limit=2000
        )
        candidate_results = [r for r in results if r.scanner_name in CANDIDATE_SCANNER_NAMES]
        if universe is not None:
            candidate_results = [
                r for r in candidate_results if r.feature_snapshot.get("universe") == universe
            ]
        if alert_category is not None:
            candidate_results = [
                r
                for r in candidate_results
                if r.feature_snapshot.get("alert_category") == alert_category
            ]
        candidate_results = candidate_results[:limit]

        symbol_ids = {r.symbol_id for r in candidate_results}
        symbols = {
            s.id: s.symbol
            for s in await SymbolRepository(self._session).list_by_ids(list(symbol_ids))
        }

        return [
            _to_summary(r, symbols.get(r.symbol_id, str(r.symbol_id)), self._settings)
            for r in candidate_results
        ]

    async def get_explain(self, symbol: str) -> CandidateExplainOut | None:
        symbol_row = await SymbolRepository(self._session).get_by_symbol(symbol)
        if symbol_row is None:
            return None

        results = await ScannerResultRepository(self._session).get_for_symbol(
            symbol_row.id, limit=50
        )
        candidate_results = [r for r in results if r.scanner_name in CANDIDATE_SCANNER_NAMES]
        if not candidate_results:
            return None
        result = candidate_results[0]  # already ordered by date desc
        snapshot = result.feature_snapshot

        decision_candidate = DecisionCandidate(
            symbol=symbol_row.symbol,
            scanner_name=result.scanner_name,
            signal_type=derive_signal_type(result.scanner_name),
            score=float(result.score),
            scan_date=result.date,
            feature_snapshot=snapshot,
        )
        decision_result = self._evaluator.evaluate(decision_candidate)

        technical_factors = _factor_list(snapshot.get("technical_factors"))
        fundamental_factors = _factor_list(snapshot.get("fundamental_factors"))
        fundamental_field_sources = [
            _to_field_source_out(s) for s in _factor_list(snapshot.get("fundamental_field_sources"))
        ]
        all_factors = technical_factors + fundamental_factors
        positive_factors = [_to_factor_out(f) for f in all_factors if f.get("status") == "POSITIVE"]
        negative_factors = [_to_factor_out(f) for f in all_factors if f.get("status") == "NEGATIVE"]

        fundamental_score = DecisionValidator.as_float(snapshot, "fundamental_score")
        technical_score = DecisionValidator.as_float(snapshot, "technical_score") or 0.0
        overall_score = float(result.score)

        overall_score_breakdown = _build_score_breakdown(
            fundamental_score=fundamental_score,
            technical_score=technical_score,
            overall_score=overall_score,
            settings=self._settings,
        )
        scanner_sources = _scanner_sources(snapshot)

        scanner_passed = _str_list(snapshot.get("passed_rules"))
        scanner_failed = _str_list(snapshot.get("failed_rules"))
        combined_passed = list(dict.fromkeys(scanner_passed + decision_result.passed_rules))
        combined_failed = list(dict.fromkeys(scanner_failed + decision_result.failed_rules))

        raw_setup_state = snapshot.get("setup_state")
        explanation = explain_decision(
            fundamental_score=fundamental_score,
            technical_reasons=_str_list(snapshot.get("technical_reasons")),
            fundamental_reasons=_str_list(snapshot.get("fundamental_reasons")),
            setup_state=raw_setup_state if isinstance(raw_setup_state, str) else None,
            passed_rules=combined_passed,
            failed_rules=combined_failed,
            risk_flags=_str_list(snapshot.get("risk_flags")),
        )

        compounding = evaluate_compounding(snapshot, self._settings)

        days_old = (date_.today() - result.date).days
        data_freshness = DataFreshnessOut(
            scan_date=result.date,
            days_old=days_old,
            is_fresh=days_old <= self._settings.decision_max_data_age_days,
            max_age_days=self._settings.decision_max_data_age_days,
        )

        return CandidateExplainOut(
            symbol=symbol_row.symbol,
            universe=str(snapshot.get("universe", "")),
            scanner_name=result.scanner_name,
            alert_category=(
                snapshot.get("alert_category")
                if isinstance(snapshot.get("alert_category"), str)
                else None
            ),
            setup_state=(
                snapshot.get("setup_state")
                if isinstance(snapshot.get("setup_state"), str)
                else None
            ),
            instrument_type=str(snapshot.get("instrument_type", "EQUITY")),
            price=DecisionValidator.as_float(snapshot, "price"),
            breakout_level=DecisionValidator.as_float(snapshot, "breakout_level"),
            support_level=DecisionValidator.as_float(snapshot, "support_level"),
            resistance_level=DecisionValidator.as_float(snapshot, "resistance_level"),
            fundamental_score=fundamental_score,
            technical_score=technical_score,
            overall_score=overall_score,
            quality=str(snapshot.get("quality", "LOW")),
            overall_score_breakdown=overall_score_breakdown,
            scanner_sources=scanner_sources,
            scanner_confirmation_count=len(scanner_sources),
            fundamental_reasons=_str_list(snapshot.get("fundamental_reasons")),
            technical_reasons=_str_list(snapshot.get("technical_reasons")),
            positive_factors=positive_factors,
            negative_factors=negative_factors,
            risk_flags=_str_list(snapshot.get("risk_flags")),
            passed_rules=combined_passed,
            failed_rules=combined_failed,
            fundamental_data_completeness_pct=DecisionValidator.as_float(
                snapshot, "data_completeness_pct"
            )
            or 0.0,
            technical_data_completeness_pct=DecisionValidator.as_float(
                snapshot, "technical_data_completeness_pct"
            )
            or 0.0,
            data_freshness=data_freshness,
            technical_breakdown=[
                _to_breakdown_out(b) for b in technical_breakdown(technical_factors, self._settings)
            ],
            fundamental_breakdown=(
                [
                    _to_breakdown_out(b)
                    for b in fundamental_breakdown(fundamental_factors, self._settings)
                ]
                if fundamental_score is not None
                else []
            ),
            fundamental_unavailable_reason=(
                None
                if fundamental_score is not None
                else "Fundamental data unavailable — no data source is currently integrated."
            ),
            fundamental_field_sources=fundamental_field_sources,
            decision=decision_result.decision.value,
            decision_quality=decision_result.quality.value if decision_result.quality else None,
            decision_rules=[_to_rule_out(r) for r in decision_result.rule_results],
            explanation=DecisionExplanationOut(
                why_this_stock=explanation.why_this_stock,
                why_now=explanation.why_now,
                what_confirms=explanation.what_confirms,
                what_has_not_been_confirmed=explanation.what_has_not_been_confirmed,
                risks=explanation.risks,
            ),
            compounding=_to_compounding_out(compounding),
            scan_date=result.date,
            timestamp=utc_now(),
        )


def _to_summary(result: ScannerResult, symbol: str, settings: Settings) -> CandidateSummaryOut:
    snapshot = result.feature_snapshot
    scanner_sources = _scanner_sources(snapshot)
    # Reuses the same compounding engine as get_explain() — no new
    # calculation, just calling it once more for the list view (pure,
    # in-memory arithmetic over data already in `snapshot`).
    compounding = evaluate_compounding(snapshot, settings)
    return CandidateSummaryOut(
        symbol=symbol,
        universe=str(snapshot.get("universe", "")),
        scanner_name=result.scanner_name,
        alert_category=(
            snapshot.get("alert_category")
            if isinstance(snapshot.get("alert_category"), str)
            else None
        ),
        setup_state=(
            snapshot.get("setup_state") if isinstance(snapshot.get("setup_state"), str) else None
        ),
        status=result.status,
        price=DecisionValidator.as_float(snapshot, "price"),
        overall_score=float(result.score),
        technical_score=DecisionValidator.as_float(snapshot, "technical_score") or 0.0,
        fundamental_score=DecisionValidator.as_float(snapshot, "fundamental_score"),
        quality=str(snapshot.get("quality", "LOW")),
        compounding_decision=compounding.decision.value,
        compounding_score=compounding.compounding_score,
        scan_date=result.date,
        scanner_sources=scanner_sources,
        scanner_confirmation_count=len(scanner_sources),
    )


def _build_score_breakdown(
    *,
    fundamental_score: float | None,
    technical_score: float,
    overall_score: float,
    settings: Settings,
) -> OverallScoreBreakdownOut:
    if fundamental_score is None:
        return OverallScoreBreakdownOut(
            technical_weight=1.0,
            fundamental_weight=0.0,
            technical_contribution=round(technical_score, 2),
            fundamental_contribution=None,
            overall_score=overall_score,
            note=(
                "Fundamental Score is UNKNOWN (no data source integrated) — the "
                "Overall Setup Score is based entirely on the Technical Score, "
                "per the documented scoring policy: missing data is never "
                "treated as zero, and the configured fundamental/technical "
                "weighting only applies when both scores are known."
            ),
        )
    technical_weight = settings.overall_technical_weight
    fundamental_weight = settings.overall_fundamental_weight
    return OverallScoreBreakdownOut(
        technical_weight=technical_weight,
        fundamental_weight=fundamental_weight,
        technical_contribution=round(technical_score * technical_weight, 2),
        fundamental_contribution=round(fundamental_score * fundamental_weight, 2),
        overall_score=overall_score,
        note=None,
    )


def _to_breakdown_out(breakdown: CategoryBreakdown) -> CategoryBreakdownOut:
    return CategoryBreakdownOut(
        category=breakdown.category,
        label=breakdown.label,
        score=breakdown.score,
        max_score=breakdown.max_score,
        factors=[_to_factor_out(f) for f in breakdown.factors],
    )


def _to_compounding_out(result: CompoundingResult) -> CompoundingOut:
    return CompoundingOut(
        timeframe=result.timeframe.value,
        setup_type=result.setup_type,
        current_price=result.current_price,
        target_price=result.target_price,
        potential_upside_pct=result.potential_upside_pct,
        stop_loss=result.stop_loss,
        risk_pct=result.risk_pct,
        reward_risk_ratio=result.reward_risk_ratio,
        target_classification=result.target_classification.value,
        higher_timeframe_confirmation=result.higher_timeframe_confirmation.value,
        fundamental_quality=result.fundamental_quality,
        compounding_score=result.compounding_score,
        decision=result.decision.value,
        reasons=list(result.reasons),
        data_limitations=list(result.data_limitations),
    )


def _to_rule_out(rule: RuleResult) -> DecisionRuleOut:
    return DecisionRuleOut(
        rule_name=rule.rule_name,
        status=rule.status.value,
        actual_value=rule.actual_value,
        required_value=rule.required_value,
        reason=rule.reason,
    )
