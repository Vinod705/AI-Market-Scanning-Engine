"""NotificationProvider: the abstraction the alert layer depends on instead
of any specific channel.

`AlertManager`/`NotificationManager` only ever talk to this interface —
`WhatsAppProvider` is the first implementation, but a future SMS/email/
Telegram provider is a new class here, not a change to the decision or
alert engine.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class DeliveryResult:
    """What a provider hands back after one send attempt. `response_metadata`
    is whatever's safe to persist (message id, provider status) — never
    tokens/secrets."""

    success: bool
    status: str  # "SENT" | "FAILED" | "RETRYING"
    provider_message_id: str | None = None
    error_message: str | None = None
    response_metadata: dict[str, object] = field(default_factory=dict)
    retryable: bool = True


class NotificationProvider(ABC):
    """Strategy interface for one notification channel."""

    name: str

    @abstractmethod
    async def send_message(self, *, recipient: str, text: str) -> DeliveryResult:
        """Send a free-form text message."""
        raise NotImplementedError

    @abstractmethod
    async def send_template(
        self, *, recipient: str, template_name: str, language: str, parameters: list[str]
    ) -> DeliveryResult:
        """Send a pre-approved template message — required by some channels
        (e.g. WhatsApp, outside a 24h customer-service window)."""
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        """Whether the provider is currently reachable/authenticated."""
        raise NotImplementedError
