"""structured HOME/OTHER reason for cash withdrawals"""
from alembic import op
import sqlalchemy as sa


revision = "0012"
down_revision = "0011"


def upgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("cash_movements")}
    if "withdrawal_reason" not in columns:
        # Nullable by design: existing WITHDRAWAL rows stay unclassified, never
        # guessed at. Only new withdrawals are required to pick HOME or OTHER.
        op.add_column("cash_movements", sa.Column("withdrawal_reason", sa.String(10), nullable=True))
        op.create_check_constraint(
            "ck_cash_movements_withdrawal_reason", "cash_movements",
            "withdrawal_reason IS NULL OR withdrawal_reason IN ('HOME','OTHER')")


def downgrade():
    op.drop_constraint("ck_cash_movements_withdrawal_reason", "cash_movements", type_="check")
    op.drop_column("cash_movements", "withdrawal_reason")
