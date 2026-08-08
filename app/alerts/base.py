"""Shared types for the alerts package."""

from dataclasses import dataclass


@dataclass
class SuppressionResult:
    """Returned by the deduplicator and throttler — both answer the same
    question ("should this candidate be suppressed, and why?") over
    different criteria, so they share one result shape."""

    suppressed: bool
    reason: str | None = None
    # The existing alert responsible for the suppression, if any — lets the
    # caller log a SUPPRESSED event against it for a complete audit trail.
    blocking_alert_id: int | None = None
