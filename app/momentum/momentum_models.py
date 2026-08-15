"""Domain types for the Intraday Momentum State Machine.

Tracks a symbol's momentum-event *lifecycle* across repeated scans, as
opposed to `app.decision`'s `DecisionEvaluator`, which produces a fresh,
stateless ALERT/WATCH/REJECT verdict on every single scan with no memory
of what came before. This module is what turns "the same qualifying
condition seen every minute" into "one developing event" — see
`state_machine.py` for the transition rules and `momentum_engine.py` for
persistence + alert reuse.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class MomentumState(StrEnum):
    SETUP = "SETUP"
    WATCH = "WATCH"
    ACTIVATING = "ACTIVATING"
    TRIGGERED = "TRIGGERED"
    CONFIRMED = "CONFIRMED"
    EXHAUSTED = "EXHAUSTED"
    INVALIDATED = "INVALIDATED"


# EXHAUSTED/INVALIDATED are terminal — no outgoing transitions. See
# state_machine.py's module docstring for why each edge below (and no
# others) is allowed: strictly one-step-forward progression, plus an exit
# to INVALIDATED (setup broke down before ever triggering) or EXHAUSTED
# (it triggered, then faded) — never a backward step within the ladder,
# never skipping a stage.
ALLOWED_TRANSITIONS: frozenset[tuple[MomentumState, MomentumState]] = frozenset(
    {
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
    }
)

ALERT_WORTHY_STATES = frozenset({MomentumState.TRIGGERED, MomentumState.CONFIRMED})


def is_valid_transition(from_state: MomentumState, to_state: MomentumState) -> bool:
    return (from_state, to_state) in ALLOWED_TRANSITIONS


@dataclass
class StateTransition:
    """One momentum-state change. Persisted append-only (see
    `app.models.momentum_state_transition.MomentumStateTransition`) —
    never mutated once written, same "durable log" role
    `fundamental_fetch_log`/`AlertEvent` already play elsewhere in this
    project."""

    symbol: str
    from_state: MomentumState | None  # None only for the very first observation
    to_state: MomentumState
    timestamp: datetime
    reason: str
    score: float
    evidence: dict[str, object] = field(default_factory=dict)
