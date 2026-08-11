"""Compounding/Opportunity Engine.

Evaluates a candidate's realistic potential upside (5%-20%+, not capped at
5%) against its risk/reward and confirmation strength, so the system
prefers healthy setups over the largest raw percentage — see the module-
level `IMPORTANT LOGIC` example in the originating spec: an 18% target
with poor risk/reward and no confirmation must not automatically outrank
a 9% target with strong risk/reward and confirmed trend.

Pure and stateless: `evaluate_compounding()` takes the same persisted
`feature_snapshot` dict every other read-time consumer already uses
(`app.services.candidate_service.CandidateService.get_explain`,
`app.alerts.formatter`) and re-derives everything fresh, exactly like
`app.decision.evaluator.DecisionEvaluator` already does — no new
persisted fields, no migration, no new scanner, no new data source.

Every input is something the Phase 3 feature engine / Fundamental Queue
already computes:
  - target/upside: `app.compounding.target` (support/resistance/swing_high)
  - stop/risk: `app.compounding.risk` (support/ATR14/swing_low)
  - higher-timeframe confirmation: `trend_direction` + `adx14` — the
    existing EMA20/50/200 trend stack and ADX(14), which are inherently
    multi-week signals already. This is NOT literal weekly/monthly bar
    resampling (this system has no such pipeline, and building one was
    judged out of scope for "smallest necessary change" — see the final
    report) — it's an honest confirmation signal built only from data
    already computed, clearly labeled as such rather than pretending to
    be a true higher-timeframe scan.
  - fundamental quality: the existing Fundamental Score (Trendlyne, via
    `FundamentalQueueService`) — reused as-is, `FundamentalScorer`'s
    weights are untouched.
"""

from app.candidates.builder import quality_from_score
from app.compounding.models import (
    CompoundingDecision,
    CompoundingResult,
    HigherTimeframeConfirmation,
    TargetClassification,
    TargetResult,
    Timeframe,
)
from app.compounding.risk import derive_risk
from app.compounding.target import derive_target
from app.config.settings import Settings
from app.decision.validator import DecisionValidator

_UPSIDE_SCORE: dict[TargetClassification, float] = {
    TargetClassification.UNKNOWN: 0.0,
    TargetClassification.NOT_ATTRACTIVE: 10.0,
    TargetClassification.MINIMUM_OPPORTUNITY: 45.0,
    TargetClassification.GOOD: 65.0,
    TargetClassification.STRONG: 80.0,
    TargetClassification.VERY_STRONG: 90.0,
    # Plateaus at the same score as VERY_STRONG rather than climbing
    # further — implements "do NOT assume >20% is automatically better"
    # as a genuine ceiling, not just a comment: beyond ~15-20%, risk/
    # reward and confirmation must do the differentiating work.
    TargetClassification.EXCEPTIONAL: 90.0,
}

_HTF_SCORE: dict[HigherTimeframeConfirmation, float] = {
    HigherTimeframeConfirmation.CONFIRMED: 100.0,
    HigherTimeframeConfirmation.NOT_CONFIRMED: 40.0,
    HigherTimeframeConfirmation.UNKNOWN: 50.0,
}

_FUNDAMENTAL_UNKNOWN_SCORE = 50.0  # neutral — mirrors combine_scores' UNKNOWN handling
_RR_UNKNOWN_SCORE = 30.0


def _rr_score(reward_risk_ratio: float | None) -> float:
    if reward_risk_ratio is None:
        return _RR_UNKNOWN_SCORE
    if reward_risk_ratio < 1.0:
        return 20.0
    if reward_risk_ratio < 1.5:
        return 50.0
    if reward_risk_ratio < 2.5:
        return 75.0
    return 100.0


def _higher_timeframe_confirmation(
    trend_direction: object, adx14: float | None, settings: Settings
) -> HigherTimeframeConfirmation:
    """Reuses the existing `trend_direction` (EMA20>EMA50>EMA200 stack) and
    `adx14` (trend strength) features and the existing
    `fno_momentum_min_adx` threshold — no new indicator, no invented
    threshold. See module docstring for why this is a same-timeframe
    confirmation proxy, not literal weekly/monthly resampling."""
    if trend_direction is None:
        return HigherTimeframeConfirmation.UNKNOWN
    if trend_direction != "up":
        return HigherTimeframeConfirmation.NOT_CONFIRMED
    if adx14 is None:
        return HigherTimeframeConfirmation.UNKNOWN
    return (
        HigherTimeframeConfirmation.CONFIRMED
        if adx14 >= settings.fno_momentum_min_adx
        else HigherTimeframeConfirmation.NOT_CONFIRMED
    )


def _classify_timeframe(
    target_classification: TargetClassification,
    htf_confirmation: HigherTimeframeConfirmation,
) -> Timeframe:
    """The implied holding horizon for the move this setup targets —
    derived from target magnitude + higher-timeframe confirmation, per
    the spec's explicit "do NOT hard-code weekly/monthly = 5%". Larger
    targets are only labeled MONTHLY when the (proxy) higher-timeframe
    signal actually confirms; otherwise they're labeled WEEKLY rather
    than asserting a longer horizon than the data supports."""
    if target_classification in (TargetClassification.UNKNOWN, TargetClassification.NOT_ATTRACTIVE):
        return Timeframe.UNKNOWN
    if target_classification == TargetClassification.MINIMUM_OPPORTUNITY:
        return Timeframe.DAILY
    if target_classification in (TargetClassification.GOOD, TargetClassification.STRONG):
        return Timeframe.WEEKLY
    return (
        Timeframe.MONTHLY
        if htf_confirmation == HigherTimeframeConfirmation.CONFIRMED
        else Timeframe.WEEKLY
    )


