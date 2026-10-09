"""electronic invoicing: electronic_invoices table + Alegra mapping on products"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0014"
down_revision = "0013"


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    product_columns = {c["name"] for c in sa.inspect(bind).get_columns("products")}

    if "alegra_item_id" not in product_columns:
        op.add_column("products", sa.Column("alegra_item_id", sa.String(40), nullable=True))
    if "alegra_tax_id" not in product_columns:
        op.add_column("products", sa.Column("alegra_tax_id", sa.String(40), nullable=True))

    if "electronic_invoices" not in tables:
        op.create_table(
            "electronic_invoices",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("sale_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sales.id"),
                      nullable=False, unique=True),
            sa.Column("provider", sa.String(20), nullable=False, server_default="ALEGRA"),
            sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
            sa.Column("provider_invoice_id", sa.String(60), nullable=True),
            sa.Column("provider_number", sa.String(60), nullable=True),
            sa.Column("provider_status", sa.String(40), nullable=True),
            sa.Column("legal_status", sa.String(40), nullable=True),
            sa.Column("pdf_url", sa.String(500), nullable=True),
            sa.Column("xml_url", sa.String(500), nullable=True),
            sa.Column("alegra_contact_id", sa.String(40), nullable=True),
            sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error_code", sa.String(60), nullable=True),
            sa.Column("error_message", sa.String(500), nullable=True),
            sa.Column("provider_payload", postgresql.JSONB(), nullable=True),
            sa.Column("provider_response", postgresql.JSONB(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.CheckConstraint(
                "status IN ('PENDING','PROCESSING','ISSUED','REJECTED','ERROR')",
                name="ck_electronic_invoices_status"),
        )
        op.create_index("ix_electronic_invoices_sale_id", "electronic_invoices", ["sale_id"], unique=True)
        op.create_index("ix_electronic_invoices_status", "electronic_invoices", ["status"])


def downgrade():
    op.drop_table("electronic_invoices")
    op.drop_column("products", "alegra_tax_id")
    op.drop_column("products", "alegra_item_id")
