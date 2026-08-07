"""Tests for app.scanner.scanner_registry.ScannerRegistry."""

from app.config.settings import Settings
from app.scanner.breakout_scanner import BreakoutScanner
from app.scanner.scanner_registry import ScannerRegistry


def test_register_and_get_round_trip() -> None:
    registry = ScannerRegistry()
    scanner = BreakoutScanner(Settings())

    registry.register(scanner)

    assert registry.get("breakout_v1") is scanner
    assert registry.get_all() == [scanner]


def test_get_returns_none_for_unknown_scanner() -> None:
    registry = ScannerRegistry()
    assert registry.get("does_not_exist") is None


def test_register_overwrites_existing_scanner_with_same_name() -> None:
    registry = ScannerRegistry()
    first = BreakoutScanner(Settings())
    second = BreakoutScanner(Settings())

    registry.register(first)
    registry.register(second)

    assert registry.get_all() == [second]
