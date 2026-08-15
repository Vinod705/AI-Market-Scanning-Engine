"""Pydantic response schemas for the /analytics API — the dashboard's
read-only surface over stored/cached analytics (Phase 15). Every field
here is read from a table already populated by an existing scheduled job
or engine; nothing in this module computes anything itself.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class MarketRegimeOut(BaseModel):
    computed_at: datetime
    as_of: date
    regime: str | None
    score: Decimal | None
    index_symbol: str
    index_trend_direction: str | None
    index_trend_strength: Decimal | None
    volatility_state: str | None
    sector_leading_pct: Decimal | None
    evidence_sources_used: list[str]
    missing_evidence: list[str]


class SectorRrgOut(BaseModel):
    computed_at: datetime
    sector_symbol: str
    benchmark_symbol: str
    date: date
    rs_ratio: Decimal | None
    rs_momentum: Decimal | None
    rotation_state: str | None
    momentum_score: Decimal | None
    trend_strength: Decimal | None
    price_performance_pct: Decimal | None
    score: Decimal | None


class MomentumCandidateOut(BaseModel):
    symbol: str
    state: str
    score: Decimal
    reason: str
    entered_at: datetime
    updated_at: datetime


class MomentumTransitionOut(BaseModel):
    symbol: str
    from_state: str | None
    to_state: str
    timestamp: datetime
    reason: str
    score: Decimal


class VolumeLeaderOut(BaseModel):
    symbol: str
    date: date
    relative_volume: Decimal
    volume_spike: bool
    volume_ma20: int | None


class OiBuildupOut(BaseModel):
    symbol: str
    instrument_key: str
    observed_at: datetime
    classification: str
    oi: Decimal
    oi_change_pct: Decimal | None
    price_change_pct: Decimal | None


class FundamentalsCoverageOut(BaseModel):
    total_symbols_tracked: int
    with_data_count: int
    fresh_count: int
    last_fetched_at: datetime | None


class LiveTriggerOut(BaseModel):
    """Phase 16 operational-validation record for one alert-worthy
    momentum-state transition — see
    `app.models.momentum_alert_observation.MomentumAlertObservation`'s
    docstring for why every `price_after_*`/`*_pct` field is a plain
    factual observation, never a simulated trade or a verdict."""

    symbol: str
    momentum_state: str
    trigger_at: datetime
    signal_score: Decimal
    signal_confidence: Decimal | None
    data_age_seconds: float | None
    is_stale: bool
    price_at_trigger: Decimal | None
    price_after_15m: Decimal | None
    price_after_1h: Decimal | None
    price_after_1d: Decimal | None
    price_change_pct_15m: Decimal | None
    price_change_pct_1h: Decimal | None
    price_change_pct_1d: Decimal | None
    alert_id: int | None
    delivery_status: str | None
    delivery_latency_seconds: float | None


class CollectorRunOut(BaseModel):
    start_time: datetime
    finish_time: datetime | None
    duration: float | None
    symbols_processed: int
    success_count: int
    failed_count: int
    error_message: str | None


class MarketFeedConnectionOut(BaseModel):
    connected_at: datetime
    disconnected_at: datetime | None
    messages_received: int
    ticks_processed: int
    duplicates_dropped: int
    candles_flushed: int
    disconnect_reason: str | None


class ProviderHealthOut(BaseModel):
    recent_collector_runs: list[CollectorRunOut]
    recent_feed_connections: list[MarketFeedConnectionOut]
