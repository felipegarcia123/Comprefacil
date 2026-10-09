"""Manual lines inside a normal (OPEN) sale: sale_items.manual_kind = SUPER | GRANEL | BULTO for a
line without product (product_id NULL). It decides the line's operational category (SUPER /
MASCOTAS_SIN_CODIGO) and its generic Alegra item. No backfill: historical manual sales keep
manual_kind NULL and are still identified by Sale.source (MANUAL_BULK / MANUAL_SACK)."""
from alembic import op
import sqlalchemy as sa


revision = "0025"
down_revision = "0024"


def upgrade():
    op.add_column("sale_items", sa.Column("manual_kind", sa.String(20), nullable=True))


def downgrade():
    op.drop_column("sale_items", "manual_kind")
