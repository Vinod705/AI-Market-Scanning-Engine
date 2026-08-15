"""Breadth and volume-participation math for a sector — pure functions,
no I/O, no LLM.

**Not currently invoked with real data anywhere in this codebase.** Both
metrics need a sector's real constituent-stock membership list (e.g. "these
40 symbols make up NIFTY IT") to be meaningful, and no such mapping exists
in this project: `Symbol.sector`/`Symbol.industry` are 0% populated across
all 9,598 listed symbols, and Upstox's instrument-master records carry no
sector/industry field to parse either (confirmed live). `sector_strength.py`
therefore reports `breadth`/`volume_participation` as `None`, not a
fabricated value computed by guessing membership.

The functions below exist so that once a real membership source is
available, breadth/volume participation can be wired in without inventing
the math at that point too — they're ready, just not yet reachable from
real constituent data.
"""

from decimal import Decimal


def compute_breadth(advancing_count: int, total_count: int) -> Decimal | None:
    """Percentage of a sector's constituents that advanced (closed up) on
    the day — the standard market-breadth definition. `None` if
    `total_count` is 0 (no constituents to measure)."""
    if total_count <= 0:
        return None
    if advancing_count < 0 or advancing_count > total_count:
        raise ValueError("advancing_count must be between 0 and total_count")
    return Decimal(advancing_count) / Decimal(total_count) * 100


def compute_volume_participation(
    above_average_volume_count: int, total_count: int
) -> Decimal | None:
    """Percentage of a sector's constituents trading above their own
    average volume — how broad-based the sector's volume interest is, not
    just whether the index's own volume figure is high. `None` if
    `total_count` is 0."""
    if total_count <= 0:
        return None
    if above_average_volume_count < 0 or above_average_volume_count > total_count:
        raise ValueError("above_average_volume_count must be between 0 and total_count")
    return Decimal(above_average_volume_count) / Decimal(total_count) * 100
