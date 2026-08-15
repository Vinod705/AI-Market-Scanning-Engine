"""Pure OI-buildup classification — no I/O, no persistence.

Standard, industry-wide convention pairing price direction with OI
direction (not invented for this project — this is the universal
definition used across F&O market commentary):

  price up   + OI up    -> LONG_BUILDUP    (new longs entering)
  price down + OI up    -> SHORT_BUILDUP   (new shorts entering)
  price up   + OI down  -> SHORT_COVERING  (shorts exiting)
  price down + OI down  -> LONG_UNWINDING  (longs exiting)
  flat price or flat OI -> NEUTRAL

No magnitude threshold is applied anywhere — classification is sign-based
only, so nothing about "how much of a move counts" is invented here. A
missing (`None`) price or OI change (no prior reading available yet) is
also NEUTRAL, never guessed into one of the four directional buckets.
"""

from decimal import Decimal

from app.derivatives.derivatives_models import BuildupClassification


def percent_change(current: Decimal, previous: Decimal | None) -> Decimal | None:
    """Shared by both `option_chain.py` and `futures_oi.py` — `None` when
    there's no prior reading to compare against, never a fabricated 0%."""
    if previous is None or previous == 0:
        return None
    return (current - previous) / previous * 100


def classify(
    price_change: Decimal | None, oi_change: Decimal | None
) -> BuildupClassification:
    if price_change is None or oi_change is None or price_change == 0 or oi_change == 0:
        return BuildupClassification.NEUTRAL
    if price_change > 0 and oi_change > 0:
        return BuildupClassification.LONG_BUILDUP
    if price_change < 0 and oi_change > 0:
        return BuildupClassification.SHORT_BUILDUP
    if price_change > 0 and oi_change < 0:
        return BuildupClassification.SHORT_COVERING
    return BuildupClassification.LONG_UNWINDING  # price_change < 0 and oi_change < 0
