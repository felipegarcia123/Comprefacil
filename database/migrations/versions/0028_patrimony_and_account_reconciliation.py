"""Daily patrimony & business account reconciliation (2026-10-07).

- business_accounts: the operational accounts Nequi / Davivienda / Nu / DaviPlata (seeded, idempotent; DaviPlata
  has its own balance, completely separate from Davivienda). Reconciliation
  only: no credentials, no account numbers, no bank API.
- payment_channels.default_account_id / requires_destination_account: a configurable PRESELECTION of where a
  channel's money usually lands (only the unambiguous Nequi -> NEQUI and DaviPlata -> DAVIPLATA, on enabled
  channels) and whether an
  electronic payment must say it. Existing electronic channels start requiring it; the owner can turn it
  off for a channel whose money does not land in these accounts.
- payments.destination_account_id: WHERE the money landed (!= the channel). NULL for cash and for every
  existing payment (no backfill).
- sale_refunds.account_id, sale_exchange_settlements.account_id, payment_corrections.from/to_account_id: the
  account side of 0027's electronic money (existing rows stay NULL). A correction may now change the account
  only (same channel): ck_payment_corrections_change is widened accordingly.
- inventory_valuation_snapshots: the inventory REGISTERED in the POS at cost, frozen at each close (1 per cash
  session) with its coverage (active catalog with stock, active / inactive value). DIAGNOSTIC and "NO VERIFICADA"
  while the official source is MANUAL (is_official false): the catalog was imported without quantities, so it is
  never shown as the store's patrimony. No backfill.
- patrimony_declarations (+ corrections): the value of merchandise DECLARED BY THE OWNER per jornada (optional,
  origin MANUAL), audited corrections, never an UPDATE. patrimony_source_changes: explicit, audited switch of the
  official source (MANUAL by default; AUTOMATIC only after a validated physical inventory).
- business_account_snapshots (+ corrections): the balance the owner saw at the close vs the expected one,
  frozen; a typing mistake is an audited correction, never an UPDATE.
- business_account_movements: explicit external movements of the accounts (OTHER_IN / OTHER_OUT / transfer
  between accounts as two linked rows / REVERSAL).
No change to existing sales, sale_items, payments amounts/channels, inventory_movements, cash_movements or
cash_sessions."""
from uuid import uuid4

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0028"
down_revision = "0027"

UUID = postgresql.UUID(as_uuid=True)
# DaviPlata has its own balance, separate from Davivienda: four independent accounts.
ACCOUNTS = (("NEQUI", "Nequi", 10), ("DAVIVIENDA", "Davivienda", 20), ("NU", "Nu", 30), ("DAVIPLATA", "DaviPlata", 40))
_CHANGE_0027 = "from_channel_id <> to_channel_id"
_CHANGE_0028 = ("from_channel_id <> to_channel_id"
                " OR ((from_account_id IS NULL) <> (to_account_id IS NULL))"
                " OR (from_account_id IS NOT NULL AND to_account_id IS NOT NULL AND from_account_id <> to_account_id)")


