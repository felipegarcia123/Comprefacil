"""sale cancellation audit: who/when an OPEN sale was cancelled (status CANCELLED already exists, varchar(9))"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0015"
down_revision = "0014"


def upgrade():
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("sales")}
    if "cancelled_at" not in columns:
        op.add_column("sales", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    if "cancelled_by" not in columns:
        op.add_column("sales", sa.Column("cancelled_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True))


def downgrade():
    op.drop_column("sales", "cancelled_by")
    op.drop_column("sales", "cancelled_at")
