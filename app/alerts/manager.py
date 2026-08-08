"""AlertManager: Decision Engine verdict -> persisted Alert -> AlertQueue.

Owns the "no duplicate alert" and "cooldown" guarantees: every ALERT-grade
decision passes through dedup and cooldown checks here before a row is
ever created. Never talks to the notification provider directly —
`process()` only ever awaits a fast in-memory queue `put`, so a
slow/unavailable provider can never block this (or the scanner/decision
engine upstream of it).
"""

from datetime import datetime, timedelta
from decimal import Decimal

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.deduplicator import AlertDeduplicator
from app.alerts.queue import AlertQueue
from app.alerts.throttler import AlertThrottler
from app.config.settings import Settings
from app.decision.models import Decision, DecisionResult
from app.decision.validator import DecisionValidator
from app.repositories.alert_repository import AlertEventRepository, AlertRepository


class AlertManager:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        queue: AlertQueue,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._queue = queue

    async def process(
        self, decision: DecisionResult, *, symbol_id: int, now: datetime | None = None
    ) -> int | None:
        """Returns the created alert's id, or None if the candidate wasn't
        alert-worthy or was suppressed (duplicate/cooldown)."""
        if decision.decision != Decision.ALERT:
            return None
        assert decision.quality is not None  # evaluator always sets quality for ALERT

        moment = now or datetime.now()
        fingerprint = AlertDeduplicator.build_fingerprint(decision)

        async with self._session_factory() as session:
            alert_repo = AlertRepository(session)
            event_repo = AlertEventRepository(session)

            dedup_result = await AlertDeduplicator(alert_repo).check(fingerprint)
            if dedup_result.suppressed:
                logger.info(
                    "Duplicate suppressed for {symbol}: {reason}",
                    symbol=decision.symbol,
                    reason=dedup_result.reason,
                )
                if dedup_result.blocking_alert_id is not None:
                    await event_repo.log(
                        alert_id=dedup_result.blocking_alert_id,
                        event_type="SUPPRESSED",
                        event_metadata={"cause": "duplicate", "reason": dedup_result.reason},
                    )
                    await session.commit()
                return None

            cooldown_result = await AlertThrottler(alert_repo, self._settings).check(
                symbol_id, decision.signal_type, now=moment
            )
            if cooldown_result.suppressed:
                logger.info(
                    "Cooldown suppressed for {symbol}: {reason}",
                    symbol=decision.symbol,
                    reason=cooldown_result.reason,
                )
                if cooldown_result.blocking_alert_id is not None:
                    await event_repo.log(
                        alert_id=cooldown_result.blocking_alert_id,
                        event_type="SUPPRESSED",
                        event_metadata={"cause": "cooldown", "reason": cooldown_result.reason},
                    )
                    await session.commit()
                return None

            snapshot = decision.feature_snapshot
            entry_reference = DecisionValidator.as_float(snapshot, "price")
            breakout_level = DecisionValidator.as_float(snapshot, "breakout_level")
            if breakout_level is None:
                breakout_level = DecisionValidator.as_float(snapshot, "resistance_level")
            support_level = DecisionValidator.as_float(snapshot, "support_level")
            resistance_level = DecisionValidator.as_float(snapshot, "resistance_level")

            alert = await alert_repo.create(
                symbol_id=symbol_id,
                scanner_name=decision.scanner_name,
                signal_type=decision.signal_type,
                decision=decision.decision.value,
                score=Decimal(str(round(decision.score, 2))),
                quality=decision.quality.value,
                entry_reference=(
                    Decimal(str(entry_reference)) if entry_reference is not None else None
                ),
                breakout_level=Decimal(str(breakout_level)) if breakout_level is not None else None,
                support_level=Decimal(str(support_level)) if support_level is not None else None,
                resistance_level=(
                    Decimal(str(resistance_level)) if resistance_level is not None else None
                ),
                feature_snapshot=snapshot,
                reason="all conditions met: " + ", ".join(decision.passed_rules),
                passed_rules=decision.passed_rules,
                fingerprint=fingerprint,
                signal_date=moment.date(),
                expires_at=moment + timedelta(minutes=self._settings.alert_expiry_minutes),
            )
            await event_repo.log(alert_id=alert.id, event_type="CREATED")
            await session.commit()
            alert_id = alert.id

        await self._queue.put(alert_id)

        async with self._session_factory() as session:
            await AlertEventRepository(session).log(alert_id=alert_id, event_type="QUEUED")
            await session.commit()

        logger.info(
            "Alert #{id} created and queued for {symbol}", id=alert_id, symbol=decision.symbol
        )
        return alert_id
