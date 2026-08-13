"""Tests for app.repositories.alert_repository."""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.time import utc_now
from app.providers.base_provider import ProviderSymbol
from app.repositories.alert_repository import (
    AlertDeliveryLogRepository,
    AlertEventRepository,
    AlertRepository,
)
from app.repositories.market_repository import SymbolRepository


async def _seed_symbol(session: AsyncSession, symbol: str = "TCS") -> int:
    row = await SymbolRepository(session).upsert(
        ProviderSymbol(symbol=symbol, exchange="N", instrument_token=symbol)
    )
    await session.commit()
    return row.id


async def _create_alert(
    repo: AlertRepository,
    symbol_id: int,
    *,
    fingerprint: str = "fp-1",
    signal_date: date = date(2026, 1, 5),
):
    return await repo.create(
        symbol_id=symbol_id,
        scanner_name="breakout_v1",
        signal_type="BREAKOUT",
        decision="ALERT",
        score=Decimal("91.00"),
        quality="HIGH",
        entry_reference=Decimal("110"),
        breakout_level=Decimal("112"),
        support_level=Decimal("100"),
        resistance_level=Decimal("112"),
        feature_snapshot={"price": "110"},
        reason="all conditions met: trend, adx",
        passed_rules=["trend", "adx"],
        fingerprint=fingerprint,
        signal_date=signal_date,
        expires_at=None,
    )


async def test_create_and_get_by_id(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        symbol_id = await _seed_symbol(session)
        repo = AlertRepository(session)
        alert = await _create_alert(repo, symbol_id)
        await session.commit()

        fetched = await repo.get_by_id(alert.id)
        assert fetched is not None
        assert fetched.status == "PENDING"
        assert fetched.passed_rules == ["trend", "adx"]


async def test_get_by_fingerprint_returns_most_recent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        symbol_id = await _seed_symbol(session)
        repo = AlertRepository(session)
        first = await _create_alert(repo, symbol_id, fingerprint="fp-shared")
        first.status = "EXPIRED"
        await session.flush()
        second = await _create_alert(repo, symbol_id, fingerprint="fp-shared")
        await session.commit()

        latest = await repo.get_by_fingerprint("fp-shared")
        assert latest is not None
        assert latest.id == second.id


async def test_get_most_recent_for_signal_respects_since(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        symbol_id = await _seed_symbol(session)
        repo = AlertRepository(session)
        await _create_alert(repo, symbol_id, fingerprint="fp-a")
        await session.commit()

        far_future = utc_now() + timedelta(days=1)
        recent = await repo.get_most_recent_for_signal(symbol_id, "BREAKOUT", since=far_future)
        assert recent is None

        far_past = utc_now() - timedelta(days=1)
        recent = await repo.get_most_recent_for_signal(symbol_id, "BREAKOUT", since=far_past)
        assert recent is not None


async def test_list_by_status_for_restart_recovery(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        symbol_id = await _seed_symbol(session)
        repo = AlertRepository(session)
        pending = await _create_alert(repo, symbol_id, fingerprint="fp-pending")
        sent = await _create_alert(repo, symbol_id, fingerprint="fp-sent")
        await repo.update_status(sent, "SENT")
        await session.commit()

        recoverable = await repo.list_by_status(["PENDING", "RETRYING"])
        ids = {a.id for a in recoverable}
        assert pending.id in ids
        assert sent.id not in ids


async def test_expire_stale_marks_past_expiry_and_returns_them(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        symbol_id = await _seed_symbol(session)
        repo = AlertRepository(session)
        alert = await _create_alert(repo, symbol_id, fingerprint="fp-expiring")
        alert.expires_at = utc_now() - timedelta(minutes=1)
        await session.commit()

        expired = await repo.expire_stale(now=utc_now())
        assert len(expired) == 1
        assert expired[0].id == alert.id

        refreshed = await repo.get_by_id(alert.id)
        assert refreshed is not None
        assert refreshed.status == "EXPIRED"


async def test_list_created_since_excludes_older_alerts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        symbol_id = await _seed_symbol(session)
        repo = AlertRepository(session)
        old = await _create_alert(repo, symbol_id, fingerprint="fp-old")
        old.created_at = utc_now() - timedelta(days=1)
        new = await _create_alert(repo, symbol_id, fingerprint="fp-new")
        await session.commit()

        since = utc_now() - timedelta(hours=1)
        recent = await repo.list_created_since(since)
        ids = {a.id for a in recent}
        assert new.id in ids
        assert old.id not in ids


async def test_list_created_since_orders_oldest_first(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        symbol_id = await _seed_symbol(session)
        repo = AlertRepository(session)
        first = await _create_alert(repo, symbol_id, fingerprint="fp-first")
        first.created_at = utc_now() - timedelta(minutes=10)
        second = await _create_alert(repo, symbol_id, fingerprint="fp-second")
        second.created_at = utc_now() - timedelta(minutes=5)
        await session.commit()

        since = utc_now() - timedelta(hours=1)
        ordered = await repo.list_created_since(since)
        ordered_ids = [a.id for a in ordered]
        assert ordered_ids.index(first.id) < ordered_ids.index(second.id)


async def test_get_status_summary(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        symbol_id = await _seed_symbol(session)
        repo = AlertRepository(session)
        sent = await _create_alert(repo, symbol_id, fingerprint="fp-sent-2")
        await repo.update_status(sent, "SENT")
        await _create_alert(repo, symbol_id, fingerprint="fp-pending-2")
        await session.commit()

        summary = await repo.get_status_summary()
        assert summary.total_alerts == 2
        assert summary.sent_count == 1
        assert summary.pending_count == 1


async def test_alert_event_repository_logs_and_lists(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        symbol_id = await _seed_symbol(session)
        alert = await _create_alert(AlertRepository(session), symbol_id)
        await session.commit()

        event_repo = AlertEventRepository(session)
        await event_repo.log(alert_id=alert.id, event_type="CREATED")
        await event_repo.log(
            alert_id=alert.id, event_type="QUEUED", event_metadata={"queue_size": 1}
        )
        await session.commit()

        events = await event_repo.list_for_alert(alert.id)
        assert [e.event_type for e in events] == ["CREATED", "QUEUED"]


async def test_alert_delivery_log_repository(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        symbol_id = await _seed_symbol(session)
        alert = await _create_alert(AlertRepository(session), symbol_id)
        await session.commit()

        log_repo = AlertDeliveryLogRepository(session)
        await log_repo.log_attempt(
            alert_id=alert.id,
            provider="telegram",
            status="FAILED",
            attempt_number=1,
            error_message="timeout",
        )
        await log_repo.log_attempt(
            alert_id=alert.id,
            provider="telegram",
            status="SENT",
            attempt_number=2,
            response_metadata={"status_code": 200},
        )
        await session.commit()

        latest = await log_repo.get_latest_for_alert(alert.id)
        assert latest is not None
        assert latest.status == "SENT"
        assert latest.attempt_number == 2

        all_attempts = await log_repo.list_for_alert(alert.id)
        assert len(all_attempts) == 2
