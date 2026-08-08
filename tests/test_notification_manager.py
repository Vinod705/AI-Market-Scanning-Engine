"""Tests for app.notifications.manager.NotificationManager: retry, failure
handling, and restart recovery."""

from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.queue import AlertQueue
from app.config.settings import Settings
from app.notifications.base import DeliveryResult, NotificationProvider
from app.notifications.manager import NotificationManager
from app.providers.base_provider import ProviderSymbol
from app.repositories.alert_repository import (
    AlertDeliveryLogRepository,
    AlertEventRepository,
    AlertRepository,
)
from app.repositories.market_repository import SymbolRepository


class _FakeProvider(NotificationProvider):
    name = "fake"

    def __init__(self, results: list[DeliveryResult]) -> None:
        self._results = list(results)
        self.calls = 0

    async def send_message(self, *, recipient: str, text: str) -> DeliveryResult:
        self.calls += 1
        return self._results[min(self.calls - 1, len(self._results) - 1)]

    async def send_template(
        self, *, recipient, template_name, language, parameters
    ) -> DeliveryResult:  # noqa: ANN001
        raise NotImplementedError

    async def health_check(self) -> bool:
        return True


async def _seed_alert(
    session_factory: async_sessionmaker[AsyncSession], *, fingerprint: str = "fp-1"
) -> int:
    async with session_factory() as session:
        symbol = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="TCS", exchange="N", instrument_token="1")
        )
        await session.commit()

        alert = await AlertRepository(session).create(
            symbol_id=symbol.id,
            scanner_name="breakout_v1",
            signal_type="BREAKOUT",
            decision="ALERT",
            score=Decimal("91"),
            quality="HIGH",
            entry_reference=Decimal("110"),
            breakout_level=Decimal("112"),
            support_level=None,
            resistance_level=Decimal("112"),
            feature_snapshot={
                "price": "110",
                "ema20": "105",
                "ema50": "100",
                "ema200": "90",
                "relative_volume": "2.5",
                "adx14": "30",
            },
            reason="all conditions met",
            passed_rules=["trend", "adx"],
            fingerprint=fingerprint,
            signal_date=date(2026, 1, 5),
            expires_at=None,
        )
        await session.commit()
        return alert.id


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = dict(
        telegram_chat_id="123456789", alert_max_retries=3, alert_retry_delay_seconds=0.01
    )
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


async def test_deliver_now_success_marks_sent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alert_id = await _seed_alert(session_factory)
    provider = _FakeProvider(
        [DeliveryResult(success=True, status="SENT", provider_message_id="wamid.1")]
    )
    manager = NotificationManager(session_factory, _settings(), AlertQueue(), provider)

    await manager.deliver_now(alert_id)

    assert provider.calls == 1
    async with session_factory() as session:
        alert = await AlertRepository(session).get_by_id(alert_id)
        assert alert is not None
        assert alert.status == "SENT"
        events = [
            e.event_type for e in await AlertEventRepository(session).list_for_alert(alert_id)
        ]
        assert events == ["SENT"]
        logs = await AlertDeliveryLogRepository(session).list_for_alert(alert_id)
        assert len(logs) == 1
        assert logs[0].status == "SENT"


async def test_deliver_now_retries_then_succeeds(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alert_id = await _seed_alert(session_factory)
    provider = _FakeProvider(
        [
            DeliveryResult(
                success=False, status="RETRYING", error_message="timeout", retryable=True
            ),
            DeliveryResult(success=True, status="SENT", provider_message_id="wamid.2"),
        ]
    )
    manager = NotificationManager(session_factory, _settings(), AlertQueue(), provider)

    await manager.deliver_now(alert_id)

    assert provider.calls == 2
    async with session_factory() as session:
        alert = await AlertRepository(session).get_by_id(alert_id)
        assert alert is not None
        assert alert.status == "SENT"
        events = [
            e.event_type for e in await AlertEventRepository(session).list_for_alert(alert_id)
        ]
        assert events == ["RETRYING", "SENT"]


async def test_deliver_now_exhausts_retries_marks_failed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alert_id = await _seed_alert(session_factory)
    failing = DeliveryResult(
        success=False, status="RETRYING", error_message="server error", retryable=True
    )
    provider = _FakeProvider([failing, failing, failing])
    manager = NotificationManager(
        session_factory, _settings(alert_max_retries=3), AlertQueue(), provider
    )

    await manager.deliver_now(alert_id)

    assert provider.calls == 3
    async with session_factory() as session:
        alert = await AlertRepository(session).get_by_id(alert_id)
        assert alert is not None
        assert alert.status == "FAILED"


async def test_deliver_now_permanent_failure_stops_after_one_attempt(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alert_id = await _seed_alert(session_factory)
    provider = _FakeProvider(
        [
            DeliveryResult(
                success=False, status="FAILED", error_message="invalid recipient", retryable=False
            )
        ]
    )
    manager = NotificationManager(
        session_factory, _settings(alert_max_retries=3), AlertQueue(), provider
    )

    await manager.deliver_now(alert_id)

    assert provider.calls == 1  # never retried a permanent failure
    async with session_factory() as session:
        alert = await AlertRepository(session).get_by_id(alert_id)
        assert alert is not None
        assert alert.status == "FAILED"


async def test_deliver_now_skips_already_sent_alert(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alert_id = await _seed_alert(session_factory)
    async with session_factory() as session:
        alert = await AlertRepository(session).get_by_id(alert_id)
        assert alert is not None
        await AlertRepository(session).update_status(alert, "SENT")
        await session.commit()

    provider = _FakeProvider([DeliveryResult(success=True, status="SENT")])
    manager = NotificationManager(session_factory, _settings(), AlertQueue(), provider)

    await manager.deliver_now(alert_id)

    assert provider.calls == 0  # already delivered — never re-sent


async def test_recover_pending_redrives_only_pending_and_retrying(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    pending_id = await _seed_alert(session_factory, fingerprint="fp-pending")
    sent_id = await _seed_alert(session_factory, fingerprint="fp-sent")

    async with session_factory() as session:
        sent_alert = await AlertRepository(session).get_by_id(sent_id)
        assert sent_alert is not None
        await AlertRepository(session).update_status(sent_alert, "SENT")
        await session.commit()

    provider = _FakeProvider(
        [DeliveryResult(success=True, status="SENT", provider_message_id="wamid.recovered")]
    )
    manager = NotificationManager(session_factory, _settings(), AlertQueue(), provider)

    recovered_count = await manager.recover_pending()

    assert recovered_count == 1
    assert provider.calls == 1  # only the pending one was redriven

    async with session_factory() as session:
        pending_alert = await AlertRepository(session).get_by_id(pending_id)
        assert pending_alert is not None
        assert pending_alert.status == "SENT"
