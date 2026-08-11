"""AlertMessageFormatter: builds the notification text.

Kept separate from `app/notifications/telegram.py` on purpose — formatting
is provider-agnostic (any future provider — Telegram today, SMS/email
tomorrow — would reuse it), and *how* the text gets delivered (a plain
message vs. an approved template) is a provider concern, not a formatting one.

Only ever renders values that are actually present on the alert. Never
invents entry/stop-loss/target prices, and never claims the score is a
probability of anything.
"""

from dataclasses import dataclass
from datetime import datetime

from app.compounding.engine import evaluate_compounding
from app.config.settings import Settings, get_settings
from app.decision.validator import DecisionValidator

_WHY_BULLETS = {
    "resistance_proximity": "Price approaching resistance",
    "relative_volume": "Strong relative volume",
    "trend": "Trend aligned",
    "adx": "Momentum confirmed",
}

# scanner_name -> True routes to the original breakout_v1 message shape
# (kept byte-for-byte unchanged — it's already been live-verified end to
# end). Any scanner not in this set renders through
# `_format_candidate_text` instead, which understands the richer
# fundamental/technical/setup-state fields `StockCandidate` adds — see
# `app/candidates/models.py`.
_BREAKOUT_STYLE_SCANNERS = {"breakout_v1"}

_ALERT_CATEGORY_LABELS = {
    "IPO_PRE_BREAKOUT": "IPO PRE-BREAKOUT",
    "IPO_BREAKOUT": "IPO BREAKOUT",
    "IPO_MOMENTUM": "IPO MOMENTUM",
    "FNO_PRE_BREAKOUT": "F&O PRE-BREAKOUT",
    "FNO_BREAKOUT": "F&O BREAKOUT",
    "FNO_MOMENTUM": "F&O MOMENTUM",
}


@dataclass
class AlertMessageContext:
    """Everything the formatter needs, extracted ahead of time so it stays
    a pure function of plain data rather than depending on the ORM or the
    decision engine's own types."""

    symbol: str
    scanner_name: str
    score: float
    quality: str
    breakout_level: float | None
    feature_snapshot: dict[str, object]
    passed_rules: list[str]
    timestamp: datetime


class AlertMessageFormatter:
    @staticmethod
    def format_text(context: AlertMessageContext, settings: Settings | None = None) -> str:
        if context.scanner_name in _BREAKOUT_STYLE_SCANNERS:
            return _format_breakout_text(context)
        return _format_candidate_text(context, settings or get_settings())


def _format_breakout_text(context: AlertMessageContext) -> str:
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


