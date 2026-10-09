"""payment channels and mixed-payment compatible sale payments"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"

CHANNELS = [
    ("10000000-0000-0000-0000-000000000001", "Efectivo", "CASH", True, True, 10),
    ("10000000-0000-0000-0000-000000000002", "Nequi", "TRANSFER", True, False, 20),
    ("10000000-0000-0000-0000-000000000003", "Daviplata", "TRANSFER", True, False, 30),
    ("10000000-0000-0000-0000-000000000004", "Bre-B / Llave", "TRANSFER", True, False, 40),
    ("10000000-0000-0000-0000-000000000005", "Datáfono", "CARD", True, False, 50),
    ("10000000-0000-0000-0000-000000000006", "Transferencia histórica", "TRANSFER", False, False, 999),
]


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "payment_channels" not in inspector.get_table_names():
        op.create_table(
            "payment_channels",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("name", sa.String(120), nullable=False, unique=True),
            sa.Column("payment_type", sa.String(8), nullable=False),
            sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("affects_cash", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("display_order", sa.Integer(), server_default="0", nullable=False),
            sa.Column("description", sa.String(300), nullable=True),
            sa.Column("provider", sa.String(120), nullable=True),
            sa.Column("account_reference", sa.String(120), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.CheckConstraint("payment_type IN ('CASH','CARD','TRANSFER')", name="paymentmethod"),
        )
        op.create_index("ix_payment_channels_payment_type", "payment_channels", ["payment_type"])
        op.create_index("ix_payment_channels_active", "payment_channels", ["active"])

    for channel in CHANNELS:
        bind.execute(sa.text("""
            INSERT INTO payment_channels
              (id, name, payment_type, active, affects_cash, display_order, created_at, updated_at)
            VALUES (:id, :name, :payment_type, :active, :affects_cash, :display_order, now(), now())
            ON CONFLICT (name) DO NOTHING
        """), dict(zip(("id", "name", "payment_type", "active", "affects_cash", "display_order"), channel)))

    inspector = sa.inspect(bind)
    payment_columns = {column["name"]: column for column in inspector.get_columns("payments")}
    if "payment_channel_id" not in payment_columns:
        op.add_column("payments", sa.Column("payment_channel_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key("fk_payments_channel", "payments", "payment_channels",
                              ["payment_channel_id"], ["id"])
        op.create_index("ix_payments_payment_channel_id", "payments", ["payment_channel_id"])
    if "reference" not in payment_columns:
        op.add_column("payments", sa.Column("reference", sa.String(120), nullable=True))

    ids = {row[2]: row[0] for row in CHANNELS if row[1] in ("Efectivo", "Datáfono")}
    ids["TRANSFER"] = CHANNELS[-1][0]
    for method, channel_id in ids.items():
        bind.execute(sa.text("""
            UPDATE payments SET payment_channel_id = :channel_id
            WHERE payment_channel_id IS NULL AND method = :method
        """), {"channel_id": channel_id, "method": method})
    missing = bind.scalar(sa.text("SELECT count(*) FROM payments WHERE payment_channel_id IS NULL"))
    if missing:
        raise RuntimeError("No fue posible asignar canal a todos los pagos históricos")
    columns = {column["name"]: column for column in sa.inspect(bind).get_columns("payments")}
    if columns["payment_channel_id"]["nullable"]:
        op.alter_column("payments", "payment_channel_id", nullable=False)


def downgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("payments")}
    if "reference" in columns:
        op.drop_column("payments", "reference")
    if "payment_channel_id" in columns:
        op.drop_column("payments", "payment_channel_id")
    if "payment_channels" in sa.inspect(bind).get_table_names():
        op.drop_table("payment_channels")
