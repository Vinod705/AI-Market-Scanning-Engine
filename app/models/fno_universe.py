"""F&O universe membership — which symbols currently have NSE derivative
(futures/options) contracts, refreshed daily from the same scrip-master
call the equity symbol list already uses (see
`FivePaisaProvider.get_fno_symbol_roots`)."""

from datetime import datetime as datetime_

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class FnoUniverse(Base):
    """One row per symbol currently classified as F&O-eligible. Fully
    replaced on each refresh (see `FnoUniverseRepository.replace_all`) — a
    symbol's F&O eligibility isn't a scan result to accumulate history for,
    just a current classification."""

    __tablename__ = "fno_universe"

    symbol_id: Mapped[int] = mapped_column(
        ForeignKey("symbols.id", ondelete="CASCADE"), primary_key=True
    )
    added_at: Mapped[datetime_] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"FnoUniverse(symbol_id={self.symbol_id})"
