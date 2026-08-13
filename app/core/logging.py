"""Loguru-based logging configuration."""

import inspect
import logging
import sys
from types import FrameType

from loguru import logger

from app.config.settings import Settings


class InterceptHandler(logging.Handler):
    """Redirects stdlib `logging` records (e.g. APScheduler's own diagnostics
    - job overlap skips, misfires, exceptions escaping a job) into Loguru so
    they land in the same app.log/errors.log sinks instead of being dropped
    silently or going to stderr uncorrelated with the rest of the app's logs.
    """

    def emit(self, record: logging.LogRecord) -> None:
        level: int | str
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        depth = 0
        frame: FrameType | None = inspect.currentframe()
        while frame is not None and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def configure_logging(settings: Settings) -> None:
    """Configure Loguru sinks for console and rotating file output."""
    settings.log_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    for name in ("apscheduler", "uvicorn", "uvicorn.access", "uvicorn.error"):
        std_logger = logging.getLogger(name)
        std_logger.handlers = [InterceptHandler()]
        std_logger.propagate = False

    logger.add(
        sys.stdout,
        level=settings.log_level,
        colorize=True,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        backtrace=False,
        diagnose=settings.debug,
    )

    logger.add(
        settings.log_dir / "app.log",
        level=settings.log_level,
        rotation=settings.log_rotation,
        retention=settings.log_retention,
        compression="zip",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        backtrace=False,
        diagnose=settings.debug,
        enqueue=True,
    )

    logger.add(
        settings.log_dir / "errors.log",
        level="ERROR",
        rotation=settings.log_rotation,
        retention=settings.log_retention,
        compression="zip",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        backtrace=True,
        diagnose=settings.debug,
        enqueue=True,
    )
