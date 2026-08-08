"""AlertDeduplicator: deterministic fingerprinting + "is this already active" check.

The scanner runs every minute, so the same stock's same setup would
otherwise generate a decision every single tick. The fingerprint collapses
all of those into one identity; `check()` is what actually prevents a
second WhatsApp send for a signal that's still active.
"""

import hashlib

from app.alerts.base import SuppressionResult
from app.decision.models import DecisionResult
from app.repositories.alert_repository import AlertRepository

_INACTIVE_STATUSES = {"EXPIRED"}


class AlertDeduplicator:
    @staticmethod
    def build_fingerprint(decision: DecisionResult) -> str:
        """Deterministic key over (symbol, scanner, signal_type, breakout
        level, signal date). The date is the "signal time window": as long
        as the scanner keeps reporting the same setup on the same trading
        day, it hashes to the same fingerprint."""
        breakout_level = decision.feature_snapshot.get(
            "breakout_level"
        ) or decision.feature_snapshot.get("resistance_level")
        signal_date = decision.feature_snapshot.get("date") or decision.timestamp.date().isoformat()
        raw = "|".join(
            [
                decision.symbol,
                decision.scanner_name,
                decision.signal_type,
                str(breakout_level),
                str(signal_date),
            ]
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    def __init__(self, repo: AlertRepository) -> None:
        self._repo = repo

    async def check(self, fingerprint: str) -> SuppressionResult:
        existing = await self._repo.get_by_fingerprint(fingerprint)
        if existing is not None and existing.status not in _INACTIVE_STATUSES:
            return SuppressionResult(
                suppressed=True,
                reason=f"duplicate signal — already tracked as alert #{existing.id} (status={existing.status})",
                blocking_alert_id=existing.id,
            )
        return SuppressionResult(suppressed=False)
