"""Telegram Bot API notification provider.

Uses the official Telegram Bot API (`api.telegram.org/bot{token}/...`) over
httpx — no browser automation, no unofficial libraries. Every send goes
through `_send_with_retry`, which centralizes timeout/retry/backoff/
rate-limit handling so `send_message`/`send_template` stay simple.

One `TelegramProvider` instance is one bot. By default it reads the
"default" bot's credentials (`Settings.telegram_bot_token`/`telegram_chat_id`)
— but `bot_token`/`chat_id`/`channel_name` can be overridden at
construction to stand up a second (or third) independent bot instance
without any new class, which is how the IPO and F&O bots are built in
`app/main.py` and routed to by `app/notifications/router.py`.

Credentials are never logged or included in `DeliveryResult.response_metadata`
— the bot token lives only in the request URL, which is never captured in
any log line or persisted field.
"""

import asyncio
from typing import Any

import httpx
from loguru import logger

from app.config.settings import Settings
from app.notifications.base import DeliveryResult, NotificationProvider

_API_BASE = "https://api.telegram.org"

# Telegram error_codes that mean "this request will never succeed as-is" —
# retrying wastes an attempt. 429 (rate limit) and 5xx are handled
# separately as transient.
_PERMANENT_ERROR_CODES = {400, 401, 403, 404}


class TelegramProvider(NotificationProvider):
    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        *,
        bot_token: str | None = None,
        chat_id: str | None = None,
        channel_name: str = "telegram",
    ) -> None:
        self._settings = settings
        self._bot_token = settings.telegram_bot_token if bot_token is None else bot_token
        self._chat_id = settings.telegram_chat_id if chat_id is None else chat_id
        self.name = channel_name
        # Reused across calls rather than opened per-request; also the seam
        # tests use to inject an `httpx.MockTransport` instead of hitting
        # the real Bot API. Connect and request timeouts are configured
        # separately, per the Phase 5 spec's explicit requirement.
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=settings.telegram_connect_timeout,
                read=settings.telegram_request_timeout,
                write=settings.telegram_request_timeout,
                pool=settings.telegram_request_timeout,
            )
        )

    @property
    def configured(self) -> bool:
        """Whether this specific bot has both a token and a chat id — each
        `TelegramProvider` instance is independently configured, so the
        default bot being set up says nothing about the IPO/F&O bots."""
        return bool(self._bot_token and self._chat_id)

    @property
    def default_recipient(self) -> str:
        return self._chat_id

    @property
    def _base_url(self) -> str:
        return f"{_API_BASE}/bot{self._bot_token}"

    async def send_message(self, *, recipient: str, text: str) -> DeliveryResult:
        payload = {"chat_id": recipient, "text": text}
        return await self._send_with_retry(payload)

    async def send_template(
        self, *, recipient: str, template_name: str, language: str, parameters: list[str]
    ) -> DeliveryResult:
        """Telegram has no server-side approved-template concept (unlike
        WhatsApp) — a "template" send is just a formatted text message."""
        text = f"{template_name}: " + ", ".join(parameters) if parameters else template_name
        return await self.send_message(recipient=recipient, text=text)

    async def health_check(self) -> bool:
        if not self.configured:
            return False
        try:
            response = await self._client.get(
                f"{self._base_url}/getMe",
                timeout=self._settings.telegram_health_check_timeout_seconds,
            )
            return response.status_code == 200 and response.json().get("ok") is True
        except httpx.HTTPError:
            return False

    # --- internals -----------------------------------------------------

    async def _send_with_retry(self, payload: dict[str, Any]) -> DeliveryResult:
        if not self.configured:
            return DeliveryResult(
                success=False,
                status="FAILED",
                error_message=f"Telegram ({self.name}) is not configured",
                retryable=False,
            )

        last_error: str | None = None
        for attempt in range(1, self._settings.telegram_max_retries + 1):
            try:
                response = await self._client.post(f"{self._base_url}/sendMessage", json=payload)
            except httpx.TimeoutException:
                last_error = "request timed out"
                logger.warning(
                    "Telegram send timed out (attempt {attempt}/{max})",
                    attempt=attempt,
                    max=self._settings.telegram_max_retries,
                )
            except httpx.HTTPError as exc:
                last_error = str(exc)
                logger.warning(
                    "Telegram send failed (attempt {attempt}/{max}): {error}",
                    attempt=attempt,
                    max=self._settings.telegram_max_retries,
                    error=exc,
                )
            else:
                result = self._handle_response(response, attempt)
                if result is not None:
                    return result
                last_error = f"HTTP {response.status_code}"

            if attempt < self._settings.telegram_max_retries:
                await asyncio.sleep(self._settings.telegram_retry_backoff_seconds * attempt)

        return DeliveryResult(
            success=False, status="FAILED", error_message=last_error, retryable=True
        )

    def _handle_response(self, response: httpx.Response, attempt: int) -> DeliveryResult | None:
        """Returns a final `DeliveryResult` for success or a permanent
        failure, or `None` to signal "retry" for a transient failure."""
        try:
            body = response.json()
        except ValueError:
            body = {}

        if response.status_code == 200 and body.get("ok") is True:
            result = body.get("result") or {}
            message_id = result.get("message_id")
            return DeliveryResult(
                success=True,
                status="SENT",
                provider_message_id=str(message_id) if message_id is not None else None,
                response_metadata={"status_code": response.status_code},
            )

        if response.status_code == 429:
            retry_after = (body.get("parameters") or {}).get("retry_after")
            logger.warning(
                "Telegram rate limited (attempt {attempt}/{max}), retry_after={retry_after}",
                attempt=attempt,
                max=self._settings.telegram_max_retries,
                retry_after=retry_after,
            )
            return None

        if 500 <= response.status_code < 600:
            logger.warning(
                "Telegram server error (attempt {attempt}/{max}): {status}",
                attempt=attempt,
                max=self._settings.telegram_max_retries,
                status=response.status_code,
            )
            return None

        if response.status_code in _PERMANENT_ERROR_CODES:
            return DeliveryResult(
                success=False,
                status="FAILED",
                error_message=body.get("description") or f"HTTP {response.status_code}",
                response_metadata={"status_code": response.status_code},
                retryable=False,
            )

        # Any other unexpected status: treat as transient and let the retry
        # loop decide (bounded by telegram_max_retries either way).
        logger.warning(
            "Telegram unexpected status (attempt {attempt}/{max}): {status}",
            attempt=attempt,
            max=self._settings.telegram_max_retries,
            status=response.status_code,
        )
        return None
