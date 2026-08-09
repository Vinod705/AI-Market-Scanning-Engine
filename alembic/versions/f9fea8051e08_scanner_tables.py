"""scanner tables: scanner_results, scanner_runs, scanner_logs

Revision ID: f9fea8051e08
Revises: b88081472870
Create Date: 2026-08-07

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f9fea8051e08"
down_revision: str | None = "b88081472870"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scanner_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scanner_name", sa.String(64), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finish_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration", sa.Float(), nullable=True),
        sa.Column("symbols_scanned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("qualified_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_scanner_runs_scanner_name", "scanner_runs", ["scanner_name"])

    op.create_table(
        "scanner_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "symbol_id",
            sa.Integer(),
            sa.ForeignKey("symbols.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scanner_name", sa.String(64), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("score", sa.Numeric(6, 2), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("feature_snapshot", sa.JSON(), nullable=False),
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
        sa.UniqueConstraint(
            "symbol_id", "scanner_name", "date", name="uq_scanner_results_symbol_scanner_date"
        ),
    )
    op.create_index("ix_scanner_results_symbol_id", "scanner_results", ["symbol_id"])
    op.create_index("ix_scanner_results_scanner_name", "scanner_results", ["scanner_name"])
    op.create_index("ix_scanner_results_date", "scanner_results", ["date"])

    op.create_table(
        "scanner_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("scanner_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "symbol_id",
            sa.Integer(),
            sa.ForeignKey("symbols.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("scanner_name", sa.String(64), nullable=False),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_scanner_logs_run_id", "scanner_logs", ["run_id"])
    op.create_index("ix_scanner_logs_symbol_id", "scanner_logs", ["symbol_id"])


def downgrade() -> None:
    op.drop_index("ix_scanner_logs_symbol_id", table_name="scanner_logs")
    op.drop_index("ix_scanner_logs_run_id", table_name="scanner_logs")
    op.drop_table("scanner_logs")
    op.drop_index("ix_scanner_results_date", table_name="scanner_results")
    op.drop_index("ix_scanner_results_scanner_name", table_name="scanner_results")
    op.drop_index("ix_scanner_results_symbol_id", table_name="scanner_results")
    op.drop_table("scanner_results")
    op.drop_index("ix_scanner_runs_scanner_name", table_name="scanner_runs")
    op.drop_table("scanner_runs")
