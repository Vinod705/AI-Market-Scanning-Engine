"""market data feed logs table

Revision ID: c3e6f8a1d9b2
Revises: a1f4c9e2b7d5
Create Date: 2026-08-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3e6f8a1d9b2"
down_revision: str | None = "a1f4c9e2b7d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_data_feed_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("messages_received", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("ticks_processed", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("duplicates_dropped", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("candles_flushed", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("disconnect_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_market_data_feed_logs_connected_at", "market_data_feed_logs", ["connected_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_market_data_feed_logs_connected_at", table_name="market_data_feed_logs")
    op.drop_table("market_data_feed_logs")
