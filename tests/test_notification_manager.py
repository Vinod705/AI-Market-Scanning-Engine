"""Tests for app.notifications.manager.NotificationManager: retry, failure
handling, and restart recovery."""

from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.queue import AlertQueue
from app.config.settings import Settings
from app.notifications.base import DeliveryResult, NotificationProvider
from app.notifications.manager import NotificationManager
from app.notifications.router import NotificationRouter
from app.providers.base_provider import ProviderSymbol
from app.repositories.alert_repository import (
    AlertDeliveryLogRepository,
    AlertEventRepository,
    AlertRepository,
)
from app.repositories.market_repository import SymbolRepository


class _FakeProvider(NotificationProvider):
    def __init__(self, results: list[DeliveryResult], *, name: str = "fake") -> None:
        self.name = name
        self._results = list(results)
        self.calls = 0

    @property
    def default_recipient(self) -> str:
        return "fake-recipient"

    async def send_message(self, *, recipient: str, text: str) -> DeliveryResult:
        self.calls += 1
        return self._results[min(self.calls - 1, len(self._results) - 1)]

    async def send_template(
        self, *, recipient, template_name, language, parameters
    ) -> DeliveryResult:  # noqa: ANN001
        raise NotImplementedError

    async def health_check(self) -> bool:
        return True


def _router(
    *,
    default: NotificationProvider,
    ipo: NotificationProvider | None = None,
    fno: NotificationProvider | None = None,
) -> NotificationRouter:
    """Builds a router around a single "default" fake unless the test
    needs to assert something about IPO/F&O routing specifically — the
    never-configured `_FakeProvider([])` placeholders would raise
    (index out of range) if a breakout_v1 test ever misrouted to them,
    which is exactly the guard we want."""
    return NotificationRouter(
        default=default,
        ipo=ipo or _FakeProvider([], name="unused-ipo"),
        fno=fno or _FakeProvider([], name="unused-fno"),
    )


