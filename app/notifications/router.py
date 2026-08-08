"""NotificationRouter: deterministic Alert -> NotificationProvider selection.

Reuses the existing `NotificationProvider` abstraction as-is — this class
only decides WHICH already-constructed provider instance handles a given
alert; it never talks to Telegram (or any provider) directly, and nothing
about it is Telegram-specific.

Two dedicated Telegram bots exist for IPO and F&O alerts (see
`Settings.ipo_telegram_*`/`fno_telegram_*`, constructed as separate
`TelegramProvider` instances in `app/main.py`). `breakout_v1` alerts
predate the IPO/F&O split — they have no `alert_category` in their
`feature_snapshot` — and keep going through the original single "default"
bot unchanged.

Routing is deterministic and closed: an IPO alert is only ever handed to
the IPO provider, an F&O alert only ever to the F&O provider — even if
that provider isn't configured or is currently failing. There is no
fallback to a different bot; an alert whose provider can't deliver stays
PENDING/RETRYING like any other delivery failure (see
`app.notifications.manager.NotificationManager`), it is never silently
rerouted to a different channel.
"""

from app.candidates.models import AlertCategory
from app.notifications.base import NotificationProvider

_IPO_ALERT_CATEGORIES = {
    AlertCategory.IPO_PRE_BREAKOUT.value,
    AlertCategory.IPO_BREAKOUT.value,
    AlertCategory.IPO_MOMENTUM.value,
}
_FNO_ALERT_CATEGORIES = {
    AlertCategory.FNO_PRE_BREAKOUT.value,
    AlertCategory.FNO_BREAKOUT.value,
    AlertCategory.FNO_MOMENTUM.value,
}


class NotificationRouter:
    def __init__(
        self,
        *,
        default: NotificationProvider,
        ipo: NotificationProvider,
        fno: NotificationProvider,
    ) -> None:
        self._default = default
        self._ipo = ipo
        self._fno = fno

    def resolve(self, alert_category: str | None) -> NotificationProvider:
        """`alert_category` is `Alert.feature_snapshot.get("alert_category")`
        — one of the six `AlertCategory` values for a candidate-scanner
        alert, or `None`/anything else for `breakout_v1`."""
        if alert_category in _IPO_ALERT_CATEGORIES:
            return self._ipo
        if alert_category in _FNO_ALERT_CATEGORIES:
            return self._fno
        return self._default
