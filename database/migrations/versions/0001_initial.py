"""Initial POS schema, frozen independently of the current ORM models.

Only the tables needed by revision 0002 belong here. Later revisions add
suppliers' details, purchases, payment channels, and subsequent features.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None

UUID = postgresql.UUID(as_uuid=True)
MONEY = sa.Numeric(14, 2)
QUANTITY = sa.Numeric(12, 3)
TIMESTAMP = sa.DateTime(timezone=True)


def upgrade():
    op.create_table("users",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("name", sa.String(120), nullable=False))

    op.create_table("suppliers",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("name", sa.String(160), nullable=False, unique=True))

    op.create_table("products",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("barcode", sa.String(64), nullable=False),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("sale_price", MONEY, nullable=False),
        sa.Column("unit_cost", MONEY, nullable=False),
        sa.Column("supplier_id", UUID, sa.ForeignKey("suppliers.id"), nullable=True))
    op.create_index("ix_products_barcode", "products", ["barcode"], unique=True)
    op.create_index("ix_products_name", "products", ["name"])

    op.create_table("cash_sessions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("opening_amount", MONEY, nullable=False),
        sa.Column("opened_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("opened_by", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("closed_at", TIMESTAMP, nullable=True),
        sa.Column("closing_expected", MONEY, nullable=True),
        sa.Column("closing_actual", MONEY, nullable=True),
        sa.Column("difference", MONEY, nullable=True))

    op.create_table("sales",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("number", sa.String(32), nullable=False, unique=True),
        sa.Column("status", sa.String(9), nullable=False),
        sa.Column("created_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", TIMESTAMP, nullable=True),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("total", MONEY, nullable=False))

    op.create_table("sale_items",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("sale_id", UUID, sa.ForeignKey("sales.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", UUID, sa.ForeignKey("products.id"), nullable=False),
        sa.Column("product_name", sa.String(180), nullable=False),
        sa.Column("quantity", QUANTITY, nullable=False),
        sa.Column("unit_price", MONEY, nullable=False),
        sa.Column("unit_cost", MONEY, nullable=False),
        sa.Column("line_total", MONEY, nullable=False),
        sa.UniqueConstraint("sale_id", "product_id"))
    op.create_index("ix_sale_items_sale_id", "sale_items", ["sale_id"])

    op.create_table("payments",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("sale_id", UUID, sa.ForeignKey("sales.id"), nullable=False),
        sa.Column("method", sa.String(8), nullable=True),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("received_amount", MONEY, nullable=True),
        sa.Column("change_amount", MONEY, nullable=True),
        sa.Column("created_at", TIMESTAMP, server_default=sa.func.now(), nullable=False))
    op.create_index("ix_payments_sale_id", "payments", ["sale_id"])

    op.create_table("inventory_movements",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("product_id", UUID, sa.ForeignKey("products.id"), nullable=False),
        sa.Column("movement_type", sa.String(10), nullable=False),
        sa.Column("quantity", QUANTITY, nullable=False),
        sa.Column("unit_cost", MONEY, nullable=False),
        sa.Column("reference_id", UUID, nullable=True),
        sa.Column("created_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), nullable=False))
    op.create_index("ix_inventory_movements_product_id", "inventory_movements", ["product_id"])
    op.create_index("ix_inventory_movements_reference_id", "inventory_movements", ["reference_id"])

    op.create_table("cash_movements",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("cash_session_id", UUID, sa.ForeignKey("cash_sessions.id"), nullable=False),
        sa.Column("movement_type", sa.String(10), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("reference_id", UUID, nullable=True),
        sa.Column("created_at", TIMESTAMP, server_default=sa.func.now(), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), nullable=False))
    op.create_index("ix_cash_movements_cash_session_id", "cash_movements", ["cash_session_id"])
    op.create_index("ix_cash_movements_reference_id", "cash_movements", ["reference_id"])


def downgrade():
    for table in (
        "cash_movements", "inventory_movements", "payments", "sale_items",
        "sales", "cash_sessions", "products", "suppliers", "users",
    ):
        op.drop_table(table)

