"""supplier payments and semantic cash movement"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"


def upgrade():
    bind = op.get_bind()
    # A dedicated generic transfer channel keeps provider payments distinct from wallets.
    bind.execute(sa.text("""INSERT INTO payment_channels
        (id, name, payment_type, active, affects_cash, payment_group, display_order, created_at, updated_at)
        VALUES ('10000000-0000-0000-0000-000000000007', 'Transferencia', 'TRANSFER', true, false, 'ELECTRONIC', 15, now(), now())
        ON CONFLICT (name) DO NOTHING"""))
    op.create_table("supplier_payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("purchase_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("purchases.id"), nullable=False),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("payment_channel_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("payment_channels.id"), nullable=False),
        sa.Column("reference", sa.String(120)), sa.Column("notes", sa.String(500)),
        sa.Column("paid_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.UniqueConstraint("purchase_id", name="uq_supplier_payments_purchase_id"))
    op.create_index("ix_supplier_payments_purchase_id", "supplier_payments", ["purchase_id"])
    op.create_index("ix_supplier_payments_supplier_id", "supplier_payments", ["supplier_id"])
    op.create_index("ix_supplier_payments_payment_channel_id", "supplier_payments", ["payment_channel_id"])
    op.create_index("ix_supplier_payments_paid_at", "supplier_payments", ["paid_at"])


def downgrade():
    op.drop_table("supplier_payments")
