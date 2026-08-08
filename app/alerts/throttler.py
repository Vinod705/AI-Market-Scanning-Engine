"""AlertThrottler: configurable per-symbol/signal cooldown.

Separate from deduplication on purpose: the deduplicator answers "is this
exact signal still active" (fingerprint-based, can span hours/days), while
the throttler answers a narrower question — "did we already notify for
this symbol+signal recently, regardless of fingerprint" — guarding against
a *new* fingerprint (e.g. a slightly different breakout level the same
day) still re-notifying too soon after the last one.
"""

from datetime import datetime, timedelta

from app.alerts.base import SuppressionResult
from app.config.settings import Settings
from app.repositories.alert_repository import AlertRepository


class AlertThrottler:
    def __init__(self, repo: AlertRepository, settings: Settings) -> None:
        self._repo = repo
        self._cooldown = timedelta(minutes=settings.alert_cooldown_minutes)

    async def check(self, symbol_id: int, signal_type: str, *, now: datetime) -> SuppressionResult:
        recent = await self._repo.get_most_recent_for_signal(
            symbol_id, signal_type, since=now - self._cooldown
        )
        if recent is not None:
            return SuppressionResult(
                suppressed=True,
                reason=(
                    f"cooldown active — alert #{recent.id} for this symbol/signal was created at "
                    f"{recent.created_at.isoformat()}, cooldown is {self._cooldown}"
                ),
                blocking_alert_id=recent.id,
            )
        return SuppressionResult(suppressed=False)
