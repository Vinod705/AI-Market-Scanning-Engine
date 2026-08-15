"""momentum alert observations (Phase 16 — live paper/simulation mode)

Revision ID: f4a9c2e7b1d8
Revises: e7c1a4b9f3d6
Create Date: 2026-08-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4a9c2e7b1d8"
down_revision: str | None = "e7c1a4b9f3d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "momentum_alert_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "transition_id",
            sa.Integer(),
            sa.ForeignKey("momentum_state_transitions.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "symbol_id",
            sa.Integer(),
            sa.ForeignKey("symbols.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "alert_id",
            sa.Integer(),
            sa.ForeignKey("alerts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("momentum_state", sa.String(length=16), nullable=False),
        sa.Column("trigger_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signal_score", sa.Numeric(6, 2), nullable=False),
        sa.Column("signal_confidence", sa.Numeric(6, 2), nullable=True),
        sa.Column("as_of_data_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_age_seconds", sa.Float(), nullable=True),
        sa.Column("is_stale", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("price_at_trigger", sa.Numeric(14, 4), nullable=True),
        sa.Column("price_after_15m", sa.Numeric(14, 4), nullable=True),
        sa.Column("price_after_1h", sa.Numeric(14, 4), nullable=True),
        sa.Column("price_after_1d", sa.Numeric(14, 4), nullable=True),
        sa.Column("price_change_pct_15m", sa.Numeric(10, 4), nullable=True),
        sa.Column("price_change_pct_1h", sa.Numeric(10, 4), nullable=True),
        sa.Column("price_change_pct_1d", sa.Numeric(10, 4), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_momentum_alert_observations_symbol_id",
        "momentum_alert_observations",
        ["symbol_id"],
    )
    op.create_index(
        "ix_momentum_alert_observations_trigger_at",
        "momentum_alert_observations",
        ["trigger_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_momentum_alert_observations_trigger_at", table_name="momentum_alert_observations"
    )
    op.drop_index(
        "ix_momentum_alert_observations_symbol_id", table_name="momentum_alert_observations"
    )
    op.drop_table("momentum_alert_observations")