def _format_candidate_text(context: AlertMessageContext, settings: Settings) -> str:
    """Renders the unified IPO/F&O candidate alert shape — everything
    here comes from `StockCandidate.to_feature_snapshot()` (see
    `app/candidates/models.py`), so a field simply doesn't appear in the
    message when the underlying data wasn't available. Never fabricates
    a fundamental score, an entry/stop/target price, or implies the score
    is a probability."""
    snapshot = context.feature_snapshot
    price = DecisionValidator.as_float(snapshot, "price")
    support_level = DecisionValidator.as_float(snapshot, "support_level")
    resistance_level = DecisionValidator.as_float(snapshot, "resistance_level")

    universe = snapshot.get("universe")
    setup_state = snapshot.get("setup_state")
    alert_category = snapshot.get("alert_category")
    fundamental_score = DecisionValidator.as_float(snapshot, "fundamental_score")
    technical_score = DecisionValidator.as_float(snapshot, "technical_score")
    overall_score = DecisionValidator.as_float(snapshot, "overall_score") or context.score
    data_completeness = DecisionValidator.as_float(snapshot, "data_completeness_pct")
    fundamental_reasons_raw = snapshot.get("fundamental_reasons")
    technical_reasons_raw = snapshot.get("technical_reasons")
    fundamental_reasons = (
        fundamental_reasons_raw if isinstance(fundamental_reasons_raw, list) else []
    )
    technical_reasons = technical_reasons_raw if isinstance(technical_reasons_raw, list) else []
    risk_flags = snapshot.get("risk_flags")

    scanner_sources_raw = snapshot.get("scanner_sources")
    scanner_sources = (
        [str(s) for s in scanner_sources_raw]
        if isinstance(scanner_sources_raw, list)
        else ["5PAISA"]
    )

    header = _ALERT_CATEGORY_LABELS.get(str(alert_category), "CANDIDATE")
    lines = [f"\U0001f6a8 {header}", "", f"Symbol: {context.symbol}"]
    if universe is not None:
        lines += [f"Universe: {universe}"]
    lines += [f"Scanner Sources: {', '.join(scanner_sources)}", ""]

    if price is not None:
        lines += [f"Price: ₹{price:.2f}", ""]
    if context.breakout_level is not None:
        lines += [f"Breakout Level: ₹{context.breakout_level:.2f}", ""]
    if resistance_level is not None:
        lines += [f"Resistance: ₹{resistance_level:.2f}", ""]
    if support_level is not None:
        lines += [f"Support: ₹{support_level:.2f}", ""]

    if setup_state is not None:
        lines += [f"Setup: {setup_state}", ""]

    if fundamental_score is None:
        lines += ["Fundamental Score: UNKNOWN (no data source)", ""]
    elif data_completeness is not None and data_completeness < 100:
        lines += [
            f"Fundamental Score: {fundamental_score:.0f}/100 (LIMITED — {data_completeness:.0f}% data)",
            "",
        ]
    else:
        lines += [f"Fundamental Score: {fundamental_score:.0f}/100", ""]

    if fundamental_score is not None:
        field_sources_raw = snapshot.get("fundamental_field_sources")
        sources = sorted(
            {
                str(f.get("source"))
                for f in field_sources_raw
                if isinstance(f, dict) and f.get("source")
            }
            if isinstance(field_sources_raw, list)
            else set()
        )
        if sources:
            lines += [f"Fundamental Data Source(s): {', '.join(sources)}", ""]

    if technical_score is not None:
        lines += [f"Technical Score: {technical_score:.0f}/100", ""]

    lines += [f"Overall Score: {overall_score:.0f}/100", "", f"Quality: {context.quality}", ""]

    why = [str(r) for r in technical_reasons][:3] + [str(r) for r in fundamental_reasons][:3]
    if why:
        lines += ["Why:"] + [f"• {reason}" for reason in why] + [""]

    if isinstance(risk_flags, list) and risk_flags:
        lines += ["Risk Flags:"] + [f"⚠ {flag}" for flag in risk_flags] + [""]

    lines += _compounding_lines(snapshot, settings)

    lines += ["Time:", context.timestamp.strftime("%H:%M IST"), ""]
    lines += ["⚠️ Scanner signal — not an automatic trade. Score is not a probability."]

    return "\n".join(lines)


def _compounding_lines(snapshot: dict[str, object], settings: Settings) -> list[str]:
    """Compact Compounding/Opportunity summary (see `app.compounding.engine`)
    — kept to a few lines so alerts don't balloon; the full breakdown is
    on the dashboard's explain view."""
    result = evaluate_compounding(snapshot, settings)
    if result.potential_upside_pct is None or result.target_price is None:
        return ["Compounding: UNKNOWN (insufficient target data)", ""]

    lines = [
        f"Compounding: {result.potential_upside_pct:.1f}% upside → "
        f"₹{result.target_price:.2f} ({result.target_classification.value})"
    ]
    if result.stop_loss is not None and result.reward_risk_ratio is not None:
        lines.append(f"Stop: ₹{result.stop_loss:.2f}  R:R {result.reward_risk_ratio:.2f}")
    elif result.stop_loss is not None:
        lines.append(f"Stop: ₹{result.stop_loss:.2f}")
    lines.append(
        f"Timeframe: {result.timeframe.value} · HTF: {result.higher_timeframe_confirmation.value} "
        f"· Opportunity: {result.decision.value} ({result.compounding_score:.0f})"
    )
    lines.append("")
    return lines
