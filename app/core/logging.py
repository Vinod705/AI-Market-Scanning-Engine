"""Loguru-based logging configuration."""

import sys

from loguru import logger

from app.config.settings import Settings


def configure_logging(settings: Settings) -> None:
    """Configure Loguru sinks for console and rotating file output."""
    settings.log_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()

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
