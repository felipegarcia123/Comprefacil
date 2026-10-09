"""add completed sales reporting index"""
from alembic import op

revision = "0009"
down_revision = "0008"


def upgrade():
    op.create_index("ix_sales_status_completed_at", "sales", ["status", "completed_at"])


def downgrade():
    op.drop_index("ix_sales_status_completed_at", table_name="sales")
