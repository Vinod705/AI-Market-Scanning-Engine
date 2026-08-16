from enum import StrEnum


class MarketSegment(StrEnum):
    """NSE segments this project actually touches.

    `EQUITY` — Capital Market segment. Governs `daily_prices`,
    `daily_features`, and every LISTED/IPO-universe scanner
    (breakout_v1, vcp_v1, momentum_v1, orb_v1, pre_breakout_v1,
    ipo_intraday_v1) — all of these key off equity/cash-market symbols.

    `FNO` — Futures & Options segment. Governs the `fno_momentum_v1`
    scanner and any F&O-underlying feature/OI computation.

    These are NOT interchangeable: NSE's 2026 circulars show Equity and
    F&O holiday lists differ by one date (see
    `app.market_calendar.holidays`), so a holiday check must always be
    asked against the segment that's actually relevant to the caller —
    never a merged/combined list.
    """

    EQUITY = "EQUITY"
    FNO = "FNO"
