"""Tests for app.scanner.vcp_scanner.VcpScanner."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.models.daily_feature import DailyFeature
from app.models.symbol import Symbol
from app.providers.base_provider import Candle, ProviderSymbol
from app.repositories.feature_repository import DailyFeatureRepository
from app.repositories.market_repository import PriceRepository, SymbolRepository
from app.repositories.scanner_repository import ScannerResultRepository
from app.scanner.models import ScanContext
from app.scanner.scanner_manager import ScannerManager
from app.scanner.scanner_registry import ScannerRegistry
from app.scanner.vcp_scanner import VcpScanner

_QUALIFYING_FEATURES: dict[str, object] = {
    "pattern_vcp": True,
    "trend_strength": Decimal("80"),
    "momentum_score": Decimal("50"),
    "relative_volume": Decimal("2.0"),
    "atr_expansion": False,
    "atr_contraction": True,
    "rs_vs_nifty": Decimal("10"),
    "resistance_level": Decimal("112"),
}


def _context(price: Decimal | None = Decimal("110"), **overrides: object) -> ScanContext:
    symbol = Symbol(id=1, symbol="TCS", exchange="N", instrument_token="1")
    values = {**_QUALIFYING_FEATURES, **overrides}
    features = DailyFeature(symbol_id=1, **values)
    return ScanContext(symbol=symbol, features=features, price=price)


@pytest.fixture
def scanner() -> VcpScanner:
    return VcpScanner(Settings())


def test_validate_passes_with_price_present(scanner: VcpScanner) -> None:
    result = scanner.validate(_context())
    assert result.valid is True


def test_validate_rejects_missing_price(scanner: VcpScanner) -> None:
    result = scanner.validate(_context(price=None))
    assert result.valid is False
    assert result.reason is not None
    assert "price" in result.reason


def test_scan_qualifies_when_pattern_vcp_is_true(scanner: VcpScanner) -> None:
    outcome = scanner.scan(_context())
    assert outcome.qualified is True
    assert "contraction" in outcome.reason


def test_scan_rejects_when_pattern_vcp_is_false(scanner: VcpScanner) -> None:
    outcome = scanner.scan(_context(pattern_vcp=False))
    assert outcome.qualified is False
    assert "no volatility contraction" in outcome.reason


def test_score_matches_expected_weighted_composite(scanner: VcpScanner) -> None:
    # Same formula/fields as BreakoutScanner's score() — see module
    # docstring — so this locks in that the reuse is byte-for-byte, not
    # an approximation.
    score = scanner.score(_context())
    assert score == pytest.approx(70.07, abs=0.01)


def test_score_is_always_within_bounds(scanner: VcpScanner) -> None:
    score = scanner.score(
        _context(
            trend_strength=None,
            momentum_score=None,
            relative_volume=Decimal("0"),
            atr_expansion=False,
            atr_contraction=False,
            rs_vs_nifty=None,
            resistance_level=None,
        )
    )
    assert 0.0 <= score <= 100.0


def test_scan_qualification_independent_of_score() -> None:
    """A low-scoring context can still qualify (pattern_vcp alone gates
    qualification) — score and scan are independent, matching BaseScanner's
    documented contract ("even a rejected symbol gets a score")."""
    scanner = VcpScanner(Settings())
    context = _context(
        pattern_vcp=True,
        trend_strength=Decimal("0"),
        momentum_score=Decimal("-100"),
        relative_volume=Decimal("0"),
        rs_vs_nifty=Decimal("-100"),
    )
    outcome = scanner.scan(context)
    score = scanner.score(context)
    assert outcome.qualified is True
    assert score < 50.0


def test_vcp_registers_in_scanner_registry_by_name() -> None:
    registry = ScannerRegistry()
    registry.register(VcpScanner(Settings()))
    found = registry.get("vcp_v1")
    assert found is not None
    assert found.name == "vcp_v1"
    assert registry.get_all() == [found]


async def test_run_scanner_persists_qualified_vcp_result(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """End-to-end through the real ScannerManager/DB path — proves VCP
    results land in the same scanner_results mechanism every other
    scanner uses, not a special-cased new one."""
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="VCPSYM", exchange="N", instrument_token="vcp1")
        )
        await session.commit()
        symbol_id = symbol_row.id

        await PriceRepository(session).upsert_daily_many(
            symbol_id,
            [Candle(timestamp=datetime(2026, 1, 5), open=108, high=111, low=107, close=110, volume=100_000)],
        )
        await DailyFeatureRepository(session).upsert(symbol_id, date(2026, 1, 5), _QUALIFYING_FEATURES)
        await session.commit()

    async with session_factory() as session:
        symbols = await SymbolRepository(session).list_active()

    manager = ScannerManager(session_factory)
    scanner = VcpScanner(Settings())
    stats = await manager.run_scanner(scanner, symbols, run_id=1)

    assert stats.symbols_scanned == 1
    assert stats.qualified_count == 1
    assert stats.rejected_count == 0
    assert stats.error_count == 0

    async with session_factory() as session:
        results = await ScannerResultRepository(session).get_for_symbol(symbol_id)
        assert len(results) == 1
        assert results[0].scanner_name == "vcp_v1"
        assert results[0].status == "qualified"


async def test_run_scanner_handles_symbol_with_no_features_yet(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Insufficient/missing data: a symbol with zero daily_features rows
    goes through the existing "no context" rejection path, not an error —
    matches every other scanner's documented behavior."""
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="NOFEATURES", exchange="N", instrument_token="vcp2")
        )
        await session.commit()

    async with session_factory() as session:
        symbols = await SymbolRepository(session).list_active()

    manager = ScannerManager(session_factory)
    scanner = VcpScanner(Settings())
    stats = await manager.run_scanner(scanner, symbols, run_id=1)

    assert stats.rejected_count == 1
    assert stats.qualified_count == 0
    assert stats.error_count == 0

    async with session_factory() as session:
        results = await ScannerResultRepository(session).get_for_symbol(symbol_row.id)
        assert results == []  # no-context rejection writes no scanner_results row


async def test_run_scanner_skips_symbol_already_scanned_for_the_date(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="VCPDEDUPE", exchange="N", instrument_token="vcp3")
        )
        await session.commit()
        symbol_id = symbol_row.id
        await PriceRepository(session).upsert_daily_many(
            symbol_id,
            [Candle(timestamp=datetime(2026, 1, 5), open=108, high=111, low=107, close=110, volume=100_000)],
        )
        await DailyFeatureRepository(session).upsert(symbol_id, date(2026, 1, 5), _QUALIFYING_FEATURES)
        await session.commit()

    async with session_factory() as session:
        symbols = await SymbolRepository(session).list_active()

    manager = ScannerManager(session_factory)
    scanner = VcpScanner(Settings())
    await manager.run_scanner(scanner, symbols, run_id=1)
    second_stats = await manager.run_scanner(scanner, symbols, run_id=2)

    assert second_stats.qualified_count == 0
    assert second_stats.rejected_count == 0

    async with session_factory() as session:
        results = await ScannerResultRepository(session).list_results(scanner_name="vcp_v1")
        assert len(results) == 1  # still just the one row — no duplicate
