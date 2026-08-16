"""Verified NSE trading-holiday data for calendar year 2026.

Two source documents were used, of genuinely different evidentiary
weight:

1. NSE circular NSE/FAOP/71777 (Circular Ref 212/2025), Futures &
   Options Department, dated 2025-12-12, signed Khushal Shah (Associate
   Vice President), contact msm@nse.co.in — a real NSE circular (has a
   circular reference number, department, signatory, official contact).
   Lists 15 trading holidays plus 4 holidays that fall on a weekend
   anyway. This is the authoritative source for every record below
   except the one noted in (2).

2. A user-supplied document ("NSE_Equities_Market_Holidays_2026.pdf")
   listing the equity-segment holidays. 15 of its 16 entries match (1)
   exactly. Its 16th entry — 2026-01-15, "Municipal Corporation
   Election - Maharashtra" — does NOT appear in (1) and the document
   itself is not circular-formatted (no circular reference number or
   signatory). The project owner confirmed this date independently
   (not derived from the document's own formatting, which this project
   does not treat as self-authenticating). It is recorded here with
   `verified_via_official_circular=False` to keep that distinction
   honest — see `NSEHoliday`'s docstring.

Equity and F&O holiday lists are NOT identical for 2026: Equity has
2026-01-15 as an extra closure that F&O does not.
"""

from datetime import date

from app.market_calendar.model import NSEHoliday
from app.market_calendar.segments import MarketSegment

_BOTH = frozenset({MarketSegment.EQUITY, MarketSegment.FNO})
_EQUITY_ONLY = frozenset({MarketSegment.EQUITY})

_FAO_CIRCULAR = (
    "NSE circular NSE/FAOP/71777 (Circular Ref 212/2025), Futures & Options Dept, "
    "dated 2025-12-12, signed Khushal Shah (Associate Vice President)"
)
_EQUITY_PDF_UNVERIFIED = (
    "User-supplied document 'NSE_Equities_Market_Holidays_2026.pdf' — not "
    "circular-formatted (no circular reference/signatory), absent from the F&O "
    "circular NSE/FAOP/71777; date confirmed independently by the project owner"
)


def _h(
    holiday_date: date,
    name: str,
    segments: frozenset[MarketSegment],
    source: str,
    verified_via_official_circular: bool,
    *,
    special_session: bool = False,
    special_session_note: str | None = None,
) -> NSEHoliday:
    return NSEHoliday(
        date=holiday_date,
        name=name,
        segments=segments,
        source=source,
        verified_via_official_circular=verified_via_official_circular,
        special_session=special_session,
        special_session_note=special_session_note,
    )

HOLIDAYS_2026 = (
    # --- The 15 weekday closures both source documents agree on ---
    _h(date(2026, 1, 26), "Republic Day", _BOTH, _FAO_CIRCULAR, True),
    _h(date(2026, 3, 3), "Holi", _BOTH, _FAO_CIRCULAR, True),
    _h(date(2026, 3, 26), "Shri Ram Navami", _BOTH, _FAO_CIRCULAR, True),
    _h(date(2026, 3, 31), "Shri Mahavir Jayanti", _BOTH, _FAO_CIRCULAR, True),
    _h(date(2026, 4, 3), "Good Friday", _BOTH, _FAO_CIRCULAR, True),
    _h(date(2026, 4, 14), "Dr. Baba Saheb Ambedkar Jayanti", _BOTH, _FAO_CIRCULAR, True),
    _h(date(2026, 5, 1), "Maharashtra Day", _BOTH, _FAO_CIRCULAR, True),
    _h(date(2026, 5, 28), "Bakri Id", _BOTH, _FAO_CIRCULAR, True),
    _h(date(2026, 6, 26), "Muharram", _BOTH, _FAO_CIRCULAR, True),
    _h(date(2026, 9, 14), "Ganesh Chaturthi", _BOTH, _FAO_CIRCULAR, True),
    _h(date(2026, 10, 2), "Mahatma Gandhi Jayanti", _BOTH, _FAO_CIRCULAR, True),
    _h(date(2026, 10, 20), "Dussehra", _BOTH, _FAO_CIRCULAR, True),
    _h(date(2026, 11, 10), "Diwali-Balipratipada", _BOTH, _FAO_CIRCULAR, True),
    _h(date(2026, 11, 24), "Prakash Gurpurb Sri Guru Nanak Dev", _BOTH, _FAO_CIRCULAR, True),
    _h(date(2026, 12, 25), "Christmas", _BOTH, _FAO_CIRCULAR, True),
    # --- Equity-only: absent from the F&O circular, weaker provenance ---
    _h(
        date(2026, 1, 15),
        "Municipal Corporation Election - Maharashtra",
        _EQUITY_ONLY,
        _EQUITY_PDF_UNVERIFIED,
        False,
    ),
    # --- Holidays that also fall on a weekend (informational — already
    #     non-trading via the weekend rule, listed here for source
    #     traceability and dashboard display only) ---
    _h(date(2026, 2, 15), "Mahashivratri", _BOTH, _FAO_CIRCULAR, True),
    _h(date(2026, 3, 21), "Id-Ul-Fitr (Ramadan Eid)", _BOTH, _FAO_CIRCULAR, True),
    _h(date(2026, 8, 15), "Independence Day", _BOTH, _FAO_CIRCULAR, True),
    _h(
        date(2026, 11, 8),
        "Diwali Laxmi Pujan",
        _BOTH,
        _FAO_CIRCULAR,
        True,
        special_session=True,
        special_session_note=(
            "Muhurat Trading conducted (symbolic short session); regular trading "
            "closed. Exact session timings not yet published by NSE as of the "
            "source circular's date (2025-12-12) — to be notified via a separate "
            "circular."
        ),
    ),
)
