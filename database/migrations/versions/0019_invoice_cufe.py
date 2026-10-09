"""electronic invoices: CUFE (DIAN unique code reported by Alegra)"""
from alembic import op
import sqlalchemy as sa


revision = "0019"
down_revision = "0018"


def upgrade():
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("electronic_invoices")}
    if "cufe" not in columns:
        op.add_column("electronic_invoices", sa.Column("cufe", sa.String(120), nullable=True))


def downgrade():
    op.drop_column("electronic_invoices", "cufe")
