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
