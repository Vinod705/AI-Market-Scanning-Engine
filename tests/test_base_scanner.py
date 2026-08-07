"""Tests for BaseScanner.save_results, the shared persistence path every scanner uses."""

from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.models.daily_feature import DailyFeature
from app.providers.base_provider import ProviderSymbol
from app.repositories.market_repository import SymbolRepository
from app.repositories.scanner_repository import ScannerResultRepository
from app.scanner.breakout_scanner import BreakoutScanner
from app.scanner.models import ScanContext, ScanOutcome


async def test_save_results_persists_score_status_and_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        symbol = await SymbolRepository(session).upsert(
            ProviderSymbol(symbol="TCS", exchange="N", instrument_token="1")
        )
        await session.commit()

        features = DailyFeature(symbol_id=symbol.id, date=date(2026, 1, 5), rsi14=Decimal("55.5"))
        context = ScanContext(symbol=symbol, features=features, price=Decimal("110"))
        outcome = ScanOutcome(qualified=True, reason="all conditions met: ...")

        scanner = BreakoutScanner(Settings())
        repo = ScannerResultRepository(session)
        await scanner.save_results(repo, context, outcome, score=82.456, scan_date=date(2026, 1, 5))
        await session.commit()

        results = await repo.get_for_symbol(symbol.id)
        assert len(results) == 1
        row = results[0]
        assert row.status == "qualified"
        assert row.scanner_name == "breakout_v1"
        assert float(row.score) == 82.46  # rounded to 2dp
        assert row.feature_snapshot["price"] == "110"
        assert row.feature_snapshot["rsi14"] == "55.5"
