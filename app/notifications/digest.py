"""Twice-daily breakout digest: a short summary of stocks that newly
qualified (IPO or F&O) since the last digest, sent alongside -- not instead
of -- the existing real-time per-alert Telegram messages.

Kept separate from AlertMessageFormatter (which formats one alert in
detail) since this formats a batch summary. Pure functions here, no
DB/network -- app.scheduler.digest_jobs does the orchestration.
"""

from dataclasses import dataclass

_IPO_CATEGORIES = {"IPO_PRE_BREAKOUT", "IPO_BREAKOUT", "IPO_MOMENTUM"}
_FNO_CATEGORIES = {"FNO_PRE_BREAKOUT", "FNO_BREAKOUT", "FNO_MOMENTUM"}

_CATEGORY_LABELS = {
    "IPO_PRE_BREAKOUT": "IPO PRE-BREAKOUT",
    "IPO_BREAKOUT": "IPO BREAKOUT",
    "IPO_MOMENTUM": "IPO MOMENTUM",
    "FNO_PRE_BREAKOUT": "F&O PRE-BREAKOUT",
    "FNO_BREAKOUT": "F&O BREAKOUT",
    "FNO_MOMENTUM": "F&O MOMENTUM",
}


@dataclass
class DigestEntry:
    symbol: str
    alert_category: str | None
    score: float


def split_by_universe(entries: list[DigestEntry]) -> tuple[list[DigestEntry], list[DigestEntry]]:
    """(ipo_entries, fno_entries) -- anything with an unrecognized/missing
    category (e.g. breakout_v1, which predates the IPO/F&O split) is
    excluded from both: this digest is specifically about the IPO/F&O
    momentum-engine categories, not a general alert digest."""
    ipo = [e for e in entries if e.alert_category in _IPO_CATEGORIES]
    fno = [e for e in entries if e.alert_category in _FNO_CATEGORIES]
    return ipo, fno


def build_digest_text(entries: list[DigestEntry], *, universe_label: str) -> str | None:
    """None when there's nothing new -- the digest is skipped entirely
    rather than sending an empty "no new stocks" message twice a day."""
    if not entries:
        return None

    lines = [f"\U0001f4ca {universe_label} Breakout Digest — {len(entries)} new signal(s)", ""]
    for entry in sorted(entries, key=lambda e: e.score, reverse=True):
        label = _CATEGORY_LABELS.get(entry.alert_category or "", entry.alert_category or "UNKNOWN")
        lines.append(f"• {entry.symbol} — {label} (score {entry.score:.1f})")
    return "\n".join(lines)


def build_market_overview_text(
    *,
    regime: str | None,
    regime_score: float | None,
    sector_rotation_counts: dict[str, int],
    momentum_trigger_count: int,
    lookback_hours: float,
) -> str | None:
    """Summarizes the same stored analytics snapshots the dashboard reads
    (see `app.scheduler.analytics_snapshot_jobs`/`app.momentum`) — no
    computation happens here, only formatting of values the caller
    already read from the DB. `None` when there is nothing to say at
    all (no regime snapshot yet AND no sector data AND no momentum
    activity), same "skip rather than send an empty digest" rule as
    `build_digest_text`."""
    if regime is None and not sector_rotation_counts and momentum_trigger_count == 0:
        return None

    lines = [f"\U0001f30e Market Overview — last {lookback_hours:.0f}h", ""]

    if regime is not None:
        score_text = f" (score {regime_score:.1f})" if regime_score is not None else ""
        lines.append(f"Regime: {regime}{score_text}")
    else:
        lines.append("Regime: no snapshot yet")

    if sector_rotation_counts:
        parts = [f"{count} {state.lower()}" for state, count in sector_rotation_counts.items()]
        lines.append("Sectors: " + ", ".join(parts))

    lines.append(f"Momentum triggers: {momentum_trigger_count}")

    return "\n".join(lines)
