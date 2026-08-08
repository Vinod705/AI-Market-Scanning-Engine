"""Repository layer for the alert engine: `alerts`, `alert_events`, `alert_delivery_logs`."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date as date_
from datetime import datetime as datetime_
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.models.alert_delivery_log import AlertDeliveryLog
from app.models.alert_event import AlertEvent


@dataclass
class AlertStatusSummary:
    total_alerts: int
    sent_count: int
    pending_count: int
    failed_count: int
    last_alert_at: datetime_ | None


class AlertRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        symbol_id: int,
        scanner_name: str,
        signal_type: str,
        decision: str,
        score: Decimal,
        quality: str,
        entry_reference: Decimal | None,
        breakout_level: Decimal | None,
        support_level: Decimal | None,
        resistance_level: Decimal | None,
        feature_snapshot: Mapping[str, object],
        reason: str,
        passed_rules: list[str],
        fingerprint: str,
        signal_date: date_,
        expires_at: datetime_ | None,
    ) -> Alert:
        alert = Alert(
            symbol_id=symbol_id,
            scanner_name=scanner_name,
            signal_type=signal_type,
            decision=decision,
            score=score,
            quality=quality,
            entry_reference=entry_reference,
            breakout_level=breakout_level,
            support_level=support_level,
            resistance_level=resistance_level,
            feature_snapshot=dict(feature_snapshot),
            reason=reason,
            passed_rules=list(passed_rules),
            status="PENDING",
            fingerprint=fingerprint,
            signal_date=signal_date,
            expires_at=expires_at,
        )
        self._session.add(alert)
        await self._session.flush()
        return alert

    async def get_by_fingerprint(self, fingerprint: str) -> Alert | None:
        """Most recent alert for this fingerprint. Not necessarily unique —
        an EXPIRED alert can be followed by a fresh one with the same
        fingerprint later the same day — so this is "most recent", not "the"
        row."""
        # Order by id, not created_at: server-side timestamps can share the
        # same second under normal DB clock resolution, which would make
        # "most recent" ambiguous right after a rapid expire-then-recreate.
        stmt = (
            select(Alert).where(Alert.fingerprint == fingerprint).order_by(Alert.id.desc()).limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_id(self, alert_id: int) -> Alert | None:
        return await self._session.get(Alert, alert_id)

    async def get_most_recent_for_signal(
        self, symbol_id: int, signal_type: str, *, since: datetime_
    ) -> Alert | None:
        """Most recent alert for (symbol, signal_type) created at or after
        `since` — the cooldown check queries this with `since = now - cooldown`."""
        stmt = (
            select(Alert)
            .where(
                Alert.symbol_id == symbol_id,
                Alert.signal_type == signal_type,
                Alert.created_at >= since,
            )
            .order_by(Alert.id.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_recent(self, *, symbol_id: int | None = None, limit: int = 50) -> list[Alert]:
        stmt = select(Alert)
        if symbol_id is not None:
            stmt = stmt.where(Alert.symbol_id == symbol_id)
        stmt = stmt.order_by(Alert.created_at.desc()).limit(limit)
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_by_status(self, statuses: list[str], *, limit: int = 200) -> list[Alert]:
        """Used for restart recovery: reload alerts still PENDING/RETRYING
        so they can be requeued instead of resending already-delivered ones."""
        stmt = (
            select(Alert)
            .where(Alert.status.in_(statuses))
            .order_by(Alert.created_at.asc())
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def update_status(self, alert: Alert, status: str) -> None:
        alert.status = status
        await self._session.flush()

    async def expire_stale(self, *, now: datetime_) -> list[Alert]:
        """Mark PENDING/RETRYING alerts past their `expires_at` as EXPIRED,
        returning the ones just expired (caller logs an EXPIRED event per alert)."""
        stmt = select(Alert).where(
            Alert.status.in_(["PENDING", "RETRYING"]),
            Alert.expires_at.is_not(None),
            Alert.expires_at <= now,
        )
        expired = list((await self._session.execute(stmt)).scalars().all())
        for alert in expired:
            alert.status = "EXPIRED"
        if expired:
            await self._session.flush()
        return expired

    async def get_status_summary(self) -> AlertStatusSummary:
        stmt = select(
            func.count(Alert.id),
            func.count(Alert.id).filter(Alert.status == "SENT"),
            func.count(Alert.id).filter(Alert.status.in_(["PENDING", "RETRYING"])),
            func.count(Alert.id).filter(Alert.status == "FAILED"),
            func.max(Alert.created_at),
        )
        total, sent, pending, failed, last_alert_at = (await self._session.execute(stmt)).one()
        return AlertStatusSummary(
            total_alerts=total or 0,
            sent_count=sent or 0,
            pending_count=pending or 0,
            failed_count=failed or 0,
            last_alert_at=last_alert_at,
        )


class AlertEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def log(
        self, *, alert_id: int, event_type: str, event_metadata: Mapping[str, object] | None = None
    ) -> AlertEvent:
        event = AlertEvent(
            alert_id=alert_id,
            event_type=event_type,
            event_metadata=dict(event_metadata) if event_metadata is not None else None,
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def list_for_alert(self, alert_id: int) -> list[AlertEvent]:
        stmt = (
            select(AlertEvent)
            .where(AlertEvent.alert_id == alert_id)
            .order_by(AlertEvent.timestamp.asc())
        )
        return list((await self._session.execute(stmt)).scalars().all())


class AlertDeliveryLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def log_attempt(
        self,
        *,
        alert_id: int,
        provider: str,
        status: str,
        attempt_number: int,
        response_metadata: Mapping[str, object] | None = None,
        error_message: str | None = None,
    ) -> AlertDeliveryLog:
        entry = AlertDeliveryLog(
            alert_id=alert_id,
            provider=provider,
            status=status,
            attempt_number=attempt_number,
            response_metadata=dict(response_metadata) if response_metadata is not None else None,
            error_message=error_message,
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def get_latest_for_alert(self, alert_id: int) -> AlertDeliveryLog | None:
        # Order by id, not created_at — see the matching note on
        # AlertRepository.get_by_fingerprint.
        stmt = (
            select(AlertDeliveryLog)
            .where(AlertDeliveryLog.alert_id == alert_id)
            .order_by(AlertDeliveryLog.id.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_for_alert(self, alert_id: int) -> list[AlertDeliveryLog]:
        stmt = (
            select(AlertDeliveryLog)
            .where(AlertDeliveryLog.alert_id == alert_id)
            .order_by(AlertDeliveryLog.created_at.asc())
        )
        return list((await self._session.execute(stmt)).scalars().all())
