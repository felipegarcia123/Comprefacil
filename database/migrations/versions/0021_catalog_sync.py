"""products.barcode nullable (products sold without EAN) + POS<->Alegra catalog sync
(unique alegra_item_id, operation audit, stored analysis runs)"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0021"
down_revision = "0020"


def upgrade():
    bind = op.get_bind()
    # The existing UNIQUE index ix_products_barcode stays: PostgreSQL allows many NULLs
    # under a unique index, so uniqueness keeps applying to every real (non-null) code.
    # No existing barcode is modified.
    op.alter_column("products", "barcode", existing_type=sa.String(64), nullable=True)

    duplicates = bind.execute(sa.text(
        "SELECT alegra_item_id FROM products WHERE alegra_item_id IS NOT NULL "
        "GROUP BY alegra_item_id HAVING count(*) > 1")).fetchall()
    if duplicates:
        raise RuntimeError(f"alegra_item_id duplicados, resolver antes de migrar: {[d[0] for d in duplicates]}")
    # One POS product per Alegra item: the database, not only the service, refuses a double link.
    op.create_index("uq_products_alegra_item_id", "products", ["alegra_item_id"], unique=True,
                    postgresql_where=sa.text("alegra_item_id IS NOT NULL"))

    tables = set(sa.inspect(bind).get_table_names())
    if "catalog_sync_operations" not in tables:
        op.create_table(
            "catalog_sync_operations",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=False),
            sa.Column("action", sa.String(20), nullable=False),
            sa.Column("old_alegra_item_id", sa.String(40), nullable=True),
            sa.Column("new_alegra_item_id", sa.String(40), nullable=True),
            sa.Column("reference", sa.String(64), nullable=True),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("error", sa.String(500), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_catalog_sync_operations_product_id", "catalog_sync_operations", ["product_id"])
        op.create_index("ix_catalog_sync_operations_created_at", "catalog_sync_operations", ["created_at"])
    if "catalog_sync_runs" not in tables:
        op.create_table(
            "catalog_sync_runs",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("remote_items", sa.Integer(), nullable=True),
            sa.Column("summary", postgresql.JSONB(), nullable=True),
            sa.Column("rows", postgresql.JSONB(), nullable=True),
            sa.Column("error", sa.String(500), nullable=True),
        )


def downgrade():
    bind = op.get_bind()
    op.drop_table("catalog_sync_runs")
    op.drop_table("catalog_sync_operations")
    op.drop_index("uq_products_alegra_item_id", table_name="products")
    nulls = bind.execute(sa.text("SELECT count(*) FROM products WHERE barcode IS NULL")).scalar()
    if nulls:
        raise RuntimeError(f"{nulls} productos sin código: asignarles código antes de volver a NOT NULL")
    op.alter_column("products", "barcode", existing_type=sa.String(64), nullable=False)
