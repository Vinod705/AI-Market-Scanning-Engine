"""Tests for app.decision.momentum_decision_engine.MomentumDecisionEngine —
pure verdict labeling, no I/O."""

from app.config.settings import Settings
from app.decision.momentum_decision_engine import MomentumDecisionEngine
from app.decision.momentum_decision_models import PipelineVerdict
from app.momentum.momentum_models import MomentumState

_ENGINE = MomentumDecisionEngine(Settings(pipeline_min_confidence_pct=40.0))  # type: ignore[call-arg]


def test_rejects_when_confidence_below_minimum() -> None:
    verdict, reason = _ENGINE.decide(confidence=20.0, momentum_state=MomentumState.TRIGGERED)
    assert verdict == PipelineVerdict.REJECT
    assert "confidence" in reason


def test_rejects_when_no_momentum_state_even_with_good_confidence() -> None:
    verdict, _reason = _ENGINE.decide(confidence=90.0, momentum_state=None)
    assert verdict == PipelineVerdict.REJECT


def test_triggers_on_triggered_state() -> None:
    verdict, reason = _ENGINE.decide(confidence=90.0, momentum_state=MomentumState.TRIGGERED)
    assert verdict == PipelineVerdict.TRIGGER
    assert "TRIGGERED" in reason


def test_triggers_on_confirmed_state() -> None:
    verdict, _reason = _ENGINE.decide(confidence=90.0, momentum_state=MomentumState.CONFIRMED)
    assert verdict == PipelineVerdict.TRIGGER


def test_watch_on_setup_state() -> None:
    verdict, _reason = _ENGINE.decide(confidence=90.0, momentum_state=MomentumState.SETUP)
    assert verdict == PipelineVerdict.WATCH


def test_watch_on_watch_state() -> None:
    verdict, _reason = _ENGINE.decide(confidence=90.0, momentum_state=MomentumState.WATCH)
    assert verdict == PipelineVerdict.WATCH


def test_watch_on_activating_state() -> None:
    verdict, _reason = _ENGINE.decide(confidence=90.0, momentum_state=MomentumState.ACTIVATING)
    assert verdict == PipelineVerdict.WATCH


def test_invalidate_on_invalidated_state() -> None:
    verdict, reason = _ENGINE.decide(confidence=90.0, momentum_state=MomentumState.INVALIDATED)
    assert verdict == PipelineVerdict.INVALIDATE
    assert "INVALIDATED" in reason


def test_invalidate_on_exhausted_state() -> None:
    """EXHAUSTED (triggered then faded) maps to the same INVALIDATE
    verdict as INVALIDATED (never triggered) — from a "should anything
    further happen" standpoint, both answers are the same."""
    verdict, reason = _ENGINE.decide(confidence=90.0, momentum_state=MomentumState.EXHAUSTED)
    assert verdict == PipelineVerdict.INVALIDATE
    assert "EXHAUSTED" in reason


def test_confidence_gate_checked_before_momentum_state() -> None:
    """Low confidence rejects even a TRIGGERED state — data quality is
    checked first, same ordering DecisionEvaluator already uses."""
    verdict, reason = _ENGINE.decide(confidence=10.0, momentum_state=MomentumState.TRIGGERED)
    assert verdict == PipelineVerdict.REJECT
    assert "confidence" in reason
