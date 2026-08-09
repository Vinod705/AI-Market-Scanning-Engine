"""alert tables: alerts, alert_events, alert_delivery_logs

Revision ID: c1a5f7b2e9d4
Revises: f9fea8051e08
Create Date: 2026-08-08

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1a5f7b2e9d4"
down_revision: str | None = "f9fea8051e08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "symbol_id",
            sa.Integer(),
            sa.ForeignKey("symbols.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scanner_name", sa.String(64), nullable=False),
        sa.Column("signal_type", sa.String(32), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("score", sa.Numeric(6, 2), nullable=False),
        sa.Column("quality", sa.String(16), nullable=False),
        sa.Column("entry_reference", sa.Numeric(14, 4), nullable=True),
        sa.Column("breakout_level", sa.Numeric(14, 4), nullable=True),
        sa.Column("support_level", sa.Numeric(14, 4), nullable=True),
        sa.Column("resistance_level", sa.Numeric(14, 4), nullable=True),
        sa.Column("feature_snapshot", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("passed_rules", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("signal_date", sa.Date(), nullable=False),
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
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_alerts_symbol_id", "alerts", ["symbol_id"])
    op.create_index("ix_alerts_scanner_name", "alerts", ["scanner_name"])
    op.create_index("ix_alerts_status", "alerts", ["status"])
    op.create_index("ix_alerts_fingerprint", "alerts", ["fingerprint"])
    op.create_index("ix_alerts_signal_date", "alerts", ["signal_date"])

    op.create_table(
        "alert_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "alert_id", sa.Integer(), sa.ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column(
            "timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("event_metadata", sa.JSON(), nullable=True),
    )
    op.create_index("ix_alert_events_alert_id", "alert_events", ["alert_id"])
    op.create_index("ix_alert_events_event_type", "alert_events", ["event_type"])

    op.create_table(
        "alert_delivery_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "alert_id", sa.Integer(), sa.ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("response_metadata", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_alert_delivery_logs_alert_id", "alert_delivery_logs", ["alert_id"])
    op.create_index("ix_alert_delivery_logs_provider", "alert_delivery_logs", ["provider"])
    op.create_index("ix_alert_delivery_logs_status", "alert_delivery_logs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_alert_delivery_logs_status", table_name="alert_delivery_logs")
    op.drop_index("ix_alert_delivery_logs_provider", table_name="alert_delivery_logs")
    op.drop_index("ix_alert_delivery_logs_alert_id", table_name="alert_delivery_logs")
    op.drop_table("alert_delivery_logs")
    op.drop_index("ix_alert_events_event_type", table_name="alert_events")
    op.drop_index("ix_alert_events_alert_id", table_name="alert_events")
    op.drop_table("alert_events")
    op.drop_index("ix_alerts_signal_date", table_name="alerts")
    op.drop_index("ix_alerts_fingerprint", table_name="alerts")
    op.drop_index("ix_alerts_status", table_name="alerts")
    op.drop_index("ix_alerts_scanner_name", table_name="alerts")
    op.drop_index("ix_alerts_symbol_id", table_name="alerts")
    op.drop_table("alerts")
