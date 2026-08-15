"""Tests for app.repositories.sector_rrg_snapshot_repository.SectorRrgSnapshotRepository."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.sector.sector_models import SectorEvidence, SectorRotationState
from app.repositories.sector_rrg_snapshot_repository import SectorRrgSnapshotRepository


def _evidence(sector_symbol: str, *, rotation_state: SectorRotationState | None, score: Decimal | None) -> SectorEvidence:
    return SectorEvidence(
        sector_symbol=sector_symbol,
        benchmark_symbol="NIFTY 50",
        date=date(2026, 1, 5),
        rs=Decimal("1.02"),
        rs_ratio=Decimal("103.5"),
        rs_momentum=Decimal("101.2"),
        rotation_state=rotation_state,
        momentum_score=Decimal("62.0"),
        trend_strength=Decimal("70.0"),
        price_performance_pct=Decimal("4.5"),
        momentum_acceleration=Decimal("2.1"),
        breadth=None,
        volume_participation=None,
        score=score,
        evidence_sources_used=["relative_strength"],
        missing_evidence=["breadth"],
        computed_at=datetime.now(UTC),
    )


async def test_get_latest_batch_empty_when_no_snapshots(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        result = await SectorRrgSnapshotRepository(session).get_latest_batch()
    assert result == []


async def test_insert_many_then_get_latest_batch_round_trips(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    evidences = [
        _evidence("NIFTY IT", rotation_state=SectorRotationState.LEADING, score=Decimal("85")),
        _evidence("NIFTY BANK", rotation_state=SectorRotationState.LAGGING, score=Decimal("30")),
    ]

    async with session_factory() as session:
        repo = SectorRrgSnapshotRepository(session)
        await repo.insert_many(evidences, computed_at=now)
        await session.commit()

        rows = await repo.get_latest_batch()

    by_symbol = {row.sector_symbol: row for row in rows}
    assert set(by_symbol) == {"NIFTY IT", "NIFTY BANK"}
    assert by_symbol["NIFTY IT"].rotation_state == "LEADING"
    assert by_symbol["NIFTY IT"].score == Decimal("85")
    assert by_symbol["NIFTY BANK"].rotation_state == "LAGGING"


async def test_get_latest_batch_returns_only_most_recent_row_per_sector(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)

    async with session_factory() as session:
        repo = SectorRrgSnapshotRepository(session)
        await repo.insert_many(
            [_evidence("NIFTY IT", rotation_state=SectorRotationState.LAGGING, score=Decimal("35"))],
            computed_at=now - timedelta(hours=1),
        )
        await repo.insert_many(
            [_evidence("NIFTY IT", rotation_state=SectorRotationState.LEADING, score=Decimal("90"))],
            computed_at=now,
        )
        await session.commit()

        rows = await repo.get_latest_batch()

    assert len(rows) == 1
    assert rows[0].rotation_state == "LEADING"
    assert rows[0].score == Decimal("90")


async def test_insert_many_handles_no_rotation_state_no_score(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repo = SectorRrgSnapshotRepository(session)
        await repo.insert_many(
            [_evidence("NIFTY REALTY", rotation_state=None, score=None)], computed_at=datetime.now(UTC)
        )
        await session.commit()

        rows = await repo.get_latest_batch()

    assert rows[0].rotation_state is None
    assert rows[0].score is None