def upgrade():
    op.create_table(
        "business_accounts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    bind = op.get_bind()
    for code, name, order in ACCOUNTS:                         # idempotent seed
        bind.execute(sa.text("INSERT INTO business_accounts (id, code, name, active, display_order) "
                             "SELECT CAST(:id AS uuid), CAST(:code AS varchar), CAST(:name AS varchar), true, "
                             "CAST(:order AS integer) WHERE NOT EXISTS "
                             "(SELECT 1 FROM business_accounts WHERE code = CAST(:code AS varchar))"),
                     {"id": uuid4(), "code": code, "name": name, "order": order})

    op.add_column("payment_channels", sa.Column("default_account_id", UUID,
                  sa.ForeignKey("business_accounts.id", name="fk_payment_channels_default_account"), nullable=True))
    op.add_column("payment_channels", sa.Column("requires_destination_account", sa.Boolean(), nullable=False,
                                                server_default=sa.text("false")))
    bind.execute(sa.text("UPDATE payment_channels SET requires_destination_account = true "
                         "WHERE payment_group = 'ELECTRONIC' AND NOT affects_cash"))
    # The two unambiguous defaults, only on ENABLED channels (an inactive channel is never activated here):
    # Nequi -> NEQUI, DaviPlata -> DAVIPLATA. Always a preselection the cashier can change.
    for code, channel in (("NEQUI", "nequi"), ("DAVIPLATA", "daviplata")):
        bind.execute(sa.text("UPDATE payment_channels SET default_account_id = "
                             "(SELECT id FROM business_accounts WHERE code = CAST(:code AS varchar)) "
                             "WHERE lower(name) = CAST(:channel AS varchar) AND active AND NOT affects_cash"),
                     {"code": code, "channel": channel})

    op.add_column("payments", sa.Column("destination_account_id", UUID,
                  sa.ForeignKey("business_accounts.id", name="fk_payments_destination_account"), nullable=True))
    op.create_index("ix_payments_destination_account_id", "payments", ["destination_account_id"])
    op.add_column("sale_refunds", sa.Column("account_id", UUID,
                  sa.ForeignKey("business_accounts.id", name="fk_sale_refunds_account"), nullable=True))
    op.add_column("sale_exchange_settlements", sa.Column("account_id", UUID,
                  sa.ForeignKey("business_accounts.id", name="fk_sale_exchange_settlements_account"), nullable=True))
    op.add_column("payment_corrections", sa.Column("from_account_id", UUID,
                  sa.ForeignKey("business_accounts.id", name="fk_payment_corrections_from_account"), nullable=True))
    op.add_column("payment_corrections", sa.Column("to_account_id", UUID,
                  sa.ForeignKey("business_accounts.id", name="fk_payment_corrections_to_account"), nullable=True))
    op.drop_constraint("ck_payment_corrections_change", "payment_corrections", type_="check")
    op.create_check_constraint("ck_payment_corrections_change", "payment_corrections", _CHANGE_0028)

    op.create_table(
        "inventory_valuation_snapshots",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("cash_session_id", UUID, sa.ForeignKey("cash_sessions.id"), nullable=False, unique=True),
        sa.Column("inventory_value", sa.Numeric(16, 2), nullable=False),
        sa.Column("products_with_positive_stock", sa.Integer(), nullable=False),
        sa.Column("products_valued", sa.Integer(), nullable=False),
        sa.Column("products_missing_cost", sa.Integer(), nullable=False),
        sa.Column("products_negative_stock", sa.Integer(), nullable=False),
        sa.Column("quantity_valued", sa.Numeric(16, 3), nullable=False),
        sa.Column("quantity_missing_cost", sa.Numeric(16, 3), nullable=False),
        sa.Column("cost_source", sa.String(40), nullable=False),
        sa.Column("active_value", sa.Numeric(16, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("inactive_value", sa.Numeric(16, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("active_catalog_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("active_with_stock", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_official", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("inventory_value >= 0", name="ck_inventory_valuation_value"),
    )
    # MANUAL PATRIMONY (the official source until a validated physical inventory exists): the owner's declared
    # value per jornada + audited corrections, and the append-only history of the official source.
    op.create_table(
        "patrimony_declarations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("cash_session_id", UUID, sa.ForeignKey("cash_sessions.id"), nullable=False, unique=True),
        sa.Column("declared_value", sa.Numeric(16, 2), nullable=False),
        sa.Column("source", sa.String(12), nullable=False, server_default=sa.text("'MANUAL'")),
        sa.Column("note", sa.String(300), nullable=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("performed_by_text", sa.String(120), nullable=True),
        sa.Column("declared_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("declared_value >= 0", name="ck_patrimony_declarations_value"),
        sa.CheckConstraint("source IN ('MANUAL')", name="ck_patrimony_declarations_source"),
    )
    op.create_table(
        "patrimony_declaration_corrections",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("declaration_id", UUID, sa.ForeignKey("patrimony_declarations.id"), nullable=False, index=True),
        sa.Column("old_value", sa.Numeric(16, 2), nullable=False),
        sa.Column("new_value", sa.Numeric(16, 2), nullable=False),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(80), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("new_value >= 0", name="ck_patrimony_corrections_value"),
        sa.CheckConstraint("new_value <> old_value", name="ck_patrimony_corrections_change"),
    )
    op.create_table(
        "patrimony_source_changes",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("source", sa.String(12), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("performed_by_text", sa.String(120), nullable=True),
        sa.Column("idempotency_key", sa.String(80), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("source IN ('MANUAL', 'AUTOMATIC')", name="ck_patrimony_source_changes_source"),
    )
    op.create_table(
        "business_account_snapshots",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("cash_session_id", UUID, sa.ForeignKey("cash_sessions.id"), nullable=False, index=True),
        sa.Column("business_account_id", UUID, sa.ForeignKey("business_accounts.id"), nullable=False, index=True),
        sa.Column("observed_balance", sa.Numeric(16, 2), nullable=False),
        sa.Column("is_baseline", sa.Boolean(), nullable=False),
        sa.Column("previous_snapshot_id", UUID, sa.ForeignKey("business_account_snapshots.id"), nullable=True),
        sa.Column("previous_observed_balance", sa.Numeric(16, 2), nullable=True),
        sa.Column("pos_inflows", sa.Numeric(16, 2), nullable=False),
        sa.Column("pos_outflows", sa.Numeric(16, 2), nullable=False),
        sa.Column("registered_external_inflows", sa.Numeric(16, 2), nullable=False),
        sa.Column("registered_external_outflows", sa.Numeric(16, 2), nullable=False),
        sa.Column("expected_balance", sa.Numeric(16, 2), nullable=True),
        sa.Column("difference", sa.Numeric(16, 2), nullable=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(300), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.UniqueConstraint("cash_session_id", "business_account_id", name="uq_business_account_snapshots_session_account"),
        sa.CheckConstraint("observed_balance >= 0", name="ck_business_account_snapshots_observed"),
        sa.CheckConstraint("(is_baseline AND expected_balance IS NULL AND difference IS NULL) OR "
                           "(NOT is_baseline AND expected_balance IS NOT NULL AND difference IS NOT NULL "
                           "AND previous_observed_balance IS NOT NULL)", name="ck_business_account_snapshots_baseline"),
    )
    op.create_table(
        "business_account_snapshot_corrections",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("snapshot_id", UUID, sa.ForeignKey("business_account_snapshots.id"), nullable=False, index=True),
        sa.Column("old_observed_balance", sa.Numeric(16, 2), nullable=False),
        sa.Column("new_observed_balance", sa.Numeric(16, 2), nullable=False),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(80), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("new_observed_balance >= 0", name="ck_business_account_corrections_value"),
        sa.CheckConstraint("new_observed_balance <> old_observed_balance", name="ck_business_account_corrections_change"),
    )
    op.create_table(
        "business_account_movements",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("business_account_id", UUID, sa.ForeignKey("business_accounts.id"), nullable=False, index=True),
        sa.Column("cash_session_id", UUID, sa.ForeignKey("cash_sessions.id"), nullable=False, index=True),
        sa.Column("direction", sa.String(3), nullable=False),
        sa.Column("amount", sa.Numeric(16, 2), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("note", sa.String(300), nullable=True),
        sa.Column("transfer_group_id", UUID, nullable=True, index=True),
        sa.Column("reversal_of_id", UUID, sa.ForeignKey("business_account_movements.id"), nullable=True, unique=True),
        sa.Column("idempotency_key", sa.String(80), nullable=False, unique=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_business_account_movements_amount"),
        sa.CheckConstraint("direction IN ('IN', 'OUT')", name="ck_business_account_movements_direction"),
        sa.CheckConstraint("(reason = 'OTHER_IN' AND direction = 'IN') OR (reason = 'OTHER_OUT' AND direction = 'OUT') "
                           "OR (reason = 'TRANSFER_BETWEEN_ACCOUNTS' AND transfer_group_id IS NOT NULL) "
                           "OR (reason = 'REVERSAL' AND reversal_of_id IS NOT NULL)",
                           name="ck_business_account_movements_reason"),
    )


# Every domain table 0028 creates, plus the history it writes into existing tables. A downgrade is only safe
# when 0028 was applied but NEVER used (same philosophy as 0027). Channel configuration (default account /
# requires flag) and the seeded accounts are configuration, not history.
_TABLES_0028 = ("inventory_valuation_snapshots", "business_account_snapshots", "business_account_snapshot_corrections",
                "business_account_movements", "patrimony_declarations", "patrimony_declaration_corrections",
                "patrimony_source_changes")
_COLUMNS_0028 = (("payments", "destination_account_id IS NOT NULL"),
                 ("sale_refunds", "account_id IS NOT NULL"),
                 ("sale_exchange_settlements", "account_id IS NOT NULL"),
                 ("payment_corrections", "from_account_id IS NOT NULL OR to_account_id IS NOT NULL "
                                         "OR from_channel_id = to_channel_id"))


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text(f"LOCK TABLE {', '.join(_TABLES_0028)} IN ACCESS EXCLUSIVE MODE"))
        bind.execute(sa.text("LOCK TABLE payments, sale_refunds, sale_exchange_settlements, payment_corrections "
                             "IN SHARE MODE"))
    history = {table: bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar() for table in _TABLES_0028}
    for table, condition in _COLUMNS_0028:
        history[f"{table} ({condition})"] = bind.execute(sa.text(f"SELECT count(*) FROM {table} WHERE {condition}")).scalar()
    found = {name: count for name, count in history.items() if count}
    if found:
        detail = ", ".join(f"{name}={count}" for name, count in found.items())
        raise RuntimeError(f"Cannot downgrade 0028: patrimony / account history exists ({detail}). "
                           "Restore the pre-0028 backup or perform a forward migration.")
    op.drop_table("patrimony_source_changes")
    op.drop_table("patrimony_declaration_corrections")
    op.drop_table("patrimony_declarations")
    op.drop_table("business_account_movements")
    op.drop_table("business_account_snapshot_corrections")
    op.drop_table("business_account_snapshots")
    op.drop_table("inventory_valuation_snapshots")
    op.drop_constraint("ck_payment_corrections_change", "payment_corrections", type_="check")
    op.create_check_constraint("ck_payment_corrections_change", "payment_corrections", _CHANGE_0027)
    op.drop_constraint("fk_payment_corrections_to_account", "payment_corrections", type_="foreignkey")
    op.drop_constraint("fk_payment_corrections_from_account", "payment_corrections", type_="foreignkey")
    op.drop_column("payment_corrections", "to_account_id")
    op.drop_column("payment_corrections", "from_account_id")
    op.drop_constraint("fk_sale_exchange_settlements_account", "sale_exchange_settlements", type_="foreignkey")
    op.drop_column("sale_exchange_settlements", "account_id")
    op.drop_constraint("fk_sale_refunds_account", "sale_refunds", type_="foreignkey")
    op.drop_column("sale_refunds", "account_id")
    op.drop_index("ix_payments_destination_account_id", table_name="payments")
    op.drop_constraint("fk_payments_destination_account", "payments", type_="foreignkey")
    op.drop_column("payments", "destination_account_id")
    op.drop_constraint("fk_payment_channels_default_account", "payment_channels", type_="foreignkey")
    op.drop_column("payment_channels", "requires_destination_account")
    op.drop_column("payment_channels", "default_account_id")
    op.drop_table("business_accounts")
