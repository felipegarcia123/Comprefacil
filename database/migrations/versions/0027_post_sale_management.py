"""Post-sale management (2026-10-06): immutable history + append-only compensating operations.

- sale_notes: append-only clarifications (never change money, stock, invoice, sale or payment).
- sale_returns (+ sale_return_lines): PARTIAL / TOTAL / VOID returns of a COMPLETED sale. The sale,
  its lines, payments and original movements are never modified. One VOID per sale (DB index).
- sale_refunds: the money of a return, per channel. Cash = paid out at once (CashMovement REFUND,
  status COMPLETED); electronic = PENDING until the cashier confirms the external transfer.
- payment_corrections: append-only chain of channel corrections per payment (sequence 1..n). The
  payment keeps its original channel; the effective channel is the last correction's to_channel.
- sale_exchanges (+ return lines, replacement lines, settlement money): PRODUCT EXCHANGE, the normal
  post-sale case of the business ("devuelve A, se lleva B"). The original sale is never touched: a
  returned unit gives back the value it was DELIVERED with as credit, the replacement uses the current
  price, and only the signed balance (replacement - credit) moves money: EVEN (0, nothing), CUSTOMER_PAYS
  (> 0, cash -> CashMovement EXCHANGE +) or STORE_REFUNDS (< 0, cash -> CashMovement EXCHANGE_REFUND -,
  electronic -> PENDING until confirmed). Never a refund of the whole credit plus a new sale.
  Inventory: RETURN (+) for a restocked returned unit, EXCHANGE_OUT (-) for a delivered catalog product.
- Provenance: every return / exchange line consumes units of exactly ONE source, the original sale line
  (sale_item_id) or a unit delivered by an earlier exchange (source_replacement_line_id). That makes
  chained exchanges (A -> B -> C) and money returns of exchanged units possible without a new sale.
No change to existing rows of sales, sale_items, payments, inventory_movements or cash_movements; no backfill."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0027"
down_revision = "0026"

UUID = postgresql.UUID(as_uuid=True)


# cash_movements.movement_type is guarded by a CHECK (migrations 0006/0011): it must accept the new
# PAYMENT_CORRECTION (cash effect of a payment channel correction) or every such correction fails.
_TYPES_0026 = "'SALE','DEPOSIT','WITHDRAWAL','REFUND','ADJUSTMENT','EXPENSE','SUPPLIER_PAYMENT'"
_TYPES_0027 = _TYPES_0026 + ",'PAYMENT_CORRECTION','EXCHANGE','EXCHANGE_REFUND'"
# inventory_movements.movement_type CHECK (0006/0007): EXCHANGE_OUT = a product delivered as a replacement
# in an exchange (kept apart from SALE so reports never count it as a new sale).
_INV_0026 = "'INITIAL_STOCK','PURCHASE','SALE','RETURN','DAMAGE','ADJUSTMENT','INTERNAL_CONSUMPTION'"
_INV_0027 = _INV_0026 + ",'EXCHANGE_OUT'"


def upgrade():
    op.drop_constraint("ck_cash_movements_type", "cash_movements", type_="check")
    op.create_check_constraint("ck_cash_movements_type", "cash_movements", f"movement_type IN ({_TYPES_0027})")
    op.drop_constraint("ck_inventory_movements_type", "inventory_movements", type_="check")
    op.create_check_constraint("ck_inventory_movements_type", "inventory_movements", f"movement_type IN ({_INV_0027})")
    op.create_table(
        "sale_notes",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("sale_id", UUID, sa.ForeignKey("sales.id"), nullable=False, index=True),
        sa.Column("text", sa.String(500), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("performed_by_text", sa.String(120), nullable=True),
        sa.Column("idempotency_key", sa.String(80), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "sale_returns",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("sale_id", UUID, sa.ForeignKey("sales.id"), nullable=False, index=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("reason_code", sa.String(40), nullable=False),
        sa.Column("reason_text", sa.String(500), nullable=True),
        sa.Column("total_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("cash_session_id", UUID, sa.ForeignKey("cash_sessions.id"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("performed_by_text", sa.String(120), nullable=True),
        sa.Column("idempotency_key", sa.String(80), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('PARTIAL', 'TOTAL', 'VOID')", name="ck_sale_returns_kind"),
        sa.CheckConstraint("total_amount >= 0", name="ck_sale_returns_total"),
    )
    op.create_index("uq_sale_returns_one_void", "sale_returns", ["sale_id"], unique=True,
                    postgresql_where=sa.text("kind = 'VOID'"), sqlite_where=sa.text("kind = 'VOID'"))
    op.create_table(
        "sale_return_lines",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("return_id", UUID, sa.ForeignKey("sale_returns.id"), nullable=False, index=True),
        sa.Column("sale_item_id", UUID, sa.ForeignKey("sale_items.id"), nullable=True, index=True),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("unit_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("category_code", sa.String(64), nullable=False),
        sa.Column("restock", sa.Boolean(), nullable=False),
        sa.Column("inventory_movement_id", UUID, sa.ForeignKey("inventory_movements.id"), nullable=True, unique=True),
        sa.CheckConstraint("quantity > 0", name="ck_sale_return_lines_quantity"),
    )
    op.create_table(
        "sale_refunds",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("return_id", UUID, sa.ForeignKey("sale_returns.id"), nullable=False, index=True),
        sa.Column("payment_id", UUID, sa.ForeignKey("payments.id"), nullable=True),
        sa.Column("channel_id", UUID, sa.ForeignKey("payment_channels.id"), nullable=False),
        sa.Column("affects_cash", sa.Boolean(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("cash_movement_id", UUID, sa.ForeignKey("cash_movements.id"), nullable=True, unique=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("confirmed_by_text", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('PENDING', 'COMPLETED')", name="ck_sale_refunds_status"),
        sa.CheckConstraint("amount > 0", name="ck_sale_refunds_amount"),
    )
    op.create_table(
        "payment_corrections",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("sale_id", UUID, sa.ForeignKey("sales.id"), nullable=False, index=True),
        sa.Column("payment_id", UUID, sa.ForeignKey("payments.id"), nullable=False, index=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("from_channel_id", UUID, sa.ForeignKey("payment_channels.id"), nullable=False),
        sa.Column("to_channel_id", UUID, sa.ForeignKey("payment_channels.id"), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("cash_session_id", UUID, sa.ForeignKey("cash_sessions.id"), nullable=True),
        sa.Column("cash_movement_id", UUID, sa.ForeignKey("cash_movements.id"), nullable=True, unique=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("performed_by_text", sa.String(120), nullable=True),
        sa.Column("idempotency_key", sa.String(80), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("payment_id", "sequence", name="uq_payment_corrections_sequence"),
        sa.CheckConstraint("from_channel_id <> to_channel_id", name="ck_payment_corrections_change"),
    )
    op.create_table(
        "sale_exchanges",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("sale_id", UUID, sa.ForeignKey("sales.id"), nullable=False, index=True),
        sa.Column("reason_code", sa.String(40), nullable=False),
        sa.Column("reason_text", sa.String(500), nullable=True),
        sa.Column("returned_credit", sa.Numeric(14, 2), nullable=False),
        sa.Column("replacement_total", sa.Numeric(14, 2), nullable=False),
        # Signed balance: > 0 the customer pays, = 0 nobody pays, < 0 the store gives the difference back.
        sa.Column("difference", sa.Numeric(14, 2), nullable=False),
        sa.Column("settlement", sa.String(16), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("cash_session_id", UUID, sa.ForeignKey("cash_sessions.id"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("performed_by_text", sa.String(120), nullable=True),
        sa.Column("idempotency_key", sa.String(80), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('COMPLETED')", name="ck_sale_exchanges_status"),
        sa.CheckConstraint("returned_credit > 0 AND replacement_total > 0", name="ck_sale_exchanges_amounts"),
        sa.CheckConstraint("difference = replacement_total - returned_credit", name="ck_sale_exchanges_difference"),
        sa.CheckConstraint("(settlement = 'EVEN' AND difference = 0) OR (settlement = 'CUSTOMER_PAYS' AND difference > 0)"
                           " OR (settlement = 'STORE_REFUNDS' AND difference < 0)", name="ck_sale_exchanges_settlement"),
    )
    op.create_table(
        "sale_exchange_replacement_lines",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("exchange_id", UUID, sa.ForeignKey("sale_exchanges.id"), nullable=False, index=True),
        sa.Column("product_id", UUID, sa.ForeignKey("products.id"), nullable=True),
        sa.Column("manual_kind", sa.String(20), nullable=True),
        sa.Column("product_name", sa.String(180), nullable=False),
        sa.Column("category_code", sa.String(64), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("unit_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("unit_cost", sa.Numeric(14, 2), nullable=False),
        sa.Column("line_total", sa.Numeric(14, 2), nullable=False),
        sa.Column("inventory_movement_id", UUID, sa.ForeignKey("inventory_movements.id"), nullable=True, unique=True),
        sa.CheckConstraint("quantity > 0", name="ck_sale_exchange_replacement_lines_quantity"),
        sa.CheckConstraint("(product_id IS NULL) <> (manual_kind IS NULL)", name="ck_sale_exchange_replacement_lines_kind"),
    )
    op.create_table(
        "sale_exchange_return_lines",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("exchange_id", UUID, sa.ForeignKey("sale_exchanges.id"), nullable=False, index=True),
        sa.Column("sale_item_id", UUID, sa.ForeignKey("sale_items.id"), nullable=True, index=True),
        sa.Column("source_replacement_line_id", UUID, sa.ForeignKey("sale_exchange_replacement_lines.id"),
                  nullable=True, index=True),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("unit_credit", sa.Numeric(14, 2), nullable=False),
        sa.Column("line_credit", sa.Numeric(14, 2), nullable=False),
        sa.Column("category_code", sa.String(64), nullable=False),
        sa.Column("restock", sa.Boolean(), nullable=False),
        sa.Column("inventory_movement_id", UUID, sa.ForeignKey("inventory_movements.id"), nullable=True, unique=True),
        sa.CheckConstraint("quantity > 0", name="ck_sale_exchange_return_lines_quantity"),
        sa.CheckConstraint("(sale_item_id IS NULL) <> (source_replacement_line_id IS NULL)",
                           name="ck_sale_exchange_return_lines_source"),
    )
    op.create_table(
        "sale_exchange_settlements",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("exchange_id", UUID, sa.ForeignKey("sale_exchanges.id"), nullable=False, unique=True),
        # IN = the customer paid the difference; OUT = the store gave the difference back.
        sa.Column("direction", sa.String(3), nullable=False),
        sa.Column("payment_channel_id", UUID, sa.ForeignKey("payment_channels.id"), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("affects_cash", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("received_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("change_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("card_type", sa.String(10), nullable=True),
        sa.Column("reference", sa.String(120), nullable=True),
        sa.Column("cash_movement_id", UUID, sa.ForeignKey("cash_movements.id"), nullable=True, unique=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("confirmed_by_text", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_sale_exchange_settlements_amount"),
        sa.CheckConstraint("direction IN ('IN', 'OUT')", name="ck_sale_exchange_settlements_direction"),
        sa.CheckConstraint("status IN ('PENDING', 'COMPLETED')", name="ck_sale_exchange_settlements_status"),
        # Only money given back electronically waits for a confirmation.
        sa.CheckConstraint("status = 'COMPLETED' OR (direction = 'OUT' AND NOT affects_cash)",
                           name="ck_sale_exchange_settlements_pending"),
    )
    # A money return may also give back units delivered by an exchange (provenance).
    op.add_column("sale_return_lines", sa.Column("source_replacement_line_id", UUID,
                  sa.ForeignKey("sale_exchange_replacement_lines.id", name="fk_sale_return_lines_replacement"),
                  nullable=True))
    op.create_index("ix_sale_return_lines_source_replacement_line_id", "sale_return_lines",
                    ["source_replacement_line_id"])
    op.create_check_constraint("ck_sale_return_lines_source", "sale_return_lines",
                               "(sale_item_id IS NULL) <> (source_replacement_line_id IS NULL)")

# Every domain table 0027 creates. A downgrade is only safe when 0027 was applied but NEVER used.
_TABLES_0027 = ("sale_notes", "sale_returns", "sale_return_lines", "sale_refunds", "payment_corrections",
                "sale_exchanges", "sale_exchange_replacement_lines", "sale_exchange_return_lines",
                "sale_exchange_settlements")


def downgrade():
    # Never delete history to make a downgrade fit. The source of truth is the 0027 tables themselves
    # (REFUND / RETURN existed before 0027, so a return leaves no new movement type); the movement types
    # only 0027 writes are an extra guard. Locked first so nothing slips in between the check and the DROP.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text(f"LOCK TABLE {', '.join(_TABLES_0027)} IN ACCESS EXCLUSIVE MODE"))
        bind.execute(sa.text("LOCK TABLE cash_movements, inventory_movements IN SHARE MODE"))
    history = {table: bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar() for table in _TABLES_0027}
    history["cash_movements (PAYMENT_CORRECTION/EXCHANGE/EXCHANGE_REFUND)"] = bind.execute(sa.text(
        "SELECT count(*) FROM cash_movements WHERE movement_type IN "
        "('PAYMENT_CORRECTION', 'EXCHANGE', 'EXCHANGE_REFUND')")).scalar()
    history["inventory_movements (EXCHANGE_OUT)"] = bind.execute(sa.text(
        "SELECT count(*) FROM inventory_movements WHERE movement_type = 'EXCHANGE_OUT'")).scalar()
    found = {name: count for name, count in history.items() if count}
    if found:
        detail = ", ".join(f"{name}={count}" for name, count in found.items())
        raise RuntimeError(f"Cannot downgrade 0027: post-sale history exists ({detail}). "
                           "Restore the pre-0027 backup or perform a forward migration.")
    op.drop_constraint("ck_cash_movements_type", "cash_movements", type_="check")
    op.create_check_constraint("ck_cash_movements_type", "cash_movements", f"movement_type IN ({_TYPES_0026})")
    op.drop_constraint("ck_inventory_movements_type", "inventory_movements", type_="check")
    op.create_check_constraint("ck_inventory_movements_type", "inventory_movements", f"movement_type IN ({_INV_0026})")
    op.drop_constraint("ck_sale_return_lines_source", "sale_return_lines", type_="check")
    op.drop_index("ix_sale_return_lines_source_replacement_line_id", table_name="sale_return_lines")
    op.drop_constraint("fk_sale_return_lines_replacement", "sale_return_lines", type_="foreignkey")
    op.drop_column("sale_return_lines", "source_replacement_line_id")
    op.drop_table("sale_exchange_settlements")
    op.drop_table("sale_exchange_return_lines")
    op.drop_table("sale_exchange_replacement_lines")
    op.drop_table("sale_exchanges")
    op.drop_table("payment_corrections")
    op.drop_table("sale_refunds")
    op.drop_table("sale_return_lines")
    op.drop_index("uq_sale_returns_one_void", table_name="sale_returns")
    op.drop_table("sale_returns")
    op.drop_table("sale_notes")
