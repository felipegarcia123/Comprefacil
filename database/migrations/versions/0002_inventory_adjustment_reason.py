"""add auditable reason to inventory movements"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"


def upgrade():
    # 0001 builds from current metadata, so a brand-new database may already
    # contain the column; existing 0001 databases still need it added.
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("inventory_movements")}
    if "reason" not in columns:
        op.add_column("inventory_movements", sa.Column("reason", sa.String(length=500), nullable=True))


def downgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("inventory_movements")}
    if "reason" in columns:
        op.drop_column("inventory_movements", "reason")
