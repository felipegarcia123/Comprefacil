"""manual supplier material-code equivalences for receipt drafts

This migration stores review decisions only. It does not alter inventory,
purchases, prices, sales, payments or cash movements.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0030"
down_revision = "0029"

UUID = postgresql.UUID(as_uuid=True)


def upgrade():
    op.create_table(
        "receipt_equivalences",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("provider", sa.String(120), nullable=False),
        sa.Column("material_code", sa.String(64), nullable=False),
        sa.Column("product_id", UUID, sa.ForeignKey("products.id"), nullable=False),
        sa.Column("product_name_snapshot", sa.String(180), nullable=False),
        sa.Column("source_description", sa.String(240), nullable=True),
        sa.Column("source_brand", sa.String(120), nullable=True),
        sa.Column("source_variant", sa.String(120), nullable=True),
        sa.Column("source_size", sa.String(80), nullable=True),
        sa.Column("confirmation_method", sa.String(40), nullable=False, server_default="manual"),
        sa.Column("confirmed_by", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("deactivated_by", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_receipt_equivalences_provider", "receipt_equivalences", ["provider"])
    op.create_index("ix_receipt_equivalences_material_code", "receipt_equivalences", ["material_code"])
    op.create_index("ix_receipt_equivalences_product_id", "receipt_equivalences", ["product_id"])
    op.create_index("ix_receipt_equivalences_active", "receipt_equivalences", ["active"])
    op.create_index("uq_receipt_equivalences_active_code", "receipt_equivalences",
                    ["provider", "material_code"], unique=True,
                    postgresql_where=sa.text("active"))


def downgrade():
    op.drop_index("uq_receipt_equivalences_active_code", table_name="receipt_equivalences")
    op.drop_index("ix_receipt_equivalences_active", table_name="receipt_equivalences")
    op.drop_index("ix_receipt_equivalences_product_id", table_name="receipt_equivalences")
    op.drop_index("ix_receipt_equivalences_material_code", table_name="receipt_equivalences")
    op.drop_index("ix_receipt_equivalences_provider", table_name="receipt_equivalences")
    op.drop_table("receipt_equivalences")
