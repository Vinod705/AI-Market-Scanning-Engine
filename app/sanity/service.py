"""SanityService: orchestrates every individual check in `app.sanity.checks`
into one `SanityReport`. The single entry point everything else (API,
CLI, dashboard) calls into — read-only, safe to run against a live
production database at any time, never touches market data, features,
scanner config, or trading logic.
"""

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.core.time import market_today, now_market_time, utc_now
from app.sanity import checks
from app.sanity.models import SanityReport, SanityStatus, worst_status


class SanityService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        engine: AsyncEngine,
        settings: Settings,
        redis_client: Redis,
        is_market_open: bool,
    ) -> None:
        self._session_factory = session_factory
        self._engine = engine
        self._settings = settings
        self._redis_client = redis_client
        self._is_market_open = is_market_open

    async def run(self) -> SanityReport:
        report = SanityReport(
            checked_at=utc_now(),
            overall_status=SanityStatus.UNKNOWN,
            market_timezone=self._settings.market_timezone,
            checked_at_ist=now_market_time(self._settings.market_timezone),
            current_market_date=market_today(self._settings.market_timezone),
        )

        report.components.append(checks.check_timezone_configuration(self._settings))
        report.components.append(checks.check_market_calendar(self._settings))
        report.components.append(await checks.check_database(self._engine))
        report.components.append(await checks.check_redis(self._redis_client, self._settings))

        async with self._session_factory() as session:
            prices_check, features_check, freshness = await checks.check_data_freshness(
                session, self._settings
            )
            report.components.append(prices_check)
            report.components.append(features_check)
            report.daily_prices_latest_date = freshness.daily_prices_date
            report.daily_features_latest_date = freshness.daily_features_date
            features_stale = features_check.status in (SanityStatus.STALE, SanityStatus.FAILED)

            report.components.append(
                await checks.check_market_data_pipeline(
                    session, self._settings, is_market_open=self._is_market_open
                )
            )

            for worker_name in (*checks.EVENT_DRIVEN_WORKERS, checks.MOMENTUM_PIPELINE_WORKER):
                report.components.append(
                    await checks.check_worker_heartbeat(session, worker_name, self._settings)
                )

            report.scanners = await checks.check_scanners(
                session, self._settings, features_stale=features_stale
            )

        report.issues = _collect_issues(report)
        component_statuses = [c.status for c in report.components]
        scanner_statuses = [s.status for s in report.scanners]
        report.overall_status = worst_status(component_statuses + scanner_statuses)
        return report


def _collect_issues(report: SanityReport) -> list[str]:
    issues: list[str] = []
    for component in report.components:
        if component.status in (SanityStatus.STALE, SanityStatus.FAILED, SanityStatus.BLOCKED):
            issues.append(f"{component.name}: {component.detail}")
    for scanner in report.scanners:
        if scanner.status in (SanityStatus.STALE, SanityStatus.FAILED, SanityStatus.BLOCKED):
            issues.append(f"Scanner {scanner.scanner_name}: {scanner.detail}")
    return issues