async def _seed_alert(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    fingerprint: str = "fp-1",
    scanner_name: str = "breakout_v1",
    signal_type: str = "BREAKOUT",
    alert_category: str | None = None,
) -> int:
    async with session_factory() as session:
        symbol = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="TCS", exchange="N", instrument_token="1")
        )
        await session.commit()

        snapshot: dict[str, object] = {
            "price": "110",
            "ema20": "105",
            "ema50": "100",
            "ema200": "90",
            "relative_volume": "2.5",
            "adx14": "30",
        }
        if alert_category is not None:
            snapshot["alert_category"] = alert_category

        alert = await AlertRepository(session).create(
            symbol_id=symbol.id,
            scanner_name=scanner_name,
            signal_type=signal_type,
            decision="ALERT",
            score=Decimal("91"),
            quality="HIGH",
            entry_reference=Decimal("110"),
            breakout_level=Decimal("112"),
            support_level=None,
            resistance_level=Decimal("112"),
            feature_snapshot=snapshot,
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
    manager = NotificationManager(
        session_factory, _settings(), AlertQueue(), _router(default=provider)
    )

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
    manager = NotificationManager(
        session_factory, _settings(), AlertQueue(), _router(default=provider)
    )

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
        session_factory, _settings(alert_max_retries=3), AlertQueue(), _router(default=provider)
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
        session_factory, _settings(alert_max_retries=3), AlertQueue(), _router(default=provider)
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
    manager = NotificationManager(
        session_factory, _settings(), AlertQueue(), _router(default=provider)
    )

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
    manager = NotificationManager(
        session_factory, _settings(), AlertQueue(), _router(default=provider)
    )

    recovered_count = await manager.recover_pending()

    assert recovered_count == 1
    assert provider.calls == 1  # only the pending one was redriven

    async with session_factory() as session:
        pending_alert = await AlertRepository(session).get_by_id(pending_id)
        assert pending_alert is not None
        assert pending_alert.status == "SENT"


# --- Dual-bot (IPO/F&O) routing ---


async def test_ipo_alert_is_delivered_only_by_the_ipo_bot(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alert_id = await _seed_alert(
        session_factory,
        scanner_name="ipo_intraday_v1",
        signal_type="IPO_INTRADAY",
        alert_category="IPO_MOMENTUM",
    )
    default_bot = _FakeProvider([], name="default")
    ipo_bot = _FakeProvider([DeliveryResult(success=True, status="SENT")], name="telegram_ipo")
    fno_bot = _FakeProvider([], name="telegram_fno")
    manager = NotificationManager(
        session_factory,
        _settings(),
        AlertQueue(),
        _router(default=default_bot, ipo=ipo_bot, fno=fno_bot),
    )

    await manager.deliver_now(alert_id)

    assert ipo_bot.calls == 1
    assert default_bot.calls == 0
    assert fno_bot.calls == 0


async def test_fno_alert_is_delivered_only_by_the_fno_bot(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alert_id = await _seed_alert(
        session_factory,
        scanner_name="fno_momentum_v1",
        signal_type="FNO_MOMENTUM",
        alert_category="FNO_MOMENTUM",
    )
    default_bot = _FakeProvider([], name="default")
    ipo_bot = _FakeProvider([], name="telegram_ipo")
    fno_bot = _FakeProvider([DeliveryResult(success=True, status="SENT")], name="telegram_fno")
    manager = NotificationManager(
        session_factory,
        _settings(),
        AlertQueue(),
        _router(default=default_bot, ipo=ipo_bot, fno=fno_bot),
    )

    await manager.deliver_now(alert_id)

    assert fno_bot.calls == 1
    assert default_bot.calls == 0
    assert ipo_bot.calls == 0


async def test_breakout_v1_alert_still_uses_the_default_bot() -> None:
    """breakout_v1 predates the IPO/F&O split — its alerts have no
    alert_category and must keep going through the original bot."""
    from app.notifications.router import NotificationRouter

    default_bot = _FakeProvider([], name="default")
    ipo_bot = _FakeProvider([], name="telegram_ipo")
    fno_bot = _FakeProvider([], name="telegram_fno")
    router = NotificationRouter(default=default_bot, ipo=ipo_bot, fno=fno_bot)

    assert router.resolve(None) is default_bot
    assert router.resolve("BREAKOUT") is default_bot


async def test_ipo_alert_never_reaches_fno_bot_across_all_categories() -> None:
    from app.candidates.models import AlertCategory
    from app.notifications.router import NotificationRouter

    default_bot = _FakeProvider([], name="default")
    ipo_bot = _FakeProvider([], name="telegram_ipo")
    fno_bot = _FakeProvider([], name="telegram_fno")
    router = NotificationRouter(default=default_bot, ipo=ipo_bot, fno=fno_bot)

    for category in (
        AlertCategory.IPO_PRE_BREAKOUT,
        AlertCategory.IPO_BREAKOUT,
        AlertCategory.IPO_MOMENTUM,
    ):
        assert router.resolve(category.value) is ipo_bot
        assert router.resolve(category.value) is not fno_bot


async def test_fno_alert_never_reaches_ipo_bot_across_all_categories() -> None:
    from app.candidates.models import AlertCategory
    from app.notifications.router import NotificationRouter

    default_bot = _FakeProvider([], name="default")
    ipo_bot = _FakeProvider([], name="telegram_ipo")
    fno_bot = _FakeProvider([], name="telegram_fno")
    router = NotificationRouter(default=default_bot, ipo=ipo_bot, fno=fno_bot)

    for category in (
        AlertCategory.FNO_PRE_BREAKOUT,
        AlertCategory.FNO_BREAKOUT,
        AlertCategory.FNO_MOMENTUM,
    ):
        assert router.resolve(category.value) is fno_bot
        assert router.resolve(category.value) is not ipo_bot


async def test_unavailable_ipo_bot_does_not_fall_back_to_fno_bot(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """If the IPO bot fails, the alert must stay retrying on the IPO
    channel — it must never be silently redirected to the F&O bot."""
    alert_id = await _seed_alert(
        session_factory,
        scanner_name="ipo_intraday_v1",
        signal_type="IPO_INTRADAY",
        alert_category="IPO_BREAKOUT",
    )
    default_bot = _FakeProvider([], name="default")
    ipo_bot = _FakeProvider(
        [
            DeliveryResult(
                success=False, status="FAILED", error_message="unauthorized", retryable=False
            )
        ],
        name="telegram_ipo",
    )
    fno_bot = _FakeProvider([], name="telegram_fno")
    manager = NotificationManager(
        session_factory,
        _settings(),
        AlertQueue(),
        _router(default=default_bot, ipo=ipo_bot, fno=fno_bot),
    )

    await manager.deliver_now(alert_id)

    assert ipo_bot.calls == 1
    assert fno_bot.calls == 0  # never redirected

    async with session_factory() as session:
        alert = await AlertRepository(session).get_by_id(alert_id)
        assert alert is not None
        assert alert.status == "FAILED"  # persisted/queued state per existing AlertQueue behavior


async def test_missing_ipo_credentials_fails_without_affecting_fno_bot() -> None:
    from app.notifications.telegram import TelegramProvider

    settings = _settings(
        telegram_bot_token="default-token",
        telegram_chat_id="default-chat",
        fno_telegram_bot_token="fno-token",
        fno_telegram_chat_id="fno-chat",
        # ipo_telegram_bot_token/chat_id left unset — missing credentials
    )
    default_bot = TelegramProvider(settings, channel_name="telegram")
    ipo_bot = TelegramProvider(
        settings,
        bot_token=settings.ipo_telegram_bot_token,
        chat_id=settings.ipo_telegram_chat_id,
        channel_name="telegram_ipo",
    )
    fno_bot = TelegramProvider(
        settings,
        bot_token=settings.fno_telegram_bot_token,
        chat_id=settings.fno_telegram_chat_id,
        channel_name="telegram_fno",
    )

    assert ipo_bot.configured is False
    assert fno_bot.configured is True
    assert default_bot.configured is True

    result = await ipo_bot.send_message(recipient=ipo_bot.default_recipient, text="test")
    assert result.success is False
    assert result.retryable is False


async def test_missing_fno_credentials_fails_without_affecting_ipo_bot() -> None:
    from app.notifications.telegram import TelegramProvider

    settings = _settings(
        telegram_bot_token="default-token",
        telegram_chat_id="default-chat",
        ipo_telegram_bot_token="ipo-token",
        ipo_telegram_chat_id="ipo-chat",
        # fno_telegram_bot_token/chat_id left unset — missing credentials
    )
    ipo_bot = TelegramProvider(
        settings,
        bot_token=settings.ipo_telegram_bot_token,
        chat_id=settings.ipo_telegram_chat_id,
        channel_name="telegram_ipo",
    )
    fno_bot = TelegramProvider(
        settings,
        bot_token=settings.fno_telegram_bot_token,
        chat_id=settings.fno_telegram_chat_id,
        channel_name="telegram_fno",
    )

    assert fno_bot.configured is False
    assert ipo_bot.configured is True

    result = await fno_bot.send_message(recipient=fno_bot.default_recipient, text="test")
    assert result.success is False
    assert result.retryable is False


async def test_ipo_and_fno_alerts_for_the_same_symbol_are_deduplicated_independently(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Reuses the existing fingerprint/dedup system unchanged — routing
    destination is not part of the fingerprint, so two alerts for the
    same symbol only collide if their existing fingerprint fields
    (symbol/scanner/signal_type/level/date) actually collide."""
    from app.alerts.deduplicator import AlertDeduplicator
    from app.decision.models import Decision, DecisionResult, Quality

    ipo_decision = DecisionResult(
        symbol="NEWCO",
        scanner_name="ipo_intraday_v1",
        signal_type="IPO_INTRADAY",
        decision=Decision.ALERT,
        score=80.0,
        quality=Quality.HIGH,
        feature_snapshot={"breakout_level": "310"},
    )
    fno_decision = DecisionResult(
        symbol="NEWCO",
        scanner_name="fno_momentum_v1",
        signal_type="FNO_MOMENTUM",
        decision=Decision.ALERT,
        score=80.0,
        quality=Quality.HIGH,
        feature_snapshot={"breakout_level": "310"},
    )

    ipo_fingerprint = AlertDeduplicator.build_fingerprint(ipo_decision)
    fno_fingerprint = AlertDeduplicator.build_fingerprint(fno_decision)

    # Different scanner_name -> different fingerprint even for the same
    # symbol/level/date -> both can alert independently, each deduplicated
    # only against its own prior alerts.
    assert ipo_fingerprint != fno_fingerprint
