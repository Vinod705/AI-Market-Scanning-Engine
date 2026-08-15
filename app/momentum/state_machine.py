"""Pure state-transition logic — no I/O, no persistence, no alerting.

**Progression is exactly the diagram's one-way pipeline**: at most one
step forward per evaluation (SETUP -> WATCH -> ACTIVATING -> TRIGGERED ->
CONFIRMED), never skipping a stage even if the score already qualifies
for a later one — a sudden high score on a cold-start symbol enters at
whichever band it naturally falls into, but an *existing* SETUP/WATCH/
ACTIVATING symbol only ever advances one stage per call. This is the
literal mechanism behind "identify a *developing* momentum event": the
state machine itself enforces that development takes multiple
confirming evaluations, not one lucky tick. Never a backward step within
the ladder either — a score dip while ACTIVATING doesn't demote back to
WATCH; the position only ever exits via INVALIDATED (broke down before
triggering) or, once TRIGGERED/CONFIRMED, via EXHAUSTED (faded after
triggering).

**Band boundaries reuse existing, already-configured thresholds** rather
than inventing new ones: `settings.decision_min_alert_score` (already the
project's own "this candidate is alert-worthy" bar) is TRIGGERED's own
threshold. The two boundaries below it (SETUP/WATCH/ACTIVATING) split the
sub-threshold range into equal thirds — the same "no magnitude invented,
just the range's own natural thirds" reasoning already used for
`app.analytics.market.regime`'s SUPPORTIVE/NEUTRAL/RISK_OFF split. This
is a judgment call for where exactly SETUP ends and WATCH begins, same as
that module's — not a validated, backtested boundary.
"""

from app.config.settings import Settings
from app.momentum.momentum_models import MomentumState, is_valid_transition

_LADDER = [
    MomentumState.SETUP,
    MomentumState.WATCH,
    MomentumState.ACTIVATING,
    MomentumState.TRIGGERED,
]


def _band_index(score: float, trigger_threshold: float) -> int:
    """0=SETUP-band, 1=WATCH-band, 2=ACTIVATING-band, 3=TRIGGERED-eligible."""
    band_low = trigger_threshold / 3
    band_mid = 2 * trigger_threshold / 3
    if score >= trigger_threshold:
        return 3
    if score >= band_mid:
        return 2
    if score >= band_low:
        return 1
    return 0


def evaluate_transition(
    current: MomentumState | None, score: float, settings: Settings
) -> tuple[MomentumState, str] | None:
    """Returns `(next_state, reason)`, or `None` if nothing should change
    (including: terminal state, or score doesn't yet support advancing —
    the literal "no repeated alert for the same unchanged state"
    mechanism, since a caller only acts when this returns non-None)."""
    trigger_threshold = settings.decision_min_alert_score
    band_low = trigger_threshold / 3
    desired_band = _band_index(score, trigger_threshold)

    if current is None:
        entry_state = _LADDER[desired_band]
        return entry_state, f"initial observation: score={score:.2f} (band {desired_band})"

    if current in (MomentumState.EXHAUSTED, MomentumState.INVALIDATED):
        return None  # terminal — this momentum event's lifecycle is over

    if current in (MomentumState.TRIGGERED, MomentumState.CONFIRMED):
        if score >= trigger_threshold:
            if current == MomentumState.TRIGGERED:
                return (
                    MomentumState.CONFIRMED,
                    f"still above trigger threshold ({trigger_threshold:.2f}) on next evaluation: score={score:.2f}",
                )
            return None  # already CONFIRMED, still qualifying — no change
        return (
            MomentumState.EXHAUSTED,
            f"score fell below trigger threshold ({trigger_threshold:.2f}) after triggering: score={score:.2f}",
        )

    # current is SETUP, WATCH, or ACTIVATING — pre-trigger ladder.
    if score < band_low:
        return (
            MomentumState.INVALIDATED,
            f"score={score:.2f} fell below the setup threshold ({band_low:.2f}) before triggering",
        )

    current_index = _LADDER.index(current)
    next_index = current_index + 1
    if desired_band >= next_index:
        next_state = _LADDER[next_index]
        return next_state, f"score={score:.2f} supports advancing to {next_state.value}"

    return None  # holding at the current stage — score doesn't yet support advancing


def apply_transition(
    current: MomentumState | None, next_state: MomentumState
) -> None:
    """Raises `ValueError` if `(current, next_state)` isn't an allowed
    edge — the explicit "invalid transition" guard, used defensively by
    `momentum_engine.py` even though `evaluate_transition` above should
    never itself propose an illegal edge (belt-and-suspenders: a state
    machine's whole point is that illegal transitions are rejected, not
    merely "shouldn't happen")."""
    if current is None:
        return  # any starting state is a valid first observation
    if not is_valid_transition(current, next_state):
        raise ValueError(f"invalid transition: {current.value} -> {next_state.value}")
