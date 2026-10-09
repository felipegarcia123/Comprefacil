"""customers (Alegra-linked fiscal contacts) + sale/electronic_invoice customer reference"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0016"
down_revision = "0015"


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    if "customers" not in tables:
        op.create_table(
            "customers",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("alegra_contact_id", sa.String(40), nullable=True, unique=True),
            sa.Column("identification_type", sa.String(20), nullable=False),
            sa.Column("identification_number", sa.String(40), nullable=False),
            sa.Column("dv", sa.String(2), nullable=True),
            sa.Column("kind_of_person", sa.String(20), nullable=False, server_default="PERSON_ENTITY"),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("first_name", sa.String(100), nullable=True),
            sa.Column("last_name", sa.String(100), nullable=True),
            sa.Column("email", sa.String(180), nullable=True),
            sa.Column("phone", sa.String(40), nullable=True),
            sa.Column("address", sa.String(240), nullable=True),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("is_final_consumer", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("identification_type", "identification_number", name="uq_customers_identification"),
        )
        op.create_index("ix_customers_identification_number", "customers", ["identification_number"])
        op.create_index("ix_customers_name", "customers", ["name"])
        op.create_index("ix_customers_active", "customers", ["active"])
        op.create_index("uq_customers_final_consumer", "customers", ["is_final_consumer"], unique=True,
                        postgresql_where=sa.text("is_final_consumer"))

    sale_columns = {c["name"] for c in sa.inspect(bind).get_columns("sales")}
    if "customer_id" not in sale_columns:
        op.add_column("sales", sa.Column("customer_id", postgresql.UUID(as_uuid=True),
                                         sa.ForeignKey("customers.id"), nullable=True))
        op.create_index("ix_sales_customer_id", "sales", ["customer_id"])

    invoice_columns = {c["name"] for c in sa.inspect(bind).get_columns("electronic_invoices")}
    if "customer_id" not in invoice_columns:
        op.add_column("electronic_invoices", sa.Column("customer_id", postgresql.UUID(as_uuid=True),
                                                       sa.ForeignKey("customers.id"), nullable=True))
        op.create_index("ix_electronic_invoices_customer_id", "electronic_invoices", ["customer_id"])
    if "customer_snapshot" not in invoice_columns:
        op.add_column("electronic_invoices", sa.Column("customer_snapshot", postgresql.JSONB(), nullable=True))


def downgrade():
    op.drop_column("electronic_invoices", "customer_snapshot")
    op.drop_index("ix_electronic_invoices_customer_id", table_name="electronic_invoices")
    op.drop_column("electronic_invoices", "customer_id")
    op.drop_index("ix_sales_customer_id", table_name="sales")
    op.drop_column("sales", "customer_id")
    op.drop_table("customers")
