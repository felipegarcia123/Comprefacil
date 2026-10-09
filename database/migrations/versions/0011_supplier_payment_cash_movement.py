"""allow supplier-payment cash movements"""
from alembic import op
import sqlalchemy as sa


revision = "0011"
down_revision = "0010"


def upgrade():
    # Existing PostgreSQL databases used varchar(10), which cannot store
    # SUPPLIER_PAYMENT and causes the complete receipt transaction to roll back.
    op.alter_column("cash_movements", "movement_type",
                    existing_type=sa.String(10), type_=sa.String(32),
                    existing_nullable=False)
    op.drop_constraint("ck_cash_movements_type", "cash_movements", type_="check")
    op.create_check_constraint(
        "ck_cash_movements_type", "cash_movements",
        "movement_type IN ('SALE','DEPOSIT','WITHDRAWAL','REFUND','ADJUSTMENT','EXPENSE','SUPPLIER_PAYMENT')")


def downgrade():
    op.drop_constraint("ck_cash_movements_type", "cash_movements", type_="check")
    op.create_check_constraint(
        "ck_cash_movements_type", "cash_movements",
        "movement_type IN ('SALE','DEPOSIT','WITHDRAWAL','REFUND','ADJUSTMENT','EXPENSE')")
    op.alter_column("cash_movements", "movement_type",
                    existing_type=sa.String(32), type_=sa.String(10),
                    existing_nullable=False)
