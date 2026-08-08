"""Tests for app.notifications.telegram.TelegramProvider.

Uses httpx.MockTransport to simulate the Telegram Bot API — no real network
calls, no live credentials required.
"""

import httpx

from app.config.settings import Settings
from app.notifications.telegram import TelegramProvider


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = dict(
        telegram_bot_token="test-token",
        telegram_chat_id="123456789",
        telegram_max_retries=2,
        telegram_retry_backoff_seconds=0.01,
        telegram_connect_timeout=1.0,
        telegram_request_timeout=1.0,
    )
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _provider(handler, settings: Settings | None = None) -> TelegramProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return TelegramProvider(settings or _settings(), client=client)


async def test_send_message_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    provider = _provider(handler)
    result = await provider.send_message(recipient="123456789", text="hello")

    assert result.success is True
    assert result.status == "SENT"
    assert result.provider_message_id == "42"


async def test_send_message_not_configured() -> None:
    provider = _provider(lambda r: httpx.Response(200), _settings(telegram_bot_token=""))
    result = await provider.send_message(recipient="123456789", text="hello")

    assert result.success is False
    assert result.status == "FAILED"
    assert result.retryable is False


async def test_send_message_timeout_retries_then_fails() -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ReadTimeout("timed out", request=request)

    provider = _provider(handler)
    result = await provider.send_message(recipient="123456789", text="hello")

    assert result.success is False
    assert result.status == "FAILED"
    assert result.retryable is True
    assert len(attempts) == 2  # telegram_max_retries=2


async def test_send_message_rate_limited_then_succeeds() -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(
                429,
                json={
                    "ok": False,
                    "error_code": 429,
                    "description": "Too Many Requests: retry after 1",
                    "parameters": {"retry_after": 1},
                },
            )
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 99}})

    provider = _provider(handler)
    result = await provider.send_message(recipient="123456789", text="hello")

    assert result.success is True
    assert len(attempts) == 2


async def test_send_message_permanent_failure_does_not_retry() -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(
            400, json={"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}
        )

    provider = _provider(handler)
    result = await provider.send_message(recipient="invalid", text="hello")

    assert result.success is False
    assert result.status == "FAILED"
    assert result.retryable is False
    assert "chat not found" in (result.error_message or "")
    assert len(attempts) == 1  # no retry for a permanent 4xx


async def test_send_message_server_error_retries() -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(
                500, json={"ok": False, "error_code": 500, "description": "Internal error"}
            )
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    provider = _provider(handler)
    result = await provider.send_message(recipient="123456789", text="hello")

    assert result.success is True
    assert len(attempts) == 2


async def test_send_message_never_exposes_bot_token_in_persisted_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"ok": False, "error_code": 401, "description": "Unauthorized"}
        )

    provider = _provider(handler)
    result = await provider.send_message(recipient="123456789", text="hello")

    assert result.response_metadata is not None
    assert "test-token" not in str(result.response_metadata)
    assert "test-token" not in str(result.error_message)


async def test_health_check_when_not_configured() -> None:
    provider = _provider(lambda r: httpx.Response(200), _settings(telegram_bot_token=""))
    assert await provider.health_check() is False


async def test_health_check_success() -> None:
    provider = _provider(
        lambda r: httpx.Response(200, json={"ok": True, "result": {"id": 1, "username": "bot"}})
    )
    assert await provider.health_check() is True


async def test_health_check_failure() -> None:
    provider = _provider(lambda r: httpx.Response(401, json={"ok": False}))
    assert await provider.health_check() is False


# --- Multiple independent bot instances (IPO/F&O routing) ---


def test_default_channel_name_and_credentials() -> None:
    provider = TelegramProvider(_settings())
    assert provider.name == "telegram"
    assert provider.default_recipient == "123456789"
    assert provider.configured is True


def test_overridden_bot_token_and_chat_id_are_independent_of_default() -> None:
    provider = TelegramProvider(
        _settings(),
        bot_token="ipo-bot-token",
        chat_id="987654321",
        channel_name="telegram_ipo",
    )
    assert provider.name == "telegram_ipo"
    assert provider.default_recipient == "987654321"
    assert provider._base_url == "https://api.telegram.org/botipo-bot-token"


def test_unconfigured_ipo_bot_does_not_affect_default_bot() -> None:
    """The default bot has real credentials; a second instance built with
    empty overrides for the IPO bot must independently report unconfigured
    — one bot's credentials never leak into another's."""
    settings = _settings()  # default bot IS configured
    default_provider = TelegramProvider(settings, channel_name="telegram")
    ipo_provider = TelegramProvider(settings, bot_token="", chat_id="", channel_name="telegram_ipo")

    assert default_provider.configured is True
    assert ipo_provider.configured is False


async def test_unconfigured_overridden_bot_fails_without_retry() -> None:
    provider = TelegramProvider(
        _settings(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
        bot_token="",
        chat_id="",
        channel_name="telegram_fno",
    )
    result = await provider.send_message(recipient="anything", text="hello")
    assert result.success is False
    assert result.retryable is False
    assert "telegram_fno" in (result.error_message or "")
