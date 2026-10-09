"""Physical audit: codes scanned that no Product has (products to create). Never creates Products."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0023"
down_revision = "0022"


def upgrade():
    op.create_table(
        "physical_audit_unknown_codes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("audit_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("physical_audits.id", ondelete="CASCADE"), nullable=False),
        sa.Column("barcode", sa.String(64), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("scan_count", sa.Integer(), nullable=False),
        sa.Column("first_scanned_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_scanned_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("audit_id", "barcode"),
    )
    op.create_index("ix_physical_audit_unknown_codes_audit_id", "physical_audit_unknown_codes", ["audit_id"])


def downgrade():
    op.drop_table("physical_audit_unknown_codes")
