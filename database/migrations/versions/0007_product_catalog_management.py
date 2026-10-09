"""manual product catalog management and initial stock ledger movement"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"


def upgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("products")}
    if "minimum_stock" not in columns:
        op.add_column("products", sa.Column("minimum_stock", sa.Integer(), nullable=False,
                                             server_default="0"))
        bind.execute(sa.text("UPDATE products SET minimum_stock = 5"))
    if "unit" not in columns:
        op.add_column("products", sa.Column("unit", sa.String(40), nullable=False,
                                             server_default="unidad"))
    if "active" not in columns:
        op.add_column("products", sa.Column("active", sa.Boolean(), nullable=False,
                                             server_default=sa.true()))
        op.create_index("ix_products_active", "products", ["active"])
    if "description" not in columns:
        op.add_column("products", sa.Column("description", sa.String(500), nullable=True))
    if "created_at" not in columns:
        op.add_column("products", sa.Column("created_at", sa.DateTime(timezone=True),
                                             nullable=False, server_default=sa.func.now()))
    if "updated_at" not in columns:
        op.add_column("products", sa.Column("updated_at", sa.DateTime(timezone=True),
                                             nullable=False, server_default=sa.func.now()))

    constraints = {constraint["name"] for constraint in
                   sa.inspect(bind).get_check_constraints("inventory_movements")}
    if "inventorymovementtype" in constraints:
        op.drop_constraint("inventorymovementtype", "inventory_movements", type_="check")
    if "ck_inventory_movements_type" in constraints:
        op.drop_constraint("ck_inventory_movements_type", "inventory_movements", type_="check")
    op.create_check_constraint("ck_inventory_movements_type", "inventory_movements",
        "movement_type IN ('INITIAL_STOCK','PURCHASE','SALE','RETURN','DAMAGE','ADJUSTMENT','INTERNAL_CONSUMPTION')")


def downgrade():
    op.drop_constraint("ck_inventory_movements_type", "inventory_movements", type_="check")
    op.create_check_constraint("ck_inventory_movements_type", "inventory_movements",
        "movement_type IN ('PURCHASE','SALE','RETURN','DAMAGE','ADJUSTMENT','INTERNAL_CONSUMPTION')")
    op.drop_column("products", "updated_at")
    op.drop_column("products", "created_at")
    op.drop_column("products", "description")
    op.drop_index("ix_products_active", table_name="products")
    op.drop_column("products", "active")
    op.drop_column("products", "unit")
    op.drop_column("products", "minimum_stock")
