"""Tests for app.notifications.whatsapp.WhatsAppProvider.

Uses httpx.MockTransport to simulate the Graph API — no real network calls,
no live credentials required.
"""

import httpx

from app.config.settings import Settings
from app.notifications.whatsapp import WhatsAppProvider


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = dict(
        whatsapp_access_token="test-token",
        whatsapp_phone_number_id="123456",
        whatsapp_recipient_id="919999999999",
        whatsapp_max_retries=2,
        whatsapp_retry_backoff_seconds=0.01,
        whatsapp_request_timeout=1.0,
    )
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _provider(handler, settings: Settings | None = None) -> WhatsAppProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return WhatsAppProvider(settings or _settings(), client=client)


async def test_send_message_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"messages": [{"id": "wamid.123"}]})

    provider = _provider(handler)
    result = await provider.send_message(recipient="919999999999", text="hello")

    assert result.success is True
    assert result.status == "SENT"
    assert result.provider_message_id == "wamid.123"


async def test_send_message_not_configured() -> None:
    provider = _provider(lambda r: httpx.Response(200), _settings(whatsapp_access_token=""))
    result = await provider.send_message(recipient="919999999999", text="hello")

    assert result.success is False
    assert result.status == "FAILED"
    assert result.retryable is False


async def test_send_message_timeout_retries_then_fails() -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ReadTimeout("timed out", request=request)

    provider = _provider(handler)
    result = await provider.send_message(recipient="919999999999", text="hello")

    assert result.success is False
    assert result.status == "FAILED"
    assert result.retryable is True
    assert len(attempts) == 2  # whatsapp_max_retries=2


async def test_send_message_rate_limited_then_succeeds() -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(
                429, headers={"Retry-After": "1"}, json={"error": {"message": "rate limited"}}
            )
        return httpx.Response(200, json={"messages": [{"id": "wamid.456"}]})

    provider = _provider(handler)
    result = await provider.send_message(recipient="919999999999", text="hello")

    assert result.success is True
    assert len(attempts) == 2


async def test_send_message_permanent_failure_does_not_retry() -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(400, json={"error": {"message": "Invalid recipient phone number"}})

    provider = _provider(handler)
    result = await provider.send_message(recipient="invalid", text="hello")

    assert result.success is False
    assert result.status == "FAILED"
    assert result.retryable is False
    assert "Invalid recipient" in (result.error_message or "")
    assert len(attempts) == 1  # no retry for a permanent 4xx


async def test_send_message_never_exposes_access_token_in_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "test-token" not in str(request.url)
        return httpx.Response(401, json={"error": {"message": "Unauthorized"}})

    provider = _provider(handler)
    result = await provider.send_message(recipient="919999999999", text="hello")

    assert result.response_metadata is not None
    assert "test-token" not in str(result.response_metadata)
    assert "Authorization" not in str(result.response_metadata)


async def test_health_check_when_not_configured() -> None:
    provider = _provider(lambda r: httpx.Response(200), _settings(whatsapp_access_token=""))
    assert await provider.health_check() is False


async def test_health_check_success() -> None:
    provider = _provider(lambda r: httpx.Response(200, json={"id": "123456"}))
    assert await provider.health_check() is True


async def test_health_check_failure() -> None:
    provider = _provider(lambda r: httpx.Response(401))
    assert await provider.health_check() is False
