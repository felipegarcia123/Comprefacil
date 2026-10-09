"""Physical audit (store walk-through): found products per audit. Never touches inventory."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0022"
down_revision = "0021"


def upgrade():
    op.create_table(
        "physical_audits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "physical_audit_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("audit_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("physical_audits.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("counted", sa.Boolean(), nullable=False),
        sa.Column("scans", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("audit_id", "product_id"),
    )
    op.create_index("ix_physical_audit_items_audit_id", "physical_audit_items", ["audit_id"])
    op.create_index("ix_physical_audit_items_product_id", "physical_audit_items", ["product_id"])


def downgrade():
    op.drop_table("physical_audit_items")
    op.drop_table("physical_audits")
