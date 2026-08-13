"""PipelineEvent: published by market-data ingestion, consumed by
PipelineWorker to trigger a feature/scanner/decision pass. Carries no price
data itself — it's a "something changed, go look" signal; the worker still
reads current state from Postgres, same as every engine already does."""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class PipelineEvent:
    source: str  # "intraday" | "daily"
    symbol_count: int
    as_of: datetime
