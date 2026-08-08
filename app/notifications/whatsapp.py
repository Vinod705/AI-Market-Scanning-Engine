"""WhatsApp Business Cloud API notification provider.

Uses the official Meta Graph API (`graph.facebook.com/{version}/{phone_number_id}/messages`)
over httpx — no browser automation, no unofficial libraries, per the Phase 5
spec's explicit requirement. Every send goes through `_send_with_retry`,
which centralizes timeout/retry/backoff/rate-limit handling so
`send_message`/`send_template` stay simple.

Credentials come from `Settings` (`WHATSAPP_*` env vars) and are never
logged or included in `DeliveryResult.response_metadata`.
"""

import asyncio
from typing import Any

import httpx
from loguru import logger

from app.config.settings import Settings
from app.notifications.base import DeliveryResult, NotificationProvider

_GRAPH_API_BASE = "https://graph.facebook.com"


class WhatsAppProvider(NotificationProvider):
    name = "whatsapp"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        # Reused across calls rather than opened per-request; also the seam
        # tests use to inject an `httpx.MockTransport` instead of hitting
        # the real Graph API.
        self._client = client or httpx.AsyncClient(timeout=settings.whatsapp_request_timeout)

    @property
    def _base_url(self) -> str:
        return f"{_GRAPH_API_BASE}/{self._settings.whatsapp_api_version}/{self._settings.whatsapp_phone_number_id}"

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._settings.whatsapp_access_token}",
            "Content-Type": "application/json",
        }

    async def send_message(self, *, recipient: str, text: str) -> DeliveryResult:
        payload = {
            "messaging_product": "whatsapp",
            "to": recipient,
            "type": "text",
            "text": {"body": text},
        }
        return await self._send_with_retry(payload)

    async def send_template(
        self, *, recipient: str, template_name: str, language: str, parameters: list[str]
    ) -> DeliveryResult:
        payload = {
            "messaging_product": "whatsapp",
            "to": recipient,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language},
                "components": (
                    [
                        {
                            "type": "body",
                            "parameters": [{"type": "text", "text": p} for p in parameters],
                        }
                    ]
                    if parameters
                    else []
                ),
            },
        }
        return await self._send_with_retry(payload)

    async def health_check(self) -> bool:
        if not self._settings.whatsapp_configured:
            return False
        try:
            response = await self._client.get(self._base_url, headers=self._headers)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    # --- internals -----------------------------------------------------

    async def _send_with_retry(self, payload: dict[str, Any]) -> DeliveryResult:
        if not self._settings.whatsapp_configured:
            return DeliveryResult(
                success=False,
                status="FAILED",
                error_message="WhatsApp is not configured",
                retryable=False,
            )

        last_error: str | None = None
        for attempt in range(1, self._settings.whatsapp_max_retries + 1):
            try:
                response = await self._client.post(
                    f"{self._base_url}/messages", headers=self._headers, json=payload
                )
            except httpx.TimeoutException:
                last_error = "request timed out"
                logger.warning(
                    "WhatsApp send timed out (attempt {attempt}/{max})",
                    attempt=attempt,
                    max=self._settings.whatsapp_max_retries,
                )
            except httpx.HTTPError as exc:
                last_error = str(exc)
                logger.warning(
                    "WhatsApp send failed (attempt {attempt}/{max}): {error}",
                    attempt=attempt,
                    max=self._settings.whatsapp_max_retries,
                    error=exc,
                )
            else:
                result = self._handle_response(response, attempt)
                if result is not None:
                    return result
                last_error = f"HTTP {response.status_code}"

            if attempt < self._settings.whatsapp_max_retries:
                await asyncio.sleep(self._settings.whatsapp_retry_backoff_seconds * attempt)

        return DeliveryResult(
            success=False, status="FAILED", error_message=last_error, retryable=True
        )

    def _handle_response(self, response: httpx.Response, attempt: int) -> DeliveryResult | None:
        """Returns a final `DeliveryResult` for success or a permanent
        failure, or `None` to signal "retry" for a transient failure."""
        if response.status_code == 200:
            body = response.json()
            message_id = None
            messages = body.get("messages")
            if isinstance(messages, list) and messages:
                message_id = messages[0].get("id")
            return DeliveryResult(
                success=True,
                status="SENT",
                provider_message_id=message_id,
                response_metadata={"status_code": response.status_code},
            )

        if response.status_code == 429:
            logger.warning(
                "WhatsApp rate limited (attempt {attempt}/{max}), retry-after={retry_after}",
                attempt=attempt,
                max=self._settings.whatsapp_max_retries,
                retry_after=response.headers.get("Retry-After"),
            )
            return None

        if 500 <= response.status_code < 600:
            logger.warning(
                "WhatsApp server error (attempt {attempt}/{max}): {status}",
                attempt=attempt,
                max=self._settings.whatsapp_max_retries,
                status=response.status_code,
            )
            return None

        # Any other 4xx is a permanent failure — retrying won't help.
        return DeliveryResult(
            success=False,
            status="FAILED",
            error_message=_safe_error_message(response),
            response_metadata={"status_code": response.status_code},
            retryable=False,
        )


def _safe_error_message(response: httpx.Response) -> str:
    """Extract a WhatsApp API error message — never includes request
    headers, which is where the access token lives."""
    try:
        body = response.json()
        error = body.get("error", {})
        return str(error.get("message") or body)
    except ValueError:
        return f"HTTP {response.status_code}"
