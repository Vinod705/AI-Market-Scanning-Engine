"""Tests for app.repositories.market_regime_snapshot_repository.MarketRegimeSnapshotRepository."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.market.breadth import compute_market_breadth
from app.analytics.market.market_models import MarketRegimeEvidence, MarketRegimeState
from app.repositories.market_regime_snapshot_repository import MarketRegimeSnapshotRepository


async def _breadth_snapshot(session_factory: async_sessionmaker[AsyncSession]):
    async with session_factory() as session:
        return await compute_market_breadth(session)


def _evidence(*, computed_at: datetime, regime: MarketRegimeState | None, score: Decimal | None, breadth) -> MarketRegimeEvidence:
    return MarketRegimeEvidence(
        as_of=date(2026, 1, 5),
        computed_at=computed_at,
        breadth=breadth,
        index_symbol="NIFTY 50",
        index_trend_direction="up",
        index_trend_strength=Decimal("70"),
        volatility_index_symbol="NIFTY 50",
        volatility_state="contraction",
        sector_leading_pct=Decimal("60"),
        score=score,
        regime=regime,
        evidence_sources_used=["advance_decline", "index_trend"],
        missing_evidence=["volatility"],
    )


async def test_get_latest_returns_none_when_empty(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        result = await MarketRegimeSnapshotRepository(session).get_latest()
    assert result is None


async def test_insert_then_get_latest_round_trips_fields(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    breadth = await _breadth_snapshot(session_factory)
    now = datetime.now(UTC)

    async with session_factory() as session:
        repo = MarketRegimeSnapshotRepository(session)
        await repo.insert(
            _evidence(computed_at=now, regime=MarketRegimeState.SUPPORTIVE, score=Decimal("78.5"), breadth=breadth)
        )
        await session.commit()

        latest = await repo.get_latest()

    assert latest is not None
    assert latest.regime == "SUPPORTIVE"
    assert latest.score == Decimal("78.5")
    assert latest.index_symbol == "NIFTY 50"
    assert latest.evidence_sources_used == ["advance_decline", "index_trend"]
    assert latest.missing_evidence == ["volatility"]


async def test_get_latest_returns_most_recently_computed_row(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    breadth = await _breadth_snapshot(session_factory)
    now = datetime.now(UTC)

    async with session_factory() as session:
        repo = MarketRegimeSnapshotRepository(session)
        await repo.insert(
            _evidence(computed_at=now - timedelta(hours=1), regime=MarketRegimeState.RISK_OFF, score=Decimal("20"), breadth=breadth)
        )
        await repo.insert(
            _evidence(computed_at=now, regime=MarketRegimeState.SUPPORTIVE, score=Decimal("80"), breadth=breadth)
        )
        await session.commit()

        latest = await repo.get_latest()

    assert latest is not None
    assert latest.regime == "SUPPORTIVE"


async def test_insert_handles_no_regime_no_score(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A cold-start snapshot with no evidence at all must persist honestly
    as regime=None/score=None, never a fabricated default."""
    breadth = await _breadth_snapshot(session_factory)
    now = datetime.now(UTC)

    async with session_factory() as session:
        repo = MarketRegimeSnapshotRepository(session)
        await repo.insert(_evidence(computed_at=now, regime=None, score=None, breadth=breadth))
        await session.commit()

        latest = await repo.get_latest()

    assert latest is not None
    assert latest.regime is None
    assert latest.score is None
