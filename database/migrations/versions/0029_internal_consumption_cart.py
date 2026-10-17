"""cart-based internal consumption detail

Adds only child detail rows for newly-created multi-product internal consumptions.
Existing internal_consumptions, sales, payments and financial history are untouched.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0029"
down_revision = "0028"

UUID = postgresql.UUID(as_uuid=True)


def upgrade():
    op.create_table(
        "internal_consumption_items",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("internal_consumption_id", UUID,
                  sa.ForeignKey("internal_consumptions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", UUID, sa.ForeignKey("products.id"), nullable=False),
        sa.Column("product_name", sa.String(240), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("unit_cost", sa.Numeric(14, 2), nullable=False),
        sa.Column("reference_sale_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("cost_total", sa.Numeric(14, 2), nullable=False),
        sa.Column("sale_value_total", sa.Numeric(14, 2), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_internal_consumption_items_quantity"),
        sa.UniqueConstraint("internal_consumption_id", "product_id",
                            name="uq_internal_consumption_item_product"),
    )
    op.create_index("ix_internal_consumption_items_internal_consumption_id",
                    "internal_consumption_items", ["internal_consumption_id"])
    op.create_index("ix_internal_consumption_items_product_id",
                    "internal_consumption_items", ["product_id"])


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT count(*) FROM internal_consumption_items")).scalar():
        raise RuntimeError("Cannot downgrade 0029 after internal consumption cart records exist")
    op.drop_index("ix_internal_consumption_items_product_id", table_name="internal_consumption_items")
    op.drop_index("ix_internal_consumption_items_internal_consumption_id", table_name="internal_consumption_items")
    op.drop_table("internal_consumption_items")
