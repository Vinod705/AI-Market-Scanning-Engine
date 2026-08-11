"""Tests for scripts.backfill_ipo_listing_dates's pure parsing/matching
logic -- no DB/network I/O, since parse_csv_rows/match_rows are
deliberately factored out as pure functions for exactly this reason."""

from datetime import date

from scripts.backfill_ipo_listing_dates import BackfillRow, match_rows, parse_csv_rows


def test_parse_csv_rows_parses_a_valid_row() -> None:
    csv_text = (
        "symbol,isin,listing_date,company_name,classification\n"
        "MANIPALHOS,INE0AB01019,2026-08-05,Manipal Hospitals Ltd,MAINBOARD_IPO\n"
    )

    rows = parse_csv_rows(csv_text)

    assert len(rows) == 1
    assert rows[0].symbol == "MANIPALHOS"
    assert rows[0].isin == "INE0AB01019"
    assert rows[0].listing_date == date(2026, 8, 5)
    assert rows[0].classification == "MAINBOARD_IPO"


def test_parse_csv_rows_leaves_unparseable_date_as_none() -> None:
    csv_text = "symbol,isin,listing_date\nBADCO,INE0BAD0101,not-a-date\n"

    rows = parse_csv_rows(csv_text)

    assert rows[0].listing_date is None


def test_parse_csv_rows_skips_rows_with_no_symbol() -> None:
    csv_text = "symbol,isin,listing_date\n,INE0X,2026-01-01\n"

    rows = parse_csv_rows(csv_text)

    assert rows == []


def _row(
    *,
    symbol: str,
    isin: str | None = None,
    listing_date: date | None = date(2026, 1, 1),
    classification: str | None = None,
) -> BackfillRow:
    return BackfillRow(
        symbol=symbol, isin=isin, listing_date=listing_date, classification=classification
    )


def test_match_rows_matches_by_isin_when_unambiguous() -> None:
    rows = [_row(symbol="MANIPALHOS", isin="INE0AB01019")]

    to_write, report = match_rows(
        rows,
        symbol_to_isin={"MANIPALHOS": "INE0AB01019"},
        known_symbols={"MANIPALHOS"},
    )

    assert to_write == {"MANIPALHOS": date(2026, 1, 1)}
    assert report.matched_by_isin == 1
    assert report.populated == 1
    assert report.unmatched == []


def test_match_rows_leaves_ambiguous_isin_unmatched() -> None:
    """Two local symbols reporting the same ISIN is an anomaly -- never
    guess which one the curated row actually refers to."""
    rows = [_row(symbol="AMBIGCO", isin="INE0SAME001")]

    to_write, report = match_rows(
        rows,
        symbol_to_isin={"AMBIGCO": "INE0SAME001", "OTHERCO": "INE0SAME001"},
        known_symbols={"AMBIGCO", "OTHERCO"},
    )

    assert to_write == {}
    assert report.ambiguous == ["AMBIGCO"]
    assert report.populated == 0


def test_match_rows_falls_back_to_symbol_when_isin_absent_on_row() -> None:
    rows = [_row(symbol="NOISINCO", isin=None)]

    to_write, report = match_rows(
        rows,
        symbol_to_isin={},
        known_symbols={"NOISINCO"},
    )

    assert to_write == {"NOISINCO": date(2026, 1, 1)}
    assert report.matched_by_symbol == 1
    assert report.missing_isin == ["NOISINCO"]


def test_match_rows_flags_isin_mismatch_as_ambiguous_not_a_guess() -> None:
    """Symbol string matches locally, but our own live ISIN for that
    symbol disagrees with the curated row's ISIN -- a rename/reuse
    collision. Must not silently accept the symbol-string match."""
    rows = [_row(symbol="RENAMEDCO", isin="INE0NEW0001")]

    to_write, report = match_rows(
        rows,
        symbol_to_isin={"RENAMEDCO": "INE0OLD0001"},
        known_symbols={"RENAMEDCO"},
    )

    assert to_write == {}
    assert report.ambiguous == ["RENAMEDCO"]


def test_match_rows_reports_unknown_symbol_as_unmatched() -> None:
    rows = [_row(symbol="NOWHERE", isin=None)]

    to_write, report = match_rows(rows, symbol_to_isin={}, known_symbols=set())

    assert to_write == {}
    assert report.unmatched == ["NOWHERE"]


def test_match_rows_flags_duplicate_symbol_rows() -> None:
    rows = [
        _row(symbol="DUPCO", isin="INE0DUP0001"),
        _row(symbol="DUPCO", isin="INE0DUP0001"),
    ]

    to_write, report = match_rows(
        rows, symbol_to_isin={"DUPCO": "INE0DUP0001"}, known_symbols={"DUPCO"}
    )

    assert report.processed == 2
    assert report.duplicate == ["DUPCO"]
    assert report.populated == 1  # only the first occurrence is written


def test_match_rows_reports_invalid_date_without_writing() -> None:
    rows = [_row(symbol="BADDATECO", isin="INE0BAD0001", listing_date=None)]

    to_write, report = match_rows(
        rows, symbol_to_isin={"BADDATECO": "INE0BAD0001"}, known_symbols={"BADDATECO"}
    )

    assert to_write == {}
    assert report.invalid_date == ["BADDATECO"]


def test_match_rows_excludes_non_ipo_classification() -> None:
    rows = [_row(symbol="SOMEETF", isin="INE0ETF0001", classification="ETF")]

    to_write, report = match_rows(
        rows, symbol_to_isin={"SOMEETF": "INE0ETF0001"}, known_symbols={"SOMEETF"}
    )

    assert to_write == {}
    assert report.excluded == ["SOMEETF"]


def test_match_rows_includes_mainboard_and_sme_classifications() -> None:
    rows = [
        _row(symbol="MAINCO", isin="INE0MAIN001", classification="MAINBOARD_IPO"),
        _row(symbol="SMECO", isin="INE0SME0001", classification="SME_IPO"),
    ]

    to_write, report = match_rows(
        rows,
        symbol_to_isin={"MAINCO": "INE0MAIN001", "SMECO": "INE0SME0001"},
        known_symbols={"MAINCO", "SMECO"},
    )

    assert set(to_write) == {"MAINCO", "SMECO"}
    assert report.excluded == []


def test_match_rows_is_deterministic_across_repeated_calls() -> None:
    """No hidden state -- running the same input twice must produce
    identical output, which is what makes the backfill safe to re-run."""
    rows = [_row(symbol="MANIPALHOS", isin="INE0AB01019")]
    kwargs = {"symbol_to_isin": {"MANIPALHOS": "INE0AB01019"}, "known_symbols": {"MANIPALHOS"}}

    first_write, first_report = match_rows(rows, **kwargs)  # type: ignore[arg-type]
    second_write, second_report = match_rows(rows, **kwargs)  # type: ignore[arg-type]

    assert first_write == second_write
    assert first_report.summary() == second_report.summary()
