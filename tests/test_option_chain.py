"""Tests for app.derivatives.option_chain.readings_from_chain."""

from datetime import date
from decimal import Decimal

from app.derivatives.derivatives_models import BuildupClassification, InstrumentType
from app.derivatives.option_chain import readings_from_chain
from app.providers.base_provider import OptionChainSnapshot, OptionLegSnapshot


def _leg(ltp: Decimal, close_price: Decimal, oi: Decimal, prev_oi: Decimal) -> OptionLegSnapshot:
    return OptionLegSnapshot(
        instrument_key="NSE_FO|1",
        ltp=ltp,
        close_price=close_price,
        volume=1000,
        oi=oi,
        prev_oi=prev_oi,
    )


def _row(call: OptionLegSnapshot | None, put: OptionLegSnapshot | None) -> OptionChainSnapshot:
    return OptionChainSnapshot(
        underlying_symbol="TCS",
        underlying_instrument_key="NSE_EQ|INE467B01029",
        expiry_date=date(2026, 8, 27),
        strike_price=Decimal("2400"),
        underlying_spot_price=Decimal("2380"),
        call=call,
        put=put,
    )


def test_produces_one_reading_per_leg_present() -> None:
    call = _leg(Decimal("50"), Decimal("45"), Decimal("1000"), Decimal("900"))
    put = _leg(Decimal("30"), Decimal("32"), Decimal("800"), Decimal("900"))
    readings = readings_from_chain([_row(call, put)])
    assert len(readings) == 2
    assert {r.instrument_type for r in readings} == {InstrumentType.CALL, InstrumentType.PUT}


def test_skips_missing_leg() -> None:
    call = _leg(Decimal("50"), Decimal("45"), Decimal("1000"), Decimal("900"))
    readings = readings_from_chain([_row(call, None)])
    assert len(readings) == 1
    assert readings[0].instrument_type == InstrumentType.CALL


def test_call_leg_price_and_oi_change_computed_from_bundled_previous_values() -> None:
    call = _leg(ltp=Decimal("50"), close_price=Decimal("40"), oi=Decimal("1100"), prev_oi=Decimal("1000"))
    reading = readings_from_chain([_row(call, None)])[0]
    assert reading.price == Decimal("50")
    assert reading.prev_price == Decimal("40")
    assert reading.oi_change == Decimal("100")
    assert reading.classification == BuildupClassification.LONG_BUILDUP


def test_strike_price_and_expiry_carried_from_row() -> None:
    call = _leg(Decimal("50"), Decimal("45"), Decimal("1000"), Decimal("900"))
    reading = readings_from_chain([_row(call, None)])[0]
    assert reading.strike_price == Decimal("2400")
    assert reading.expiry_date == date(2026, 8, 27)
    assert reading.underlying_symbol == "TCS"


def test_no_rows_produces_no_readings() -> None:
    assert readings_from_chain([]) == []
