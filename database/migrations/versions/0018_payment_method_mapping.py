"""DIAN payment method (Alegra paymentMethod) per channel + card subtype per payment"""
from alembic import op
import sqlalchemy as sa


revision = "0018"
down_revision = "0017"

# Approved mapping (2026-09-28). Card channels (Datáfono) stay NULL: their method
# comes from the Débito/Crédito subtype chosen at checkout.
SEED = {"efectivo": "CASH", "nequi": "CREDIT_TRANSFER", "daviplata": "CREDIT_TRANSFER",
        "bre-b / llave": "CREDIT_TRANSFER", "transferencia": "CREDIT_TRANSFER"}


def upgrade():
    bind = op.get_bind()
    channel_columns = {c["name"] for c in sa.inspect(bind).get_columns("payment_channels")}
    if "alegra_payment_method" not in channel_columns:
        op.add_column("payment_channels", sa.Column("alegra_payment_method", sa.String(40), nullable=True))
    payment_columns = {c["name"] for c in sa.inspect(bind).get_columns("payments")}
    if "card_type" not in payment_columns:
        op.add_column("payments", sa.Column("card_type", sa.String(10), nullable=True))
    for name, method in SEED.items():
        bind.execute(sa.text("UPDATE payment_channels SET alegra_payment_method = :method "
                             "WHERE lower(name) = :name AND payment_type <> 'CARD' AND alegra_payment_method IS NULL"),
                     {"method": method, "name": name})


def downgrade():
    op.drop_column("payments", "card_type")
    op.drop_column("payment_channels", "alegra_payment_method")
