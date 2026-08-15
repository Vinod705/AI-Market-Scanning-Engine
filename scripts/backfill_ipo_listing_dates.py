"""Idempotent backfill: populates Symbol.listing_date from a curated,
externally-sourced IPO listing-date CSV.

NEVER infers/fabricates a listing date from created_at, the first
daily_prices bar, or bar count -- see app/universe/provider.py's
docstring for why none of those can reliably express a multi-year IPO
window (5paisa and Trendlyne were both checked live this session and
neither reports a listing date; our own daily_prices history is only
~400 days deep). A symbol this script can't confidently match is left
untouched (listing_date stays NULL) and reported as unmatched, never
guessed.

Expected CSV format (header row required):

    symbol,isin,listing_date,company_name,exchange,classification,source
    MANIPALHOS,INE0XYZ01019,2026-08-05,Manipal Hospitals Ltd,NSE,MAINBOARD_IPO,"NSE archive"

    symbol        (required)  - NSE trading symbol, matched against Symbol.symbol
    isin          (preferred) - primary match key, cross-referenced against
                                 our locally-recorded Upstox ISIN (from
                                 Symbol.instrument_token)
    listing_date  (required)  - ISO YYYY-MM-DD, the real exchange listing date
    company_name, exchange, source (optional, audit-only, not used for matching)
    classification (optional) - if present, only "MAINBOARD_IPO"/"SME_IPO"
                                 rows are used; anything else (ETF, fund,
                                 relisting, ...) is excluded and reported

Matching: ISIN-primary, symbol-string fallback when ISIN is absent on
either side. A row whose symbol resolves locally but whose ISIN
contradicts our own recorded ISIN for that symbol (a rename/reuse
collision) is left unmatched and reported as ambiguous, never guessed.
The ISIN used for cross-referencing comes from `Symbol.instrument_token`
(Upstox's own `"NSE_EQ|<ISIN>"` instrument key, already stored locally
for every Upstox-sourced symbol — see
`app.fundamentals.upstox_fundamental_provider`'s `_extract_isin`) rather
than a live provider call: no network fetch needed, and no dependency on
5paisa being configured at all.

Safe to re-run: each run re-evaluates the full CSV against current data
and only ever writes listing_date for confidently-matched rows.

Usage:
    python -m scripts.backfill_ipo_listing_dates path/to/curated_ipos.csv
"""

import asyncio
import csv
import sys
from dataclasses import dataclass, field
from datetime import date, datetime

from loguru import logger

from app.database.session import AsyncSessionLocal
from app.models.symbol import Symbol
from app.repositories.market_repository import SymbolRepository

_VALID_CLASSIFICATIONS = {"MAINBOARD_IPO", "SME_IPO"}
_UPSTOX_ISIN_PREFIX = "NSE_EQ|"


def _extract_isin(instrument_token: str) -> str | None:
    """Same extraction `UpstoxFundamentalDataProvider._extract_isin` uses
    — Upstox's own instrument key already carries the ISIN, so this
    script's ISIN cross-reference needs no live provider call at all."""
    if not instrument_token.startswith(_UPSTOX_ISIN_PREFIX):
        return None
    isin = instrument_token[len(_UPSTOX_ISIN_PREFIX) :]
    return isin or None


@dataclass
class BackfillRow:
    symbol: str
    isin: str | None
    listing_date: date | None
    classification: str | None = None


@dataclass
class BackfillReport:
    processed: int = 0
    matched_by_isin: int = 0
    matched_by_symbol: int = 0
    populated: int = 0
    unmatched: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    duplicate: list[str] = field(default_factory=list)
    invalid_date: list[str] = field(default_factory=list)
    missing_isin: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"processed={self.processed} "
            f"matched_by_isin={self.matched_by_isin} "
            f"matched_by_symbol={self.matched_by_symbol} "
            f"populated={self.populated} "
            f"unmatched={len(self.unmatched)} "
            f"ambiguous={len(self.ambiguous)} "
            f"duplicate={len(self.duplicate)} "
            f"invalid_date={len(self.invalid_date)} "
            f"missing_isin={len(self.missing_isin)} "
            f"excluded={len(self.excluded)}"
        )


