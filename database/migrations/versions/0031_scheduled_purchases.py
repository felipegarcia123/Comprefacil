"""Schedule existing purchases without changing historical receipts or payments."""

from alembic import op
import sqlalchemy as sa


revision = "0031"
down_revision = "0030"


def upgrade():
    op.add_column("purchases", sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("purchases", sa.Column("expected_delivery_date", sa.Date(), nullable=True))
    op.add_column("purchases", sa.Column("payment_terms", sa.String(20), nullable=True))
    op.add_column("purchases", sa.Column("payment_due_date", sa.Date(), nullable=True))
    op.add_column("purchases", sa.Column("estimated_total", sa.Numeric(14, 2), nullable=True))
    op.add_column("purchases", sa.Column("offer_notes", sa.Text(), nullable=True))
    op.create_index("ix_purchases_expected_delivery_date", "purchases", ["expected_delivery_date"])
    op.create_index("ix_purchases_payment_due_date", "purchases", ["payment_due_date"])
    op.create_check_constraint("ck_purchases_estimated_total", "purchases",
                               "estimated_total IS NULL OR estimated_total >= 0")
    op.create_check_constraint("ck_purchases_payment_terms", "purchases",
                               "payment_terms IS NULL OR payment_terms IN ('CASH_ON_DELIVERY','DIGITAL_ON_DELIVERY','CREDIT')")
    op.create_check_constraint("ck_purchases_schedule_complete", "purchases",
                               "status != 'SCHEDULED' OR (scheduled_at IS NOT NULL AND expected_delivery_date IS NOT NULL "
                               "AND payment_terms IS NOT NULL AND estimated_total IS NOT NULL)")
    op.create_check_constraint("ck_purchases_payment_due_date", "purchases",
                               "(payment_terms = 'CREDIT' AND payment_due_date IS NOT NULL) OR "
                               "(payment_terms IS DISTINCT FROM 'CREDIT' AND payment_due_date IS NULL)")
    op.drop_constraint("purchasestatus", "purchases", type_="check")
    op.create_check_constraint("purchasestatus", "purchases",
                               "status IN ('DRAFT', 'SCHEDULED', 'RECEIVED', 'CANCELLED')")

    op.add_column("purchase_items", sa.Column("ordered_quantity", sa.Numeric(12, 3), nullable=True))
    op.add_column("purchase_items", sa.Column("ordered_unit_cost", sa.Numeric(14, 2), nullable=True))
    op.add_column("purchase_items", sa.Column("offer_list_unit_cost", sa.Numeric(14, 2), nullable=True))
    op.add_column("purchase_items", sa.Column("offer_discount_per_unit", sa.Numeric(14, 2), nullable=True))
    op.add_column("purchase_items", sa.Column("offer_note", sa.String(500), nullable=True))
    op.create_check_constraint("ck_purchase_items_ordered_quantity", "purchase_items",
                               "ordered_quantity IS NULL OR ordered_quantity > 0")
    op.create_check_constraint("ck_purchase_items_ordered_unit_cost", "purchase_items",
                               "ordered_unit_cost IS NULL OR ordered_unit_cost >= 0")
    op.create_check_constraint("ck_purchase_items_offer_discount_per_unit", "purchase_items",
                               "offer_discount_per_unit IS NULL OR offer_discount_per_unit >= 0")
    op.create_check_constraint("ck_purchase_items_offer_pair", "purchase_items",
                               "(offer_list_unit_cost IS NULL AND offer_discount_per_unit IS NULL) OR "
                               "(offer_list_unit_cost IS NOT NULL AND offer_discount_per_unit IS NOT NULL)")


def downgrade():
    bind = op.get_bind()
    populated = bind.execute(sa.text("SELECT EXISTS (SELECT 1 FROM purchases WHERE scheduled_at IS NOT NULL) ")).scalar()
    if populated:
        raise RuntimeError("Refusing to remove scheduled purchase history")
    op.drop_constraint("ck_purchase_items_offer_pair", "purchase_items", type_="check")
    op.drop_constraint("ck_purchase_items_offer_discount_per_unit", "purchase_items", type_="check")
    op.drop_constraint("ck_purchase_items_ordered_unit_cost", "purchase_items", type_="check")
    op.drop_constraint("ck_purchase_items_ordered_quantity", "purchase_items", type_="check")
    for name in ("offer_note", "offer_discount_per_unit", "offer_list_unit_cost",
                 "ordered_unit_cost", "ordered_quantity"):
        op.drop_column("purchase_items", name)
    op.drop_constraint("purchasestatus", "purchases", type_="check")
    op.create_check_constraint("purchasestatus", "purchases",
                               "status IN ('DRAFT', 'RECEIVED', 'CANCELLED')")
    op.drop_constraint("ck_purchases_payment_terms", "purchases", type_="check")
    op.drop_constraint("ck_purchases_payment_due_date", "purchases", type_="check")
    op.drop_constraint("ck_purchases_schedule_complete", "purchases", type_="check")
    op.drop_constraint("ck_purchases_estimated_total", "purchases", type_="check")
    op.drop_index("ix_purchases_payment_due_date", table_name="purchases")
    op.drop_index("ix_purchases_expected_delivery_date", table_name="purchases")
    for name in ("offer_notes", "estimated_total", "payment_due_date", "payment_terms",
                 "expected_delivery_date", "scheduled_at"):
        op.drop_column("purchases", name)
