"""electronic invoices: BLOCKED status (pre-flight) + reconciliation guard columns"""
from alembic import op
import sqlalchemy as sa


revision = "0017"
down_revision = "0016"

OLD_STATUSES = "'PENDING','PROCESSING','ISSUED','REJECTED','ERROR'"
NEW_STATUSES = "'PENDING','BLOCKED','PROCESSING','ISSUED','REJECTED','ERROR'"


def upgrade():
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("electronic_invoices")}
    checks = {c["name"] for c in sa.inspect(bind).get_check_constraints("electronic_invoices")}
    if "ck_electronic_invoices_status" in checks:
        op.drop_constraint("ck_electronic_invoices_status", "electronic_invoices", type_="check")
    op.create_check_constraint("ck_electronic_invoices_status", "electronic_invoices", f"status IN ({NEW_STATUSES})")
    if "needs_reconciliation" not in columns:
        op.add_column("electronic_invoices", sa.Column("needs_reconciliation", sa.Boolean(), nullable=False,
                                                       server_default=sa.text("false")))
    if "last_attempt_at" not in columns:
        op.add_column("electronic_invoices", sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True))
    if "attempt_count" not in columns:
        op.add_column("electronic_invoices", sa.Column("attempt_count", sa.Integer(), nullable=False,
                                                       server_default=sa.text("0")))


def downgrade():
    op.execute("UPDATE electronic_invoices SET status = 'ERROR' WHERE status = 'BLOCKED'")
    op.drop_constraint("ck_electronic_invoices_status", "electronic_invoices", type_="check")
    op.create_check_constraint("ck_electronic_invoices_status", "electronic_invoices", f"status IN ({OLD_STATUSES})")
    op.drop_column("electronic_invoices", "attempt_count")
    op.drop_column("electronic_invoices", "last_attempt_at")
    op.drop_column("electronic_invoices", "needs_reconciliation")
