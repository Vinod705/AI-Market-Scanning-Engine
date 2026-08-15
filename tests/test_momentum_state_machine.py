"""Tests for app.momentum.state_machine — pure transition logic, no I/O.

Uses Settings(decision_min_alert_score=90) so band boundaries are round
numbers: band_low=30, band_mid=60, trigger=90.
"""

import pytest

from app.config.settings import Settings
from app.momentum.momentum_models import ALLOWED_TRANSITIONS, MomentumState, is_valid_transition
from app.momentum.state_machine import apply_transition, evaluate_transition

_SETTINGS = Settings(decision_min_alert_score=90.0)  # type: ignore[call-arg]


# --- initial observation (current=None) ---------------------------------


def test_initial_observation_enters_setup_band() -> None:
    result = evaluate_transition(None, 10.0, _SETTINGS)
    assert result is not None
    assert result[0] == MomentumState.SETUP


def test_initial_observation_enters_watch_band() -> None:
    result = evaluate_transition(None, 40.0, _SETTINGS)
    assert result is not None
    assert result[0] == MomentumState.WATCH


def test_initial_observation_enters_activating_band() -> None:
    result = evaluate_transition(None, 70.0, _SETTINGS)
    assert result is not None
    assert result[0] == MomentumState.ACTIVATING


def test_initial_observation_enters_triggered_band() -> None:
    """Even a very high first-ever score enters at TRIGGERED, not
    CONFIRMED — CONFIRMED requires persistence across a second
    evaluation, never granted on the first observation."""
    result = evaluate_transition(None, 95.0, _SETTINGS)
    assert result is not None
    assert result[0] == MomentumState.TRIGGERED


# --- one-step-forward progression, never skipping a stage ----------------


def test_setup_advances_to_watch_when_score_supports_it() -> None:
    result = evaluate_transition(MomentumState.SETUP, 40.0, _SETTINGS)
    assert result is not None
    assert result[0] == MomentumState.WATCH


def test_setup_does_not_skip_straight_to_triggered_even_with_a_high_score() -> None:
    """A sudden very high score on a SETUP symbol only advances one step
    (to WATCH), never jumps to TRIGGERED — development takes multiple
    confirming evaluations, not one lucky tick."""
    result = evaluate_transition(MomentumState.SETUP, 99.0, _SETTINGS)
    assert result is not None
    assert result[0] == MomentumState.WATCH


def test_watch_advances_to_activating() -> None:
    result = evaluate_transition(MomentumState.WATCH, 70.0, _SETTINGS)
    assert result is not None
    assert result[0] == MomentumState.ACTIVATING


def test_activating_advances_to_triggered() -> None:
    result = evaluate_transition(MomentumState.ACTIVATING, 95.0, _SETTINGS)
    assert result is not None
    assert result[0] == MomentumState.TRIGGERED


def test_triggered_advances_to_confirmed_when_still_qualifying() -> None:
    result = evaluate_transition(MomentumState.TRIGGERED, 95.0, _SETTINGS)
    assert result is not None
    assert result[0] == MomentumState.CONFIRMED


# --- holding (no change) --------------------------------------------------


def test_watch_holds_when_score_stays_in_its_own_band() -> None:
    """SETUP has no 'hold' zone of its own (band 0 *is* the invalidation
    zone — see the SETUP/WATCH/ACTIVATING invalidation tests below), but
    WATCH and ACTIVATING each have a real band where the score neither
    supports advancing nor triggers invalidation."""
    result = evaluate_transition(MomentumState.WATCH, 40.0, _SETTINGS)
    assert result is None


def test_confirmed_holds_when_still_qualifying() -> None:
    """Repeated evaluations with a still-qualifying score never re-alert
    — this is the literal 'no repeated alert for the same unchanged
    state' mechanism: no transition means the caller does nothing."""
    result = evaluate_transition(MomentumState.CONFIRMED, 95.0, _SETTINGS)
    assert result is None


def test_activating_holds_when_score_regresses_to_watch_band() -> None:
    """No backward step within the ladder — a dip from ACTIVATING-level
    back to WATCH-level score just holds at ACTIVATING, never demotes."""
    result = evaluate_transition(MomentumState.ACTIVATING, 40.0, _SETTINGS)
    assert result is None


# --- exit states: INVALIDATED (pre-trigger breakdown) ---------------------


def test_setup_invalidated_when_score_drops_below_setup_band() -> None:
    result = evaluate_transition(MomentumState.SETUP, 5.0, _SETTINGS)
    assert result is not None
    assert result[0] == MomentumState.INVALIDATED


def test_watch_invalidated_when_score_collapses() -> None:
    result = evaluate_transition(MomentumState.WATCH, 5.0, _SETTINGS)
    assert result is not None
    assert result[0] == MomentumState.INVALIDATED


