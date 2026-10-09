"""operational categories, manual sales, expenses and internal consumption"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"

CATEGORIES = [
    ("10000000-0000-0000-0000-000000000001", "CIGARROS", "Cigarros", 10),
    ("10000000-0000-0000-0000-000000000002", "MASCOTAS_CON_CODIGO", "Mascotas con código", 20),
    ("10000000-0000-0000-0000-000000000003", "MASCOTAS_SIN_CODIGO", "Mascotas sin código / Bultos", 30),
    ("10000000-0000-0000-0000-000000000004", "LICORES", "Licores", 40),
    ("10000000-0000-0000-0000-000000000005", "SUPER", "Super", 50),
    ("10000000-0000-0000-0000-000000000006", "SIN_CLASIFICAR", "Sin clasificar", 999),
]


def _columns(inspector, table):
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade():
    bind = op.get_bind(); inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "operational_categories" not in tables:
        op.create_table("operational_categories",
            sa.Column("id", sa.UUID(), primary_key=True),
            sa.Column("code", sa.String(64), nullable=False, unique=True),
            sa.Column("name", sa.String(120), nullable=False, unique=True),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
        op.create_index("ix_operational_categories_code", "operational_categories", ["code"], unique=True)
        op.create_index("ix_operational_categories_active", "operational_categories", ["active"])
    for category in CATEGORIES:
        bind.execute(sa.text("""
            INSERT INTO operational_categories (id, code, name, active, display_order)
            VALUES (:id, :code, :name, true, :display_order)
            ON CONFLICT (code) DO NOTHING
        """), dict(zip(("id", "code", "name", "display_order"), category)))

    payment_columns = _columns(sa.inspect(bind), "payment_channels")
    if "payment_group" not in payment_columns:
        op.add_column("payment_channels", sa.Column("payment_group", sa.String(20), nullable=True))
        bind.execute(sa.text("UPDATE payment_channels SET payment_group = CASE WHEN affects_cash THEN 'CASH' ELSE 'ELECTRONIC' END"))
        op.alter_column("payment_channels", "payment_group", nullable=False, server_default="ELECTRONIC")
        op.create_check_constraint("ck_payment_channels_payment_group", "payment_channels",
                                   "payment_group IN ('CASH','ELECTRONIC','OTHER')")
        op.create_index("ix_payment_channels_payment_group", "payment_channels", ["payment_group"])

    product_columns = _columns(sa.inspect(bind), "products")
    if "operational_category_id" not in product_columns:
        op.add_column("products", sa.Column("operational_category_id", sa.UUID(), nullable=True))
        op.create_foreign_key("fk_products_operational_category", "products", "operational_categories",
                              ["operational_category_id"], ["id"])
        op.create_index("ix_products_operational_category_id", "products", ["operational_category_id"])
        bind.execute(sa.text("UPDATE products SET operational_category_id = :id WHERE operational_category_id IS NULL"),
                     {"id": CATEGORIES[-1][0]})

    sale_columns = _columns(sa.inspect(bind), "sales")
    if "source" not in sale_columns:
        op.add_column("sales", sa.Column("source", sa.String(30), nullable=False, server_default="POS_PRODUCT"))
        op.create_check_constraint("ck_sales_source", "sales",
                                   "source IN ('POS_PRODUCT','MANUAL_BULK','MANUAL_SACK')")
        op.create_index("ix_sales_source", "sales", ["source"])
    if "notes" not in sale_columns:
        op.add_column("sales", sa.Column("notes", sa.String(500), nullable=True))
    if "idempotency_key" not in sale_columns:
        op.add_column("sales", sa.Column("idempotency_key", sa.String(80), nullable=True))
        op.create_unique_constraint("uq_sales_idempotency_key", "sales", ["idempotency_key"])

    item_columns = _columns(sa.inspect(bind), "sale_items")
    if "operational_category_id" not in item_columns:
        op.add_column("sale_items", sa.Column("operational_category_id", sa.UUID(), nullable=True))
        op.create_foreign_key("fk_sale_items_operational_category", "sale_items", "operational_categories",
                              ["operational_category_id"], ["id"])
        op.create_index("ix_sale_items_operational_category_id", "sale_items", ["operational_category_id"])
    if "category_code" not in item_columns:
        op.add_column("sale_items", sa.Column("category_code", sa.String(64), nullable=False,
                                               server_default="SIN_CLASIFICAR"))
    op.alter_column("sale_items", "product_id", existing_type=sa.UUID(), nullable=True)
    bind.execute(sa.text("""
        UPDATE sale_items si SET operational_category_id = p.operational_category_id,
          category_code = COALESCE(oc.code, 'SIN_CLASIFICAR')
        FROM products p LEFT JOIN operational_categories oc ON oc.id = p.operational_category_id
        WHERE si.product_id = p.id AND si.operational_category_id IS NULL
    """))

    if "expenses" not in tables:
        op.create_table("expenses",
            sa.Column("id", sa.UUID(), primary_key=True), sa.Column("amount", sa.Numeric(14,2), nullable=False),
            sa.Column("category", sa.String(120), nullable=False), sa.Column("description", sa.String(240), nullable=False),
            sa.Column("payment_source", sa.String(20), nullable=False),
            sa.Column("payment_channel_id", sa.UUID(), sa.ForeignKey("payment_channels.id"), nullable=True),
            sa.Column("notes", sa.String(500), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("created_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("idempotency_key", sa.String(80), nullable=False, unique=True),
            sa.CheckConstraint("payment_source IN ('CASH','ELECTRONIC','OTHER')", name="ck_expenses_payment_source"))
        op.create_index("ix_expenses_category", "expenses", ["category"])
        op.create_index("ix_expenses_payment_source", "expenses", ["payment_source"])
        op.create_index("ix_expenses_payment_channel_id", "expenses", ["payment_channel_id"])
        op.create_index("ix_expenses_created_at", "expenses", ["created_at"])

    if "internal_consumptions" not in tables:
        op.create_table("internal_consumptions",
            sa.Column("id", sa.UUID(), primary_key=True),
            sa.Column("product_id", sa.UUID(), sa.ForeignKey("products.id"), nullable=True),
            sa.Column("description", sa.String(240), nullable=False),
            sa.Column("quantity", sa.Numeric(12,3), nullable=True),
            sa.Column("unit_cost", sa.Numeric(14,2), nullable=True),
            sa.Column("reference_sale_price", sa.Numeric(14,2), nullable=True),
            sa.Column("cost_total", sa.Numeric(14,2), nullable=False, server_default="0"),
            sa.Column("sale_value_total", sa.Numeric(14,2), nullable=True),
            sa.Column("estimated_value", sa.Numeric(14,2), nullable=True),
            sa.Column("notes", sa.String(500), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("created_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("idempotency_key", sa.String(80), nullable=False, unique=True))
        op.create_index("ix_internal_consumptions_product_id", "internal_consumptions", ["product_id"])
        op.create_index("ix_internal_consumptions_created_at", "internal_consumptions", ["created_at"])

    constraints = {c["name"] for c in sa.inspect(bind).get_check_constraints("cash_movements")}
    if "cashmovementtype" in constraints:
        op.drop_constraint("cashmovementtype", "cash_movements", type_="check")
    if "ck_cash_movements_type" not in constraints:
        op.create_check_constraint("ck_cash_movements_type", "cash_movements",
            "movement_type IN ('SALE','DEPOSIT','WITHDRAWAL','REFUND','ADJUSTMENT','EXPENSE')")
    constraints = {c["name"] for c in sa.inspect(bind).get_check_constraints("inventory_movements")}
    if "inventorymovementtype" in constraints:
        op.drop_constraint("inventorymovementtype", "inventory_movements", type_="check")
    if "ck_inventory_movements_type" not in constraints:
        op.create_check_constraint("ck_inventory_movements_type", "inventory_movements",
            "movement_type IN ('PURCHASE','SALE','RETURN','DAMAGE','ADJUSTMENT','INTERNAL_CONSUMPTION')")


def downgrade():
    op.drop_constraint("ck_inventory_movements_type", "inventory_movements", type_="check")
    op.drop_constraint("ck_cash_movements_type", "cash_movements", type_="check")
    op.drop_table("internal_consumptions"); op.drop_table("expenses")
    op.drop_column("sale_items", "category_code"); op.drop_column("sale_items", "operational_category_id")
    op.drop_column("sales", "idempotency_key"); op.drop_column("sales", "notes"); op.drop_column("sales", "source")
    op.drop_column("products", "operational_category_id")
    op.drop_column("payment_channels", "payment_group")
    op.drop_table("operational_categories")