def parse_csv_rows(csv_text: str) -> list[BackfillRow]:
    """Pure CSV parsing -- no I/O, no matching. A row with an unparseable
    date is still returned (with listing_date=None) so match_rows can
    report it rather than silently dropping it."""
    reader = csv.DictReader(csv_text.splitlines())
    rows: list[BackfillRow] = []
    for raw in reader:
        symbol = (raw.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        isin = (raw.get("isin") or "").strip().upper() or None
        raw_date = (raw.get("listing_date") or "").strip()
        try:
            listing_date: date | None = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            listing_date = None
        classification = (raw.get("classification") or "").strip().upper() or None
        rows.append(
            BackfillRow(
                symbol=symbol, isin=isin, listing_date=listing_date, classification=classification
            )
        )
    return rows


def match_rows(
    rows: list[BackfillRow],
    *,
    symbol_to_isin: dict[str, str],
    known_symbols: set[str],
) -> tuple[dict[str, date], BackfillReport]:
    """Pure matching logic -- no DB/network I/O, fully unit-testable.

    `symbol_to_isin`: our own symbols mapped to their locally-recorded
    Upstox ISIN (only symbols with a resolvable ISIN are present).
    `known_symbols`: every symbol we track locally (superset of the above,
    used for the symbol-string fallback).

    Returns {our_symbol: listing_date} to write, plus the full report.
    Never returns a match for a row it isn't confident about.
    """
    report = BackfillReport()
    isin_to_symbols: dict[str, list[str]] = {}
    for sym, isin in symbol_to_isin.items():
        isin_to_symbols.setdefault(isin, []).append(sym)

    to_write: dict[str, date] = {}
    seen_symbols: set[str] = set()

    for row in rows:
        report.processed += 1

        if row.symbol in seen_symbols:
            report.duplicate.append(row.symbol)
            continue
        seen_symbols.add(row.symbol)

        if row.listing_date is None:
            report.invalid_date.append(row.symbol)
            continue

        if row.classification is not None and row.classification not in _VALID_CLASSIFICATIONS:
            report.excluded.append(row.symbol)
            continue

        matched_symbol: str | None = None

        if row.isin:
            candidates = isin_to_symbols.get(row.isin, [])
            if len(candidates) == 1:
                matched_symbol = candidates[0]
                report.matched_by_isin += 1
            elif len(candidates) > 1:
                report.ambiguous.append(row.symbol)
                continue
            # zero candidates -> fall through to the symbol-string fallback
        else:
            report.missing_isin.append(row.symbol)

        if matched_symbol is None and row.symbol in known_symbols:
            local_isin = symbol_to_isin.get(row.symbol)
            if row.isin and local_isin and local_isin != row.isin:
                # Symbol string matches, but the ISINs disagree -- most
                # likely a renamed/reused ticker referring to a different
                # company than the CSV row intends. Do not guess.
                report.ambiguous.append(row.symbol)
                continue
            matched_symbol = row.symbol
            report.matched_by_symbol += 1

        if matched_symbol is None:
            report.unmatched.append(row.symbol)
            continue

        to_write[matched_symbol] = row.listing_date
        report.populated += 1

    return to_write, report


async def main(csv_path: str) -> None:
    with open(csv_path, encoding="utf-8") as f:
        rows = parse_csv_rows(f.read())

    async with AsyncSessionLocal() as session:
        repo = SymbolRepository(session)
        active_symbols: list[Symbol] = await repo.list_active()
        known_symbols = {s.symbol for s in active_symbols}
        symbol_to_isin = {
            s.symbol: isin
            for s in active_symbols
            if (isin := _extract_isin(s.instrument_token)) is not None
        }

        to_write, report = match_rows(
            rows, symbol_to_isin=symbol_to_isin, known_symbols=known_symbols
        )

        for symbol_name, listing_date in to_write.items():
            symbol_row = await repo.get_by_symbol(symbol_name)
            if symbol_row is not None:
                symbol_row.listing_date = listing_date
        await session.commit()

    logger.info("IPO listing-date backfill complete: {summary}", summary=report.summary())
    if report.unmatched:
        logger.warning("Unmatched symbols: {symbols}", symbols=", ".join(report.unmatched))
    if report.ambiguous:
        logger.warning("Ambiguous matches: {symbols}", symbols=", ".join(report.ambiguous))
    if report.duplicate:
        logger.warning("Duplicate rows: {symbols}", symbols=", ".join(report.duplicate))
    if report.invalid_date:
        logger.warning(
            "Invalid listing_date rows: {symbols}", symbols=", ".join(report.invalid_date)
        )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m scripts.backfill_ipo_listing_dates <path-to-curated-ipo-csv>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
