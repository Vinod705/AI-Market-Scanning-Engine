"""NotificationManager: the queue consumer that actually sends alerts.

Runs as a long-lived background task (`run_forever`), pulling alert ids off
the `AlertQueue` one at a time and sending them through the configured
`NotificationProvider`. Retries transient failures with exponential
backoff up to `Settings.alert_max_retries`; a permanent failure (or
exhausted retries) is recorded as FAILED and never crashes the worker loop
— one bad message must never take the whole pipeline down.
"""

import asyncio

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.formatter import AlertMessageContext, AlertMessageFormatter
from app.alerts.queue import AlertQueue
from app.config.settings import Settings
from app.notifications.base import NotificationProvider
from app.repositories.alert_repository import (
    AlertDeliveryLogRepository,
    AlertEventRepository,
    AlertRepository,
)
from app.repositories.market_repository import SymbolRepository


class NotificationManager:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        queue: AlertQueue,
        provider: NotificationProvider,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._queue = queue
        self._provider = provider
        self._stopping = False

    async def run_forever(self) -> None:
        logger.info("Notification worker started ({provider})", provider=self._provider.name)
        while not self._stopping:
            queued = await self._queue.get()
            try:
                await self.deliver_now(queued.alert_id)
            except Exception:  # noqa: BLE001 - a bad message must never kill the worker loop
                logger.exception("Unexpected error delivering alert #{id}", id=queued.alert_id)
            finally:
                self._queue.task_done()

    def stop(self) -> None:
        self._stopping = True

    async def recover_pending(self) -> int:
        """Restart recovery: reload alerts still PENDING/RETRYING from
        PostgreSQL and (re)drive their delivery. Alerts already SENT are
        never touched — `deliver_now` checks status first and is a no-op
        for anything already delivered, so this is safe to call every
        startup even when nothing needs recovering."""
        async with self._session_factory() as session:
            pending = await AlertRepository(session).list_by_status(["PENDING", "RETRYING"])

        if pending:
            logger.info("Recovering {count} pending alert(s) after restart", count=len(pending))
        for alert in pending:
            try:
                await self.deliver_now(alert.id)
            except Exception:  # noqa: BLE001 - one bad alert must not block recovering the rest
                logger.exception("Failed to recover alert #{id}", id=alert.id)
        return len(pending)

    async def deliver_now(self, alert_id: int) -> None:
        """Send one alert. Also the restart-recovery entry point: alerts
        reloaded from the DB as PENDING/RETRYING call this directly,
        bypassing the (empty, post-restart) in-memory queue."""
        async with self._session_factory() as session:
            alert = await AlertRepository(session).get_by_id(alert_id)
            if alert is None:
                logger.warning("Alert #{id} not found, skipping delivery", id=alert_id)
                return
            if alert.status == "SENT":
                return  # already delivered — restart-recovery safety net

        attempt = 0
        while attempt < self._settings.alert_max_retries:
            attempt += 1
            text = await self._format(alert_id)
            if text is None:
                return

            logger.info(
                "Sending alert #{id} via {provider} (attempt {attempt}/{max})",
                id=alert_id,
                provider=self._provider.name,
                attempt=attempt,
                max=self._settings.alert_max_retries,
            )
            result = await self._provider.send_message(
                recipient=self._settings.telegram_chat_id, text=text
            )

            async with self._session_factory() as session:
                await AlertDeliveryLogRepository(session).log_attempt(
                    alert_id=alert_id,
                    provider=self._provider.name,
                    status=result.status,
                    attempt_number=attempt,
                    response_metadata=result.response_metadata,
                    error_message=result.error_message,
                )
                alert_repo = AlertRepository(session)
                event_repo = AlertEventRepository(session)
                current = await alert_repo.get_by_id(alert_id)
                assert current is not None

                if result.success:
                    await alert_repo.update_status(current, "SENT")
                    await event_repo.log(
                        alert_id=alert_id,
                        event_type="SENT",
                        event_metadata={"provider_message_id": result.provider_message_id},
                    )
                    await session.commit()
                    logger.info("Alert #{id} delivered", id=alert_id)
                    return

                if not result.retryable or attempt >= self._settings.alert_max_retries:
                    await alert_repo.update_status(current, "FAILED")
                    await event_repo.log(
                        alert_id=alert_id,
                        event_type="FAILED",
                        event_metadata={"error": result.error_message},
                    )
                    await session.commit()
                    logger.warning(
                        "Alert #{id} delivery failed permanently: {error}",
                        id=alert_id,
                        error=result.error_message,
                    )
                    return

                await alert_repo.update_status(current, "RETRYING")
                await event_repo.log(
                    alert_id=alert_id,
                    event_type="RETRYING",
                    event_metadata={"attempt": attempt, "error": result.error_message},
                )
                await session.commit()

            await asyncio.sleep(self._settings.alert_retry_delay_seconds * (2 ** (attempt - 1)))

    # --- internals -----------------------------------------------------

    async def _format(self, alert_id: int) -> str | None:
        async with self._session_factory() as session:
            alert = await AlertRepository(session).get_by_id(alert_id)
            if alert is None:
                return None
            symbol = await SymbolRepository(session).get_by_id(alert.symbol_id)
            symbol_name = symbol.symbol if symbol is not None else str(alert.symbol_id)
            context = AlertMessageContext(
                symbol=symbol_name,
                score=float(alert.score),
                quality=alert.quality,
                breakout_level=(
                    float(alert.breakout_level) if alert.breakout_level is not None else None
                ),
                feature_snapshot=alert.feature_snapshot,
                passed_rules=list(alert.passed_rules),
                timestamp=alert.created_at,
            )

        return AlertMessageFormatter.format_text(context)
