"""Add optional request identity and receipt differences to existing purchases."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0032"
down_revision = "0031"


def upgrade():
    op.add_column("purchases", sa.Column("creation_request_id", UUID(as_uuid=True), nullable=True))
    op.add_column("purchases", sa.Column("creation_request_hash", sa.String(64), nullable=True))
    op.add_column("purchases", sa.Column("receipt_request_id", UUID(as_uuid=True), nullable=True))
    op.add_column("purchases", sa.Column("receipt_request_hash", sa.String(64), nullable=True))
    op.create_unique_constraint("uq_purchases_creation_request_id", "purchases", ["creation_request_id"])
    op.create_unique_constraint("uq_purchases_receipt_request_id", "purchases", ["receipt_request_id"])
    op.add_column("purchase_items", sa.Column("difference_reason", sa.String(500), nullable=True))


def downgrade():
    bind = op.get_bind()
    populated = bind.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM purchases WHERE receipt_request_id IS NOT NULL "
        "OR creation_request_id IS NOT NULL)"
    )).scalar()
    if populated:
        raise RuntimeError("Refusing to discard purchase request identities")
    op.drop_column("purchase_items", "difference_reason")
    op.drop_constraint("uq_purchases_receipt_request_id", "purchases", type_="unique")
    op.drop_constraint("uq_purchases_creation_request_id", "purchases", type_="unique")
    for name in ("receipt_request_hash", "receipt_request_id", "creation_request_hash", "creation_request_id"):
        op.drop_column("purchases", name)
