"""market data tables: symbols, daily_prices, intraday_prices, market_status, collector_logs

Revision ID: 686f8df2789c
Revises:
Create Date: 2026-08-07

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "686f8df2789c"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "symbols",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("exchange", sa.String(length=16), nullable=False),
        sa.Column("instrument_token", sa.String(length=64), nullable=False),
        sa.Column("company_name", sa.String(length=255), nullable=True),
        sa.Column("sector", sa.String(length=128), nullable=True),
        sa.Column("industry", sa.String(length=128), nullable=True),
        sa.Column("listing_date", sa.Date(), nullable=True),
        sa.Column("is_ipo", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("symbol", "exchange", name="uq_symbols_symbol_exchange"),
    )
    op.create_index("ix_symbols_symbol", "symbols", ["symbol"])
    op.create_index("ix_symbols_instrument_token", "symbols", ["instrument_token"])
    op.create_index("ix_symbols_is_active", "symbols", ["is_active"])

    op.create_table(
        "daily_prices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "symbol_id",
            sa.Integer(),
            sa.ForeignKey("symbols.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(14, 4), nullable=False),
        sa.Column("high", sa.Numeric(14, 4), nullable=False),
        sa.Column("low", sa.Numeric(14, 4), nullable=False),
        sa.Column("close", sa.Numeric(14, 4), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column("vwap", sa.Numeric(14, 4), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("symbol_id", "date", name="uq_daily_prices_symbol_date"),
    )
    op.create_index("ix_daily_prices_symbol_id", "daily_prices", ["symbol_id"])
    op.create_index("ix_daily_prices_date", "daily_prices", ["date"])

    op.create_table(
        "intraday_prices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "symbol_id",
            sa.Integer(),
            sa.ForeignKey("symbols.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(14, 4), nullable=False),
        sa.Column("high", sa.Numeric(14, 4), nullable=False),
        sa.Column("low", sa.Numeric(14, 4), nullable=False),
        sa.Column("close", sa.Numeric(14, 4), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column("vwap", sa.Numeric(14, 4), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("symbol_id", "datetime", name="uq_intraday_prices_symbol_datetime"),
    )
    op.create_index("ix_intraday_prices_symbol_id", "intraday_prices", ["symbol_id"])
    op.create_index("ix_intraday_prices_datetime", "intraday_prices", ["datetime"])

    op.create_table(
        "market_status",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("market_open", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("provider_connected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_update", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "collector_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finish_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration", sa.Float(), nullable=True),
        sa.Column("symbols_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("collector_logs")
    op.drop_table("market_status")
    op.drop_index("ix_intraday_prices_datetime", table_name="intraday_prices")
    op.drop_index("ix_intraday_prices_symbol_id", table_name="intraday_prices")
    op.drop_table("intraday_prices")
    op.drop_index("ix_daily_prices_date", table_name="daily_prices")
    op.drop_index("ix_daily_prices_symbol_id", table_name="daily_prices")
    op.drop_table("daily_prices")
    op.drop_index("ix_symbols_is_active", table_name="symbols")
    op.drop_index("ix_symbols_instrument_token", table_name="symbols")
    op.drop_index("ix_symbols_symbol", table_name="symbols")
    op.drop_table("symbols")
