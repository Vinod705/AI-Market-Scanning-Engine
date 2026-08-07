"""Tests for app.repositories.scanner_repository."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.providers.base_provider import ProviderSymbol
from app.repositories.market_repository import SymbolRepository
from app.repositories.scanner_repository import (
    ScannerLogRepository,
    ScannerResultRepository,
    ScannerRunRepository,
)


async def _seed_symbol(session: AsyncSession, symbol: str = "TCS") -> int:
    row = await SymbolRepository(session).upsert(
        ProviderSymbol(symbol=symbol, exchange="N", instrument_token=symbol)
    )
    await session.commit()
    return row.id


async def test_upsert_is_idempotent_dedup(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A second scan on the same (symbol, scanner, date) updates the existing
    row instead of creating a duplicate alert."""
    async with session_factory() as session:
        symbol_id = await _seed_symbol(session)
        repo = ScannerResultRepository(session)

        await repo.upsert(
            symbol_id=symbol_id,
            scanner_name="breakout_v1",
            date=date(2026, 1, 5),
            score=Decimal("55.00"),
            status="rejected",
            reason="failed 1/7: near_resistance",
            feature_snapshot={"price": "100"},
        )
        await repo.upsert(
            symbol_id=symbol_id,
            scanner_name="breakout_v1",
            date=date(2026, 1, 5),
            score=Decimal("82.50"),
            status="qualified",
            reason="all conditions met",
            feature_snapshot={"price": "110"},
        )
        await session.commit()

        results = await repo.list_results(scanner_name="breakout_v1")
        assert len(results) == 1
        assert results[0].status == "qualified"
        assert float(results[0].score) == 82.50


async def test_exists_for_date(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        symbol_id = await _seed_symbol(session)
        repo = ScannerResultRepository(session)

        assert await repo.exists_for_date(symbol_id, "breakout_v1", date(2026, 1, 5)) is False

        await repo.upsert(
            symbol_id=symbol_id,
            scanner_name="breakout_v1",
            date=date(2026, 1, 5),
            score=Decimal("70"),
            status="qualified",
            reason="ok",
            feature_snapshot={},
        )
        await session.commit()

        assert await repo.exists_for_date(symbol_id, "breakout_v1", date(2026, 1, 5)) is True
        assert await repo.exists_for_date(symbol_id, "breakout_v1", date(2026, 1, 6)) is False


async def test_list_results_filters_by_status(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        symbol_repo = SymbolRepository(session)
        tcs = await symbol_repo.upsert(
            ProviderSymbol(symbol="TCS", exchange="N", instrument_token="1")
        )
        infy = await symbol_repo.upsert(
            ProviderSymbol(symbol="INFY", exchange="N", instrument_token="2")
        )
        await session.commit()

        repo = ScannerResultRepository(session)
        await repo.upsert(
            symbol_id=tcs.id,
            scanner_name="breakout_v1",
            date=date(2026, 1, 5),
            score=Decimal("90"),
            status="qualified",
            reason="ok",
            feature_snapshot={},
        )
        await repo.upsert(
            symbol_id=infy.id,
            scanner_name="breakout_v1",
            date=date(2026, 1, 5),
            score=Decimal("30"),
            status="rejected",
            reason="no",
            feature_snapshot={},
        )
        await session.commit()

        qualified = await repo.list_results(status="qualified")
        assert len(qualified) == 1
        assert qualified[0].symbol_id == tcs.id


async def test_get_status_summary(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        symbol_id = await _seed_symbol(session)
        repo = ScannerResultRepository(session)
        await repo.upsert(
            symbol_id=symbol_id,
            scanner_name="breakout_v1",
            date=date(2026, 1, 5),
            score=Decimal("90"),
            status="qualified",
            reason="ok",
            feature_snapshot={},
        )
        await repo.upsert(
            symbol_id=symbol_id,
            scanner_name="breakout_v1",
            date=date(2026, 1, 6),
            score=Decimal("30"),
            status="rejected",
            reason="no",
            feature_snapshot={},
        )
        await session.commit()

        summary = await repo.get_status_summary("breakout_v1")
        assert summary.total_results == 2
        assert summary.qualified_count == 1
        assert summary.last_run_at is not None


async def test_run_repository_start_finish_and_list_names(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repo = ScannerRunRepository(session)
        start_time = datetime(2026, 1, 5, 9, 0, 0)
        run = await repo.start("breakout_v1", start_time)
        await session.commit()

        assert run.id is not None
        assert run.finish_time is None

        finish_time = datetime(2026, 1, 5, 9, 5, 0)
        await repo.finish(
            run,
            finish_time=finish_time,
            symbols_scanned=10,
            qualified_count=2,
            rejected_count=8,
            error_count=0,
        )
        await session.commit()

        assert run.duration == 300.0
        assert run.qualified_count == 2

        names = await repo.list_scanner_names()
        assert names == ["breakout_v1"]

        recent = await repo.get_recent(scanner_name="breakout_v1")
        assert len(recent) == 1
        assert recent[0].id == run.id


async def test_get_recent_runs_filters_by_scanner_name(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repo = ScannerRunRepository(session)
        await repo.start("breakout_v1", datetime(2026, 1, 5, 9, 0, 0))
        await repo.start("other_scanner", datetime(2026, 1, 5, 9, 0, 0))
        await session.commit()

        recent = await repo.get_recent(scanner_name="other_scanner")
        assert len(recent) == 1
        assert recent[0].scanner_name == "other_scanner"


async def test_scanner_log_repository_writes_entry(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        symbol_id = await _seed_symbol(session)
        run = await ScannerRunRepository(session).start(
            "breakout_v1", datetime(2026, 1, 5, 9, 0, 0)
        )
        await session.commit()

        entry = await ScannerLogRepository(session).log(
            run_id=run.id,
            scanner_name="breakout_v1",
            level="info",
            message="validation failed: missing required field: ema200",
            symbol_id=symbol_id,
        )
        await session.commit()

        assert entry.id is not None
        assert entry.symbol_id == symbol_id
