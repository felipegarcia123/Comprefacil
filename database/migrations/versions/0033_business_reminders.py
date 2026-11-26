"""Add isolated personal reminders for the opt-in notification center."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0033"
down_revision = "0032"


def upgrade():
    op.create_table(
        "business_reminders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("business_id", sa.String(64), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("idempotency_key", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('PENDING', 'COMPLETED', 'CANCELLED')",
                           name="ck_business_reminders_status"),
        sa.UniqueConstraint("business_id", "user_id", "idempotency_key",
                            name="uq_business_reminders_actor_request"),
    )
    op.create_index("ix_business_reminders_pending", "business_reminders",
                    ["business_id", "user_id", "status", "due_at"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM business_reminders)")).scalar():
        raise RuntimeError("Refusing to discard reminders")
    op.drop_index("ix_business_reminders_pending", table_name="business_reminders")
    op.drop_table("business_reminders")
