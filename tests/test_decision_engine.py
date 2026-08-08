"""Integration tests for app.decision.engine.DecisionEngine: the full
scanner-result -> decision -> alert path, against an in-memory DB."""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.manager import AlertManager
from app.alerts.queue import AlertQueue
from app.config.settings import Settings
from app.decision.engine import DecisionEngine
from app.providers.base_provider import ProviderSymbol
from app.repositories.alert_repository import AlertRepository
from app.repositories.market_repository import SymbolRepository
from app.repositories.scanner_repository import ScannerResultRepository

_MARKET_OPEN = datetime(2026, 1, 5, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))  # Monday

_QUALIFYING_SNAPSHOT: dict[str, object] = {
    "price": "110",
    "ema20": "105",
    "ema50": "100",
    "ema200": "90",
    "adx14": "30",
    "relative_volume": "2.5",
    "resistance_level": "112",
    "breakout_level": "112",
}


async def _seed_qualified_result(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    symbol: str = "TCS",
    score: Decimal = Decimal("95"),
) -> int:
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol, exchange="N", instrument_token=symbol)
        )
        await session.commit()

        await ScannerResultRepository(session).upsert(
            symbol_id=symbol_row.id,
            scanner_name="breakout_v1",
            date=date(2026, 1, 5),
            score=score,
            status="qualified",
            reason="all conditions met",
            feature_snapshot=_QUALIFYING_SNAPSHOT,
        )
        await session.commit()
        return symbol_row.id


async def test_run_all_creates_alert_for_qualifying_candidate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_qualified_result(session_factory)

    settings = Settings(decision_min_alert_score=80.0)
    queue = AlertQueue()
    alert_manager = AlertManager(session_factory, settings, queue)
    engine = DecisionEngine(session_factory, settings, alert_manager)

    result = await engine.run_all(now=_MARKET_OPEN)

    assert result.candidates_evaluated == 1
    assert result.alerts_created == 1
    assert queue.qsize() == 1

    async with session_factory() as session:
        alerts = await AlertRepository(session).list_recent()
        assert len(alerts) == 1
        assert alerts[0].status == "PENDING"


async def test_run_all_does_not_create_alert_when_score_too_low(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_qualified_result(session_factory, score=Decimal("50"))

    settings = Settings(decision_min_alert_score=80.0)
    queue = AlertQueue()
    alert_manager = AlertManager(session_factory, settings, queue)
    engine = DecisionEngine(session_factory, settings, alert_manager)

    result = await engine.run_all(now=_MARKET_OPEN)

    assert result.candidates_evaluated == 1
    assert result.alerts_created == 0
    assert result.watch_count == 1
    assert queue.qsize() == 0


async def test_run_all_ignores_rejected_scanner_results(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        symbol_row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="TCS", exchange="N", instrument_token="1")
        )
        await session.commit()
        await ScannerResultRepository(session).upsert(
            symbol_id=symbol_row.id,
            scanner_name="breakout_v1",
            date=date(2026, 1, 5),
            score=Decimal("30"),
            status="rejected",
            reason="failed conditions",
            feature_snapshot=_QUALIFYING_SNAPSHOT,
        )
        await session.commit()

    settings = Settings()
    queue = AlertQueue()
    engine = DecisionEngine(
        session_factory, settings, AlertManager(session_factory, settings, queue)
    )

    result = await engine.run_all(now=_MARKET_OPEN)

    assert result.candidates_evaluated == 0
    assert result.alerts_created == 0


async def test_run_all_repeated_tick_does_not_duplicate_alert(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The scanner runs every minute, so the same qualifying candidate would
    otherwise be re-evaluated on every tick — confirms the alert layer's
    dedup still holds end-to-end through the engine."""
    await _seed_qualified_result(session_factory)

    settings = Settings(decision_min_alert_score=80.0)
    queue = AlertQueue()
    alert_manager = AlertManager(session_factory, settings, queue)
    engine = DecisionEngine(session_factory, settings, alert_manager)

    first = await engine.run_all(now=_MARKET_OPEN)
    second = await engine.run_all(now=_MARKET_OPEN)

    assert first.alerts_created == 1
    assert second.alerts_created == 0

    async with session_factory() as session:
        alerts = await AlertRepository(session).list_recent()
        assert len(alerts) == 1
