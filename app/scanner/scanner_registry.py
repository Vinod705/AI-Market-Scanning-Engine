"""Scanner registry: the Strategy Pattern lookup `ScannerManager`/`ScannerEngine` use.

Adding a new scanner never means editing `ScannerManager` or `ScannerEngine`
— it means constructing the new `BaseScanner` subclass and calling
`register()` on it (see `app/main.py` for where `BreakoutScanner` is
registered).
"""

from app.scanner.base_scanner import BaseScanner


class ScannerRegistry:
    def __init__(self) -> None:
        self._scanners: dict[str, BaseScanner] = {}

    def register(self, scanner: BaseScanner) -> None:
        self._scanners[scanner.name] = scanner

    def get(self, name: str) -> BaseScanner | None:
        return self._scanners.get(name)

    def get_all(self) -> list[BaseScanner]:
        return list(self._scanners.values())
