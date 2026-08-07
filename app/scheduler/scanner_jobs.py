"""Scanner engine job, registered onto the shared `SchedulerService`.

Runs every minute like the Phase 3 feature engine job. `ScannerManager` skips
any symbol that already has a `scanner_results` row for today (see
`ScannerResultRepository.upsert`'s dedup note), so repeated ticks within the
same day are cheap no-ops once every symbol has been scanned.
"""

from loguru import logger

from app.scanner.engine import ScannerEngine
from app.scheduler.service import SchedulerService

JOB_ID_SCANNER = "scanner_engine_run"


def register_scanner_jobs(
    scheduler_service: SchedulerService, scanner_engine: ScannerEngine
) -> None:
    async def _scanner_job() -> None:
        await scanner_engine.run_all()

    scheduler_service.add_job(
        _scanner_job,
        trigger="interval",
        minutes=1,
        id=JOB_ID_SCANNER,
        replace_existing=True,
    )

    logger.info("Registered scanner engine job: {job_id}", job_id=JOB_ID_SCANNER)