def _compute_score(
    *,
    target: TargetResult,
    reward_risk_ratio: float | None,
    htf_confirmation: HigherTimeframeConfirmation,
    fundamental_score: float | None,
    settings: Settings,
) -> tuple[float, list[str]]:
    upside_component = _UPSIDE_SCORE[target.target_classification]
    rr_component = _rr_score(reward_risk_ratio)
    htf_component = _HTF_SCORE[htf_confirmation]
    fundamental_component = (
        fundamental_score if fundamental_score is not None else _FUNDAMENTAL_UNKNOWN_SCORE
    )

    score = (
        upside_component * settings.compounding_weight_upside
        + rr_component * settings.compounding_weight_risk_reward
        + htf_component * settings.compounding_weight_higher_timeframe
        + fundamental_component * settings.compounding_weight_fundamental
    )
    score = round(min(max(score, 0.0), 100.0), 2)

    reasons = [
        (
            f"Potential upside {target.potential_upside_pct:.1f}% ({target.target_classification.value})"
            if target.potential_upside_pct is not None
            else "Potential upside UNKNOWN"
        ),
        (
            f"Risk/Reward {reward_risk_ratio:.2f}"
            if reward_risk_ratio is not None
            else "Risk/Reward UNKNOWN"
        ),
        f"Higher-timeframe confirmation: {htf_confirmation.value}",
        (
            f"Fundamental quality: {quality_from_score(fundamental_score).value}"
            if fundamental_score is not None
            else "Fundamental quality: UNKNOWN"
        ),
    ]
    return score, reasons


def _decide(
    target_classification: TargetClassification, score: float, settings: Settings
) -> CompoundingDecision:
    if target_classification == TargetClassification.UNKNOWN:
        return CompoundingDecision.WATCH
    if target_classification == TargetClassification.NOT_ATTRACTIVE:
        return CompoundingDecision.NOT_ATTRACTIVE
    if score >= settings.compounding_strong_score_threshold:
        return CompoundingDecision.STRONG_CANDIDATE
    if score >= settings.compounding_trade_score_threshold:
        return CompoundingDecision.TRADE_CANDIDATE
    return CompoundingDecision.WATCH


def evaluate_compounding(snapshot: dict[str, object], settings: Settings) -> CompoundingResult:
    price = DecisionValidator.as_float(snapshot, "price")
    resistance_level = DecisionValidator.as_float(snapshot, "resistance_level")
    support_level = DecisionValidator.as_float(snapshot, "support_level")
    swing_high = DecisionValidator.as_float(snapshot, "swing_high")
    swing_low = DecisionValidator.as_float(snapshot, "swing_low")
    atr14 = DecisionValidator.as_float(snapshot, "atr14")
    adx14 = DecisionValidator.as_float(snapshot, "adx14")
    trend_direction = snapshot.get("trend_direction")
    fundamental_score = DecisionValidator.as_float(snapshot, "fundamental_score")
    raw_setup_state = snapshot.get("setup_state")

    target = derive_target(
        price=price,
        resistance_level=resistance_level,
        support_level=support_level,
        swing_high=swing_high,
    )
    risk = derive_risk(
        price=price,
        target_price=target.target_price,
        support_level=support_level,
        swing_low=swing_low,
        atr14=atr14,
        atr_multiplier=settings.compounding_atr_stop_multiplier,
    )
    htf_confirmation = _higher_timeframe_confirmation(trend_direction, adx14, settings)
    timeframe = _classify_timeframe(target.target_classification, htf_confirmation)
    fundamental_quality = (
        quality_from_score(fundamental_score).value if fundamental_score is not None else "UNKNOWN"
    )

    score, reasons = _compute_score(
        target=target,
        reward_risk_ratio=risk.reward_risk_ratio,
        htf_confirmation=htf_confirmation,
        fundamental_score=fundamental_score,
        settings=settings,
    )
    decision = _decide(target.target_classification, score, settings)

    data_limitations: list[str] = []
    if target.target_price is None:
        data_limitations.append(f"target: {target.basis}")
    if risk.stop_loss is None:
        data_limitations.append(f"stop-loss: {risk.basis}")
    if fundamental_score is None:
        data_limitations.append("fundamental score unavailable (UNKNOWN)")
    if htf_confirmation == HigherTimeframeConfirmation.UNKNOWN:
        data_limitations.append("trend direction/ADX unavailable for higher-timeframe confirmation")

    return CompoundingResult(
        timeframe=timeframe,
        setup_type=raw_setup_state if isinstance(raw_setup_state, str) else None,
        current_price=price,
        target_price=target.target_price,
        potential_upside_pct=target.potential_upside_pct,
        stop_loss=risk.stop_loss,
        risk_pct=risk.risk_pct,
        reward_risk_ratio=risk.reward_risk_ratio,
        target_classification=target.target_classification,
        higher_timeframe_confirmation=htf_confirmation,
        fundamental_quality=fundamental_quality,
        compounding_score=score,
        decision=decision,
        reasons=reasons,
        data_limitations=data_limitations,
    )
