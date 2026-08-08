"""Integration tests for app.alerts.manager.AlertManager against an in-memory DB."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.manager import AlertManager
from app.alerts.queue import AlertQueue
from app.config.settings import Settings
from app.decision.models import Decision, DecisionResult, Quality
from app.providers.base_provider import ProviderSymbol
from app.repositories.alert_repository import AlertEventRepository, AlertRepository
from app.repositories.market_repository import SymbolRepository

_NOW = datetime(2026, 1, 5, 10, 0, 0)


def _alert_decision(**overrides: object) -> DecisionResult:
    defaults: dict[str, object] = dict(
        symbol="TCS",
        scanner_name="breakout_v1",
        signal_type="BREAKOUT",
        decision=Decision.ALERT,
        score=91.0,
        quality=Quality.HIGH,
        passed_rules=["minimum_score", "trend", "relative_volume", "adx", "resistance_proximity"],
        feature_snapshot={
            "price": "110",
            "breakout_level": "112",
            "resistance_level": "112",
            "date": "2026-01-05",
        },
        timestamp=_NOW,
    )
    defaults.update(overrides)
    return DecisionResult(**defaults)  # type: ignore[arg-type]


async def _seed_symbol(
    session_factory: async_sessionmaker[AsyncSession], symbol: str = "TCS"
) -> int:
    async with session_factory() as session:
        row = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol=symbol, exchange="N", instrument_token=symbol)
        )
        await session.commit()
        return row.id


async def test_process_creates_alert_and_queues_it(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory)
    queue = AlertQueue()
    manager = AlertManager(session_factory, Settings(), queue)

    alert_id = await manager.process(_alert_decision(), symbol_id=symbol_id, now=_NOW)

    assert alert_id is not None
    assert queue.qsize() == 1
    queued = await queue.get()
    assert queued.alert_id == alert_id

    async with session_factory() as session:
        alert = await AlertRepository(session).get_by_id(alert_id)
        assert alert is not None
        assert alert.status == "PENDING"
        assert alert.passed_rules == _alert_decision().passed_rules

        events = await AlertEventRepository(session).list_for_alert(alert_id)
        assert [e.event_type for e in events] == ["CREATED", "QUEUED"]


async def test_process_skips_watch_and_reject_decisions(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory)
    queue = AlertQueue()
    manager = AlertManager(session_factory, Settings(), queue)

    watch_id = await manager.process(
        _alert_decision(decision=Decision.WATCH, quality=Quality.LOW), symbol_id=symbol_id, now=_NOW
    )
    reject_id = await manager.process(
        _alert_decision(decision=Decision.REJECT, quality=None), symbol_id=symbol_id, now=_NOW
    )

    assert watch_id is None
    assert reject_id is None
    assert queue.qsize() == 0


async def test_process_suppresses_duplicate_fingerprint(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory)
    queue = AlertQueue()
    manager = AlertManager(session_factory, Settings(), queue)

    first_id = await manager.process(_alert_decision(), symbol_id=symbol_id, now=_NOW)
    second_id = await manager.process(_alert_decision(), symbol_id=symbol_id, now=_NOW)

    assert first_id is not None
    assert second_id is None  # same fingerprint, same day — suppressed
    assert queue.qsize() == 1  # only the first ever got queued

    async with session_factory() as session:
        events = await AlertEventRepository(session).list_for_alert(first_id)
        assert "SUPPRESSED" in [e.event_type for e in events]


async def test_process_suppresses_within_cooldown_even_with_new_fingerprint(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    symbol_id = await _seed_symbol(session_factory)
    queue = AlertQueue()
    manager = AlertManager(session_factory, Settings(alert_cooldown_minutes=30), queue)

    await manager.process(_alert_decision(), symbol_id=symbol_id, now=_NOW)

    # A different breakout level -> different fingerprint, but the same
    # symbol/signal within the cooldown window should still be suppressed.
    different_snapshot = _alert_decision(
        feature_snapshot={
            "price": "115",
            "breakout_level": "999",
            "resistance_level": "999",
            "date": "2026-01-05",
        }
    )
    second_id = await manager.process(different_snapshot, symbol_id=symbol_id, now=_NOW)

    assert second_id is None
    assert queue.qsize() == 1
