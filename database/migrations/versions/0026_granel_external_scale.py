"""Comida a granel settled by the external scale + scale closing data per cash session (2026-10-05).

1. sales.granel_settlement (NULL, no default): the checkout choice for the sale's GRANEL lines —
   EXTERNAL_SCALE ("Báscula / efectivo") or POS ("Pago electrónico"). NULL = the sale has no granel
   decision (every historical sale): a default would give them a scale meaning they never had.
2. sale_external_items: GRANEL settled by the scale. Part of the customer's bill (total / change),
   never a POS sale line, payment, cash movement or report figure. Audit of what the POS observed.
3. cash_session_scale_closings (1:1 cash_sessions): what the scale reports at its own closing —
   scale_total and scale_digital. Its cash is DERIVED (total - digital), never stored. Reconciliation
   data only: not a payment, cash movement, sale or POS income; expected cash is not changed.
No backfill. Replaces the unreleased draft of 0026 (production was still on 0025)."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0026"
down_revision = "0025"


def upgrade():
    op.add_column("sales", sa.Column("granel_settlement", sa.String(20), nullable=True))
    op.create_table(
        "sale_external_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("sale_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sales.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("settlement", sa.String(20), nullable=False),
        sa.Column("description", sa.String(180), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("unit_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("category_code", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sale_external_items_sale_id", "sale_external_items", ["sale_id"])
    op.create_table(
        "cash_session_scale_closings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("cash_session_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("cash_sessions.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("scale_total", sa.Numeric(14, 2), nullable=False),
        sa.Column("scale_digital", sa.Numeric(14, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("scale_total >= 0", name="ck_scale_total_non_negative"),
        sa.CheckConstraint("scale_digital >= 0", name="ck_scale_digital_non_negative"),
        sa.CheckConstraint("scale_digital <= scale_total", name="ck_scale_digital_le_total"),
    )


def downgrade():
    op.drop_table("cash_session_scale_closings")
    op.drop_table("sale_external_items")
    op.drop_column("sales", "granel_settlement")
