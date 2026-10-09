"""expand suppliers and add formal purchases"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {column["name"] for column in inspector.get_columns("suppliers")}
    additions = {
        "document": sa.Column("document", sa.String(40), nullable=True),
        "phone": sa.Column("phone", sa.String(40), nullable=True),
        "email": sa.Column("email", sa.String(180), nullable=True),
        "address": sa.Column("address", sa.String(240), nullable=True),
        "contact_name": sa.Column("contact_name", sa.String(160), nullable=True),
        "notes": sa.Column("notes", sa.Text(), nullable=True),
        "active": sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        "created_at": sa.Column("created_at", sa.DateTime(timezone=True),
                                server_default=sa.func.now(), nullable=False),
    }
    for name, column in additions.items():
        if name not in existing:
            op.add_column("suppliers", column)
    inspector = sa.inspect(bind)
    unique_columns = {tuple(item["column_names"]) for item in inspector.get_unique_constraints("suppliers")}
    if ("document",) not in unique_columns:
        op.create_unique_constraint("uq_suppliers_document", "suppliers", ["document"])

    if "purchases" not in inspector.get_table_names():
        op.create_table(
            "purchases",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("supplier_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("suppliers.id"), nullable=False),
            sa.Column("status", sa.String(9), nullable=False),
            sa.Column("invoice_number", sa.String(80), nullable=True),
            sa.Column("purchased_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("subtotal", sa.Numeric(14, 2), nullable=False),
            sa.Column("total", sa.Numeric(14, 2), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
            sa.CheckConstraint("status IN ('DRAFT', 'RECEIVED', 'CANCELLED')", name="purchasestatus"),
        )
        op.create_index("ix_purchases_supplier_id", "purchases", ["supplier_id"])
        op.create_index("ix_purchases_status", "purchases", ["status"])
    inspector = sa.inspect(bind)
    if "purchase_items" not in inspector.get_table_names():
        op.create_table(
            "purchase_items",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("purchase_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("purchases.id", ondelete="CASCADE"), nullable=False),
            sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=False),
            sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
            sa.Column("unit_cost", sa.Numeric(14, 2), nullable=False),
            sa.Column("line_total", sa.Numeric(14, 2), nullable=False),
            sa.UniqueConstraint("purchase_id", "product_id", name="uq_purchase_product"),
        )
        op.create_index("ix_purchase_items_purchase_id", "purchase_items", ["purchase_id"])
        op.create_index("ix_purchase_items_product_id", "purchase_items", ["product_id"])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "purchase_items" in inspector.get_table_names():
        op.drop_table("purchase_items")
    inspector = sa.inspect(bind)
    if "purchases" in inspector.get_table_names():
        op.drop_table("purchases")
    existing = {column["name"] for column in sa.inspect(bind).get_columns("suppliers")}
    for name in ["created_at", "active", "notes", "contact_name", "address", "email", "phone", "document"]:
        if name in existing:
            op.drop_column("suppliers", name)
