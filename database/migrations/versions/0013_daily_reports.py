"""daily_reports: immutable per-jornada snapshot, structure only (no backfill)"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0013"
down_revision = "0012"


def upgrade():
    bind = op.get_bind()
    if "daily_reports" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "daily_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("cash_session_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("cash_sessions.id"), nullable=False, unique=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("sales_total", sa.Numeric(14, 2), nullable=False),
        sa.Column("cash_total", sa.Numeric(14, 2), nullable=False),
        sa.Column("electronic_total", sa.Numeric(14, 2), nullable=False),
        sa.Column("tickets", sa.Integer(), nullable=False),
        sa.Column("average_ticket", sa.Numeric(14, 2), nullable=False),
        sa.Column("gross_cost", sa.Numeric(14, 2), nullable=False),
        sa.Column("gross_profit", sa.Numeric(14, 2), nullable=False),
        sa.Column("gross_margin", sa.Numeric(7, 2), nullable=False),
        sa.Column("closing_expected", sa.Numeric(14, 2), nullable=False),
        sa.Column("closing_actual", sa.Numeric(14, 2), nullable=False),
        sa.Column("closing_difference", sa.Numeric(14, 2), nullable=False),
        sa.Column("report_data", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_daily_reports_cash_session_id", "daily_reports", ["cash_session_id"], unique=True)


def downgrade():
    op.drop_table("daily_reports")