def test_activating_invalidated_when_score_collapses() -> None:
    result = evaluate_transition(MomentumState.ACTIVATING, 5.0, _SETTINGS)
    assert result is not None
    assert result[0] == MomentumState.INVALIDATED


# --- exit states: EXHAUSTED (post-trigger fade) ---------------------------


def test_triggered_exhausted_when_score_falls_below_trigger() -> None:
    result = evaluate_transition(MomentumState.TRIGGERED, 50.0, _SETTINGS)
    assert result is not None
    assert result[0] == MomentumState.EXHAUSTED


def test_confirmed_exhausted_when_score_falls_below_trigger() -> None:
    result = evaluate_transition(MomentumState.CONFIRMED, 10.0, _SETTINGS)
    assert result is not None
    assert result[0] == MomentumState.EXHAUSTED


# --- terminal states never transition further -----------------------------


def test_exhausted_is_terminal() -> None:
    assert evaluate_transition(MomentumState.EXHAUSTED, 99.0, _SETTINGS) is None
    assert evaluate_transition(MomentumState.EXHAUSTED, 0.0, _SETTINGS) is None


def test_invalidated_is_terminal() -> None:
    assert evaluate_transition(MomentumState.INVALIDATED, 99.0, _SETTINGS) is None
    assert evaluate_transition(MomentumState.INVALIDATED, 0.0, _SETTINGS) is None


# --- explicit valid/invalid transition table ------------------------------


@pytest.mark.parametrize(
    ("from_state", "to_state"),
    [
        (MomentumState.SETUP, MomentumState.WATCH),
        (MomentumState.SETUP, MomentumState.INVALIDATED),
        (MomentumState.WATCH, MomentumState.ACTIVATING),
        (MomentumState.WATCH, MomentumState.INVALIDATED),
        (MomentumState.ACTIVATING, MomentumState.TRIGGERED),
        (MomentumState.ACTIVATING, MomentumState.INVALIDATED),
        (MomentumState.TRIGGERED, MomentumState.CONFIRMED),
        (MomentumState.TRIGGERED, MomentumState.EXHAUSTED),
        (MomentumState.TRIGGERED, MomentumState.INVALIDATED),
        (MomentumState.CONFIRMED, MomentumState.EXHAUSTED),
        (MomentumState.CONFIRMED, MomentumState.INVALIDATED),
    ],
)
def test_allowed_transition_is_valid(from_state: MomentumState, to_state: MomentumState) -> None:
    assert is_valid_transition(from_state, to_state) is True
    apply_transition(from_state, to_state)  # must not raise


@pytest.mark.parametrize(
    ("from_state", "to_state"),
    [
        (MomentumState.SETUP, MomentumState.ACTIVATING),  # skips WATCH
        (MomentumState.SETUP, MomentumState.TRIGGERED),  # skips two stages
        (MomentumState.SETUP, MomentumState.CONFIRMED),
        (MomentumState.SETUP, MomentumState.EXHAUSTED),  # never triggered, can't be exhausted
        (MomentumState.WATCH, MomentumState.TRIGGERED),  # skips ACTIVATING
        (MomentumState.WATCH, MomentumState.CONFIRMED),
        (MomentumState.WATCH, MomentumState.EXHAUSTED),
        (MomentumState.WATCH, MomentumState.SETUP),  # backward step
        (MomentumState.ACTIVATING, MomentumState.CONFIRMED),  # skips TRIGGERED
        (MomentumState.ACTIVATING, MomentumState.EXHAUSTED),
        (MomentumState.ACTIVATING, MomentumState.WATCH),  # backward step
        (MomentumState.TRIGGERED, MomentumState.WATCH),  # backward step
        (MomentumState.TRIGGERED, MomentumState.ACTIVATING),
        (MomentumState.CONFIRMED, MomentumState.TRIGGERED),  # backward step
        (MomentumState.CONFIRMED, MomentumState.SETUP),
        (MomentumState.EXHAUSTED, MomentumState.SETUP),  # terminal, no way out
        (MomentumState.EXHAUSTED, MomentumState.WATCH),
        (MomentumState.INVALIDATED, MomentumState.SETUP),  # terminal, no way out
        (MomentumState.INVALIDATED, MomentumState.TRIGGERED),
    ],
)
def test_disallowed_transition_is_invalid(from_state: MomentumState, to_state: MomentumState) -> None:
    assert is_valid_transition(from_state, to_state) is False
    with pytest.raises(ValueError, match="invalid transition"):
        apply_transition(from_state, to_state)


def test_first_observation_never_raises_for_any_starting_state() -> None:
    for state in MomentumState:
        apply_transition(None, state)  # must not raise


def test_allowed_transitions_table_has_no_self_loops() -> None:
    for from_state, to_state in ALLOWED_TRANSITIONS:
        assert from_state != to_state
