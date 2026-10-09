"""Electronic invoice is opt-in per sale: Sale.requires_electronic_invoice (the cashier's
"Factura electrónica" checkbox) and Sale.invoice_customer_id (the fiscal recipient, NULL =
Consumidor final), independent of Sale.customer_id. No backfill: every historical sale stays
false/NULL and gets no invoice; existing ElectronicInvoice rows keep working unchanged."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0024"
down_revision = "0023"


def upgrade():
    op.add_column("sales", sa.Column("requires_electronic_invoice", sa.Boolean(), nullable=False,
                                     server_default=sa.false()))
    op.add_column("sales", sa.Column("invoice_customer_id", postgresql.UUID(as_uuid=True),
                                     sa.ForeignKey("customers.id"), nullable=True))


def downgrade():
    op.drop_column("sales", "invoice_customer_id")
    op.drop_column("sales", "requires_electronic_invoice")
