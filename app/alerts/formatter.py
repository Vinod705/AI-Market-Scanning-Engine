"""AlertMessageFormatter: builds the WhatsApp notification text.

Kept separate from `app/notifications/whatsapp.py` on purpose — formatting
is provider-agnostic (a future SMS/email provider would reuse it), and the
WhatsApp Business API's template-message rules mean *how* this text gets
delivered (free-form session message vs. an approved template) is a
provider concern, not a formatting one.

Only ever renders values that are actually present on the alert. Never
invents entry/stop-loss/target prices, and never claims the score is a
probability of anything.
"""

from dataclasses import dataclass
from datetime import datetime

from app.decision.validator import DecisionValidator

_WHY_BULLETS = {
    "resistance_proximity": "Price approaching resistance",
    "relative_volume": "Strong relative volume",
    "trend": "Trend aligned",
    "adx": "Momentum confirmed",
}


@dataclass
class AlertMessageContext:
    """Everything the formatter needs, extracted ahead of time so it stays
    a pure function of plain data rather than depending on the ORM or the
    decision engine's own types."""

    symbol: str
    score: float
    quality: str
    breakout_level: float | None
    feature_snapshot: dict[str, object]
    passed_rules: list[str]
    timestamp: datetime


class AlertMessageFormatter:
    @staticmethod
    def format_text(context: AlertMessageContext) -> str:
        snapshot = context.feature_snapshot
        price = DecisionValidator.as_float(snapshot, "price")
        ema20 = DecisionValidator.as_float(snapshot, "ema20")
        ema50 = DecisionValidator.as_float(snapshot, "ema50")
        ema200 = DecisionValidator.as_float(snapshot, "ema200")
        rvol = DecisionValidator.as_float(snapshot, "relative_volume")
        adx = DecisionValidator.as_float(snapshot, "adx14")

        why = [_WHY_BULLETS[rule] for rule in context.passed_rules if rule in _WHY_BULLETS]

        lines = ["\U0001f6a8 BREAKOUT CANDIDATE", "", f"Symbol: {context.symbol}", ""]

        if price is not None:
            lines += [f"Price: ₹{price:.2f}", ""]
        if context.breakout_level is not None:
            lines += [f"Breakout Level: ₹{context.breakout_level:.2f}", ""]

        lines += [f"Score: {context.score:.0f}/100", "", f"Quality: {context.quality}", ""]

        if ema20 is not None and ema50 is not None and ema200 is not None:
            lines += ["Trend:", "EMA20 > EMA50 > EMA200", ""]
        if rvol is not None:
            lines += ["RVOL:", f"{rvol:.1f}x", ""]
        if adx is not None:
            lines += ["ADX:", f"{adx:.0f}", ""]

        if why:
            lines += ["Why:"] + [f"• {reason}" for reason in why] + [""]

        lines += ["Time:", context.timestamp.strftime("%H:%M IST"), ""]
        lines += ["⚠️ Scanner signal — not an automatic trade."]

        return "\n".join(lines)
