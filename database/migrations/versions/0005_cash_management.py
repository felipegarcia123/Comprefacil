"""cash sessions, auditable movements and close metadata"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"


def _columns(inspector, table):
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    session_columns = _columns(inspector, "cash_sessions")
    movement_columns = _columns(inspector, "cash_movements")

    if "opening_note" not in session_columns:
        op.add_column("cash_sessions", sa.Column("opening_note", sa.String(500), nullable=True))
    if "closed_by" not in session_columns:
        op.add_column("cash_sessions", sa.Column("closed_by", sa.UUID(), nullable=True))
        op.create_foreign_key("fk_cash_sessions_closed_by_users", "cash_sessions", "users", ["closed_by"], ["id"])
    if "closing_note" not in session_columns:
        op.add_column("cash_sessions", sa.Column("closing_note", sa.String(500), nullable=True))
    if "notes" not in movement_columns:
        op.add_column("cash_movements", sa.Column("notes", sa.String(500), nullable=True))

    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("cash_sessions")}
    if "uq_cash_sessions_one_open" not in indexes:
        op.execute(sa.text(
            "CREATE UNIQUE INDEX uq_cash_sessions_one_open ON cash_sessions ((1)) WHERE closed_at IS NULL"))
    movement_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("cash_movements")}
    if "uq_cash_movement_sale_reference" not in movement_indexes:
        op.create_index("uq_cash_movement_sale_reference", "cash_movements", ["reference_id"],
                        unique=True, postgresql_where=sa.text(
                            "movement_type = 'SALE' AND reference_id IS NOT NULL"))


def downgrade():
    op.drop_index("uq_cash_movement_sale_reference", table_name="cash_movements")
    op.drop_index("uq_cash_sessions_one_open", table_name="cash_sessions")
    op.drop_column("cash_movements", "notes")
    op.drop_constraint("fk_cash_sessions_closed_by_users", "cash_sessions", type_="foreignkey")
    op.drop_column("cash_sessions", "closing_note")
    op.drop_column("cash_sessions", "closed_by")
    op.drop_column("cash_sessions", "opening_note")
