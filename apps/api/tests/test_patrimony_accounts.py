"""0028 — DAILY PATRIMONY (inventory at cost) & BUSINESS ACCOUNT RECONCILIATION (Nequi / Davivienda / Nu).

SQLite in memory with the fake-Alegra fixture `ctx` of test_customers (nothing reaches the network). Real
PostgreSQL checks (CHECK constraints, row locks, migration up/down) live in test_patrimony_postgres.py and the
disposable-database harness; never against the real database."""
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.domain.enums import InventoryMovementType, PaymentMethod
from app.models import (BusinessAccount, BusinessAccountMovement, BusinessAccountSnapshot,
                        BusinessAccountSnapshotCorrection, CashMovement, CashSession, InventoryMovement,
                        InventoryValuationSnapshot, Payment, PaymentChannel, Product, User)
from app.services.business_accounts import ensure_default_accounts
from app.services.pos import create_sale
from tests.test_customers import API, block1_categories, ctx, recon  # noqa: F401

ZERO = Decimal("0")


# ---- helpers --------------------------------------------------------------------------------------------

def key():
    return f"t-{uuid4().hex}"


@pytest.fixture()
def env(ctx):
    """ctx + the four accounts and the real channel set: Nequi (default NEQUI), Daviplata (default DAVIPLATA, its own
    balance, never Davivienda), Bre-B / Llave, Transferencia and Datáfono require a destination account;
    "Billetera externa" does not (money that lands outside these accounts)."""
    db, client, fake, product, channels, settings = ctx
    ensure_default_accounts(db); db.commit()
    acc = {a.code: a.id for a in db.scalars(select(BusinessAccount))}
    channels["Nequi"].requires_destination_account = True
    channels["Nequi"].default_account_id = acc["NEQUI"]
    channels["Datáfono"].requires_destination_account = True
    for name, requires, default in (("Bre-B / Llave", True, None), ("Transferencia", True, None),
                                    ("Daviplata", True, "DAVIPLATA"), ("Billetera externa", False, None)):
        channel = PaymentChannel(name=name, payment_type=PaymentMethod.TRANSFER, affects_cash=False, display_order=40,
                                 alegra_payment_method="CREDIT_TRANSFER", requires_destination_account=requires,
                                 default_account_id=acc[default] if default else None)
        db.add(channel); channels[name] = channel
    db.commit()
    return {"db": db, "client": client, "product": product, "ch": channels, "acc": acc}


def ch(env, name):
    return str(env["ch"][name].id)


def sell(env, qty=1, *, channel="Efectivo", account=None, received=None, payments=None, product=None, expect=200):
    client, db = env["client"], env["db"]
    product = product or env["product"]
    sale = create_sale(db)
    response = client.post(f"{API}/sales/{sale.id}/items", json={"barcode": product.barcode, "quantity": str(qty)})
    assert response.status_code == 200, response.text
    if payments is not None:
        body = {"payments": payments}
    else:
        body = {"channel_id": ch(env, channel)}
        if env["ch"][channel].affects_cash:
            body["received_amount"] = str(received or Decimal(product.sale_price) * qty)
        if account:
            body["destination_account_id"] = str(env["acc"][account])
    response = client.post(f"{API}/sales/{sale.id}/checkout", json=body)
    assert response.status_code == expect, response.text
    return response.json()


def close(env, counted=None, balances=None, note="Cierre de prueba"):
    client = env["client"]
    current = client.get(f"{API}/cash/current").json()
    expected = current["expected_cash"]
    body = {"counted_amount": str(counted if counted is not None else expected), "expected_amount": expected,
            "note": note}
    if balances:
        body["account_balances"] = [{"account_id": str(env["acc"][code]), "observed_balance": str(value)}
                                    for code, value in balances.items()]
    response = client.post(f"{API}/cash/close", json=body)
    return current["session"]["id"], response


def close_ok(env, **kwargs):
    session_id, response = close(env, **kwargs)
    assert response.status_code == 200, response.text
    return session_id


def reopen(env):
    response = env["client"].post(f"{API}/cash/open", json={"opening_amount": "0", "note": "Nueva jornada"})
    assert response.status_code == 201, response.text


def accounts_of(env, session_id):
    response = env["client"].get(f"{API}/cash/sessions/{session_id}/accounts")
    assert response.status_code == 200, response.text
    return {row["code"]: row for row in response.json()["accounts"]}


def money(value):
    return None if value is None else Decimal(str(value))


def add_product(env, name, price, cost, stock):
    db = env["db"]
    user = db.scalar(select(User))
    product = Product(barcode=f"77{uuid4().int % 10**10:010d}", name=name, sale_price=Decimal(price), unit_cost=Decimal(cost))
    db.add(product); db.flush()
    if stock:
        db.add(InventoryMovement(product_id=product.id, movement_type=InventoryMovementType.PURCHASE,
                                 quantity=Decimal(stock), unit_cost=Decimal(cost), user_id=user.id))
    db.commit()
    return product


def valuation(env):
    response = env["client"].get(f"{API}/inventory-valuation/current")
    assert response.status_code == 200, response.text
    return response.json()


def movement(env, reason, account, amount, to=None, note="Pago de servicio", idem=None, expect=201):
    body = {"reason": reason, "account_id": str(env["acc"][account]), "amount": str(amount), "note": note,
            "idempotency_key": idem or key()}
    if to:
        body["to_account_id"] = str(env["acc"][to])
    response = env["client"].post(f"{API}/business-account-movements", json=body)
    assert response.status_code == expect, response.text
    return response.json()


def ledger(db):
    db.expire_all()
    return (db.scalar(select(func.count()).select_from(Payment)), db.scalar(select(func.count()).select_from(CashMovement)),
            db.scalar(select(func.coalesce(func.sum(CashMovement.amount), 0))),
            db.scalar(select(func.count()).select_from(InventoryMovement)))


# ======================================================================================================
# INVENTORY PATRIMONY
# ======================================================================================================

def test_01_02_03_stock_times_current_cost_over_several_products_and_zero_stock_adds_nothing(env):
    # fixture Arroz: 50 x 6.000 = 300.000
    add_product(env, "Aceite", "12000", "8000", "10")          # 80.000
    add_product(env, "Sal", "2000", "900", "0")                # stock 0: adds nothing, not "missing"
    data = valuation(env)
    assert money(data["inventory_value"]) == Decimal("380000")
    assert data["products_with_positive_stock"] == 2 and data["products_valued"] == 2
    assert data["products_missing_cost"] == 0 and data["complete"] is True
    assert data["verification"] == "NO VERIFICADA" and data["is_official"] is False
    assert data["active_with_stock"] == 2 and money(data["active_value"]) == 380000 and money(data["inactive_value"]) == 0
    assert data["cost_source"] == "PRODUCT_UNIT_COST"
    sell(env, 5)                                               # 45 x 6.000 now
    assert money(valuation(env)["inventory_value"]) == Decimal("350000")


def test_04_negative_stock_is_still_blocked_by_the_existing_rules(env):
    before = valuation(env)
    sell(env, 51, expect=409)                                  # only 50 in stock: checkout refuses
    after = valuation(env)
    assert after["inventory_value"] == before["inventory_value"] and after["products_negative_stock"] == 0


def test_05_positive_stock_without_cost_is_reported_never_valued_at_zero_silently(env):
    add_product(env, "Huevos sin costo", "500", "0", "30")
    data = valuation(env)
    assert money(data["inventory_value"]) == Decimal("300000")         # excluded from the value
    assert data["products_missing_cost"] == 1 and data["complete"] is False
    assert money(data["quantity_missing_cost"]) == 30
    assert "existencias registradas" in data["coverage_message"]
    assert [p["name"] for p in data["missing_cost_products"]] == ["Huevos sin costo"]


def test_06_07_08_snapshot_frozen_at_close_cost_change_later_does_not_touch_it_and_variation(env):
    db, client = env["db"], env["client"]
    first = close_ok(env)
    snapshot = db.scalar(select(InventoryValuationSnapshot).where(InventoryValuationSnapshot.cash_session_id == UUID(first)))
    assert snapshot.inventory_value == Decimal("300000") and snapshot.products_valued == 1
    env["product"].unit_cost = Decimal("9000"); db.commit()                # tomorrow's cost change
    db.expire_all()
    assert db.get(InventoryValuationSnapshot, snapshot.id).inventory_value == Decimal("300000")
    view = client.get(f"{API}/cash/sessions/{first}/inventory-valuation").json()
    assert view["status"] == "SNAPSHOT" and money(view["inventory_value"]) == 300000 and view["is_baseline"] is True
    reopen(env)
    sell(env, 10)                                                         # 40 x 9.000 = 360.000
    second = close_ok(env)
    view = client.get(f"{API}/cash/sessions/{second}/inventory-valuation").json()
    assert money(view["inventory_value"]) == 360000
    assert money(view["previous_value"]) == 300000 and money(view["variation_amount"]) == 60000
    assert money(view["variation_percent"]) == Decimal("20.00")
    # the close is idempotent: closing again is refused, the photo is unique per jornada
    again = client.post(f"{API}/cash/close", json={"counted_amount": "0", "expected_amount": "0"})
    assert again.status_code == 409
    assert db.scalar(select(func.count()).select_from(InventoryValuationSnapshot)) == 2


def test_08b_variation_handles_a_previous_value_of_zero(env):
    from app.services.inventory_valuation import variation
    assert variation(Decimal("100"), Decimal("0")) == {"previous_value": 0, "variation_amount": 100,
                                                       "variation_percent": None}
    assert variation(Decimal("74000000"), Decimal("75000000"))["variation_percent"] == Decimal("-1.33")
    assert variation(Decimal("5"), None)["variation_amount"] is None


def test_09_jornadas_before_0028_appear_without_snapshot_never_as_zero(env):
    db, client = env["db"], env["client"]
    old = db.scalar(select(CashSession))
    from datetime import datetime, timedelta, timezone
    old.closed_at = datetime.now(timezone.utc) - timedelta(seconds=2)       # a jornada closed "before 0028"
    old.closing_expected = old.closing_actual = old.difference = ZERO
    db.commit()
    reopen(env)
    current = close_ok(env)
    rows = client.get(f"{API}/inventory-valuation/history").json()
    by_id = {row["cash_session_id"]: row for row in rows}
    assert by_id[str(old.id)]["status"] == "NO_SNAPSHOT" and by_id[str(old.id)]["inventory_value"] is None
    assert by_id[current]["status"] == "SNAPSHOT" and by_id[current]["is_baseline"] is True
    assert client.get(f"{API}/cash/sessions/{old.id}/inventory-valuation").json()["message"] == "Sin snapshot patrimonial"
    history = {row["session"]["id"]: row for row in client.get(f"{API}/cash/history").json()}
    assert history[str(old.id)]["inventory_value"] is None and money(history[current]["inventory_value"]) == 300000


# ======================================================================================================
# BUSINESS ACCOUNTS
# ======================================================================================================

def test_10_11_first_close_is_baseline_second_uses_previous_observed(env):
    sell(env, 3, channel="Nequi", account="NEQUI")
    first = close_ok(env, balances={"NEQUI": 1000000, "DAVIVIENDA": 5000000, "NU": 2000000})
    rows = accounts_of(env, first)
    assert rows["NEQUI"]["status"] == "CAPTURED" and rows["NEQUI"]["is_baseline"] is True
    assert rows["NEQUI"]["expected_balance"] is None and rows["NEQUI"]["difference"] is None  # no fake difference
    reopen(env)
    sell(env, 2, channel="Nequi", account="NEQUI")              # +20.000
    preview = accounts_of(env, env["client"].get(f"{API}/cash/current").json()["session"]["id"])
    assert money(preview["NEQUI"]["expected_balance"]) == 1020000 and preview["NEQUI"]["status"] == "PENDING"
    second = close_ok(env, balances={"NEQUI": 1020000})
    nequi = accounts_of(env, second)["NEQUI"]
    assert nequi["is_baseline"] is False and money(nequi["previous_observed_balance"]) == 1000000
    assert money(nequi["pos_inflows"]) == 20000 and money(nequi["expected_balance"]) == 1020000
    assert money(nequi["difference"]) == 0


def test_12_13_14_15_destination_is_where_the_money_landed_not_the_channel(env):
    db = env["db"]
    a = sell(env, 1, channel="Nequi", account="NEQUI")
    b = sell(env, 1, channel="Bre-B / Llave", account="DAVIVIENDA")
    c = sell(env, 1, channel="Transferencia", account="NU")
    d = sell(env, 1, channel="Nequi", account="DAVIVIENDA")      # same channel, another account: allowed
    db.expire_all()
    dest = {p.sale_id: p.destination_account_id for p in db.scalars(select(Payment))}
    acc = env["acc"]
    assert [dest[UUID(x["id"])] for x in (a, b, c, d)] == [acc["NEQUI"], acc["DAVIVIENDA"], acc["NU"], acc["DAVIVIENDA"]]


def test_15b_a_channel_that_requires_an_account_refuses_to_charge_without_it_and_an_untracked_one_does_not(env):
    response = sell(env, 1, channel="Bre-B / Llave", expect=422)
    assert "Elige a qué cuenta llegó el dinero de Bre-B / Llave" in response["detail"]
    sell(env, 1, channel="Billetera externa")                    # not a tracked account: no destination invented


def test_16_cash_never_has_a_destination_account(env):
    response = sell(env, 1, channel="Efectivo", account="NEQUI", expect=422)
    assert "El efectivo no llega a una cuenta" in response["detail"]
    sell(env, 1)
    env["db"].expire_all()
    assert env["db"].scalar(select(Payment)).destination_account_id is None


def test_17_split_payments_keep_one_destination_each(env):
    acc = env["acc"]
    sale = sell(env, 3, payments=[
        {"channel_id": ch(env, "Efectivo"), "amount": "10000", "received_amount": "10000"},
        {"channel_id": ch(env, "Nequi"), "amount": "10000", "destination_account_id": str(acc["NEQUI"])},
        {"channel_id": ch(env, "Bre-B / Llave"), "amount": "10000", "destination_account_id": str(acc["NU"])}])
    env["db"].expire_all()
    got = {p.payment_channel_id: p.destination_account_id
           for p in env["db"].scalars(select(Payment).where(Payment.sale_id == UUID(sale["id"])))}
    assert got == {env["ch"]["Efectivo"].id: None, env["ch"]["Nequi"].id: acc["NEQUI"],
                   env["ch"]["Bre-B / Llave"].id: acc["NU"]}


def test_18_19_20_external_movements_and_transfers(env):
    close_ok(env, balances={"NEQUI": 1000000, "DAVIVIENDA": 5000000, "NU": 0})
    reopen(env)
    sell(env, 30, channel="Nequi", account="NEQUI")                       # +300.000
    movement(env, "OTHER_OUT", "NEQUI", 100000)                           # -100.000
    movement(env, "OTHER_IN", "NU", 50000, note="Abono de un socio")      # +50.000 Nu
    rows = movement(env, "TRANSFER_BETWEEN_ACCOUNTS", "NEQUI", 500000, to="DAVIVIENDA", note="")
    assert sorted((r["account_name"], r["direction"]) for r in rows) == [("Davivienda", "IN"), ("Nequi", "OUT")]
    assert rows[0]["transfer_group_id"] == rows[1]["transfer_group_id"]
    session_id = env["client"].get(f"{API}/cash/current").json()["session"]["id"]
    view = accounts_of(env, session_id)
    assert money(view["NEQUI"]["expected_balance"]) == 1000000 + 300000 - 100000 - 500000
    assert money(view["DAVIVIENDA"]["expected_balance"]) == 5500000
    assert money(view["NU"]["expected_balance"]) == 50000
    # a transfer is not income: across the accounts it nets to zero
    net = sum(money(v["registered_external_inflows"]) - money(v["registered_external_outflows"]) for v in view.values())
    assert net == -100000 + 50000


def test_21_22_23_difference_zero_negative_positive(env):
    close_ok(env, balances={"NEQUI": 1000000, "DAVIVIENDA": 1000000, "NU": 1000000})
    reopen(env)
    sell(env, 1, channel="Nequi", account="NEQUI")
    sell(env, 1, channel="Bre-B / Llave", account="DAVIVIENDA")
    sell(env, 1, channel="Transferencia", account="NU")
    second = close_ok(env, balances={"NEQUI": 1010000, "DAVIVIENDA": 990000, "NU": 1030000})
    rows = accounts_of(env, second)
    assert money(rows["NEQUI"]["difference"]) == 0
    assert money(rows["DAVIVIENDA"]["difference"]) == -20000
    assert money(rows["NU"]["difference"]) == 20000


def test_24_25_account_differences_never_touch_expected_cash_the_cash_difference_or_block1(env):
    db, client = env["db"], env["client"]
    sell(env, 2)                                                           # cash 20.000
    sell(env, 1, channel="Nequi", account="NEQUI")
    session_id = client.get(f"{API}/cash/current").json()["session"]["id"]
    block1_before = recon(client, session_id)
    expected_before = client.get(f"{API}/cash/current").json()["expected_cash"]
    ledger_before = ledger(db)
    close_ok(env, counted=20000, balances={"NEQUI": 999999})              # a wild account difference
    db.expire_all()
    session = db.get(CashSession, UUID(session_id))
    assert session.closing_expected == Decimal(expected_before) and session.difference == 0
    assert ledger(db) == ledger_before                                     # no payment / cash movement / stock row
    block1_after = recon(client, session_id)
    assert block1_after["pos_cash_subtotal"] == block1_before["pos_cash_subtotal"]


def test_pending_reconciliation_can_be_completed_after_the_close(env):
    sell(env, 1, channel="Nequi", account="NEQUI")
    session_id = close_ok(env)                                             # no balances typed
    view = env["client"].get(f"{API}/cash/sessions/{session_id}/accounts").json()
    assert view["status"] == "PENDING" and all(r["status"] == "PENDING" for r in view["accounts"])
    response = env["client"].put(f"{API}/cash/sessions/{session_id}/account-balances",
                                 json={"balances": [{"account_id": str(env["acc"]["NEQUI"]), "observed_balance": "50000"}]})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "PARTIAL"
    again = env["client"].put(f"{API}/cash/sessions/{session_id}/account-balances",
                              json={"balances": [{"account_id": str(env["acc"]["NEQUI"]), "observed_balance": "1"}]})
    assert again.status_code == 409 and "Corregir saldo registrado" in again.json()["detail"]


def test_negative_or_invalid_balances_are_refused_before_closing(env):
    client = env["client"]
    current = client.get(f"{API}/cash/current").json()
    response = client.post(f"{API}/cash/close", json={
        "counted_amount": current["expected_cash"], "expected_amount": current["expected_cash"],
        "account_balances": [{"account_id": str(env["acc"]["NEQUI"]), "observed_balance": "-5"}]})
    assert response.status_code == 422
    assert client.get(f"{API}/cash/current").json() is not None           # the jornada is still open


# ======================================================================================================
# POST-SALE (0027) + ACCOUNTS
# ======================================================================================================

def detail(env, sale_id):
    return env["client"].get(f"{API}/sales/{sale_id}/detail").json()


def test_26_27_electronic_refund_leaves_the_right_account_only_when_completed(env):
    client = env["client"]
    close_ok(env, balances={"NEQUI": 0, "DAVIVIENDA": 1000000, "NU": 0})
    reopen(env)
    sale = sell(env, 2, channel="Bre-B / Llave", account="DAVIVIENDA")     # +20.000 Davivienda
    item = detail(env, sale["id"])["lines"][0]["id"]
    response = client.post(f"{API}/sales/{sale['id']}/returns", json={
        "lines": [{"sale_item_id": item, "quantity": "1", "restock": True}], "reason_code": "CUSTOMER_RETURN",
        "idempotency_key": key()})
    assert response.status_code == 201, response.text
    refund = response.json()["pending_refunds"][0]
    assert refund["account_name"] == "Davivienda"                         # default: where that money landed
    session_id = client.get(f"{API}/cash/current").json()["session"]["id"]
    assert money(accounts_of(env, session_id)["DAVIVIENDA"]["pos_outflows"]) == 0      # PENDING never leaves
    assert money(accounts_of(env, session_id)["DAVIVIENDA"]["pending_outflows"]) == 10000
    client.post(f"{API}/sales/{sale['id']}/refunds/{refund['id']}/confirm", json={})
    view = accounts_of(env, session_id)["DAVIVIENDA"]
    assert money(view["pos_outflows"]) == 10000 and money(view["expected_balance"]) == 1010000


def test_26b_a_refund_whose_account_is_unknown_asks_for_it_at_confirmation(env):
    client, db = env["client"], env["db"]
    sale = sell(env, 1, channel="Billetera externa")                       # untracked channel, no account
    db.get(PaymentChannel, env["ch"]["Billetera externa"].id).requires_destination_account = True; db.commit()
    item = detail(env, sale["id"])["lines"][0]["id"]
    client.post(f"{API}/sales/{sale['id']}/returns", json={
        "lines": [{"sale_item_id": item, "quantity": "1", "restock": True}], "reason_code": "CUSTOMER_RETURN",
        "idempotency_key": key()})
    refund = detail(env, sale["id"])["pending_refunds"][0]
    assert refund["account_id"] is None and refund["requires_account"] is True
    missing = client.post(f"{API}/sales/{sale['id']}/refunds/{refund['id']}/confirm", json={})
    assert missing.status_code == 422 and "de qué cuenta salió" in missing.json()["detail"]
    ok = client.post(f"{API}/sales/{sale['id']}/refunds/{refund['id']}/confirm",
                     json={"account_id": str(env["acc"]["NU"])})
    assert ok.status_code == 200 and ok.json()["returns"][0]["refunds"][0]["account_name"] == "Nu"


def exchange(env, sale_id, replacement_amount, settlement=None):
    item = detail(env, sale_id)["lines"][0]["id"]
    body = {"return_lines": [{"sale_item_id": item, "quantity": "1", "restock": True}],
            "replacement_lines": [{"manual_kind": "SUPER", "description": "Otro producto", "amount": str(replacement_amount)}],
            "reason_code": "WRONG_SIZE", "idempotency_key": key(), "settlement": settlement}
    return env["client"].post(f"{API}/sales/{sale_id}/exchanges", json=body)


def test_28_customer_pays_the_difference_electronically_into_the_right_account(env):
    close_ok(env, balances={"NEQUI": 0, "DAVIVIENDA": 0, "NU": 100000})
    reopen(env)
    sale = sell(env, 1)                                                    # cash sale 10.000
    missing = exchange(env, sale["id"], 18000, {"channel_id": ch(env, "Transferencia")})
    assert missing.status_code == 422 and "Elige a qué cuenta" in missing.json()["detail"]
    ok = exchange(env, sale["id"], 18000, {"channel_id": ch(env, "Transferencia"), "account_id": str(env["acc"]["NU"])})
    assert ok.status_code == 201, ok.text
    assert ok.json()["exchanges"][0]["money"]["account_name"] == "Nu"
    session_id = env["client"].get(f"{API}/cash/current").json()["session"]["id"]
    nu = accounts_of(env, session_id)["NU"]
    assert money(nu["breakdown"]["exchange_differences_in"]) == 8000 and money(nu["expected_balance"]) == 108000


def test_29_store_refunds_the_difference_electronically_only_counts_when_completed(env):
    client = env["client"]
    close_ok(env, balances={"NEQUI": 500000, "DAVIVIENDA": 0, "NU": 0})
    reopen(env)
    sale = sell(env, 1, channel="Nequi", account="NEQUI")                 # +10.000
    response = exchange(env, sale["id"], 4000, {"channel_id": ch(env, "Nequi"), "account_id": str(env["acc"]["NEQUI"])})
    assert response.status_code == 201, response.text
    exchange_id = response.json()["exchanges"][0]["id"]
    session_id = client.get(f"{API}/cash/current").json()["session"]["id"]
    assert money(accounts_of(env, session_id)["NEQUI"]["expected_balance"]) == 510000     # PENDING: not yet
    client.post(f"{API}/sales/{sale['id']}/exchanges/{exchange_id}/refund/confirm", json={})
    nequi = accounts_of(env, session_id)["NEQUI"]
    assert money(nequi["breakdown"]["exchange_differences_out"]) == 6000 and money(nequi["expected_balance"]) == 504000


def correct(env, sale_id, payment_id, channel, account=None, expect=201):
    body = {"to_channel_id": ch(env, channel), "reason": "Se marcó mal", "idempotency_key": key()}
    if account:
        body["to_account_id"] = str(env["acc"][account])
    response = env["client"].post(f"{API}/sales/{sale_id}/payments/{payment_id}/correct", json=body)
    assert response.status_code == expect, response.text
    return response.json()


def test_30_31_payment_correction_between_channels_and_accounts_is_reflected_exactly_once(env):
    close_ok(env, balances={"NEQUI": 0, "DAVIVIENDA": 0, "NU": 0})
    reopen(env)
    sale = sell(env, 5, channel="Nequi", account="NEQUI")                 # 50.000 into Nequi
    payment = detail(env, sale["id"])["payments"][0]["id"]
    # same channel, another account (the money really landed in Davivienda)
    out = correct(env, sale["id"], payment, "Nequi", "DAVIVIENDA")
    assert out["payments"][0]["effective_account_name"] == "Davivienda"
    correct(env, sale["id"], payment, "Nequi", "DAVIVIENDA", expect=409)  # nothing to change
    # then the channel too, keeping the account
    correct(env, sale["id"], payment, "Bre-B / Llave")
    session_id = env["client"].get(f"{API}/cash/current").json()["session"]["id"]
    view = accounts_of(env, session_id)
    assert money(view["NEQUI"]["expected_balance"]) == 0                  # +50.000 - 50.000
    assert money(view["DAVIVIENDA"]["expected_balance"]) == 50000         # +50.000 once, never twice
    assert money(view["NU"]["expected_balance"]) == 0


def test_30b_a_correction_to_cash_takes_the_money_out_of_the_account_and_into_the_drawer(env):
    close_ok(env, balances={"NEQUI": 0, "DAVIVIENDA": 0, "NU": 0})
    reopen(env)
    sale = sell(env, 2, channel="Nequi", account="NEQUI")
    payment = detail(env, sale["id"])["payments"][0]["id"]
    correct(env, sale["id"], payment, "Efectivo")
    session_id = env["client"].get(f"{API}/cash/current").json()["session"]["id"]
    assert money(accounts_of(env, session_id)["NEQUI"]["expected_balance"]) == 0
    assert money(env["client"].get(f"{API}/cash/current").json()["expected_cash"]) == 20000


def test_32_0027_payments_without_account_are_never_assigned_one_retroactively(env):
    db = env["db"]
    sale = sell(env, 1, channel="Billetera externa")                       # like a payment from before 0028
    payment = detail(env, sale["id"])["payments"][0]["id"]
    refused = correct(env, sale["id"], payment, "Nequi", "NEQUI", expect=422)
    assert "anterior al control de cuentas" in refused["detail"]
    ok = correct(env, sale["id"], payment, "Nequi")                       # channel only: account stays unknown
    assert ok["payments"][0]["effective_account_id"] is None
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(BusinessAccountSnapshot)) == 0


def test_33_no_fake_backfill_existing_payments_and_jornadas_stay_without_account_or_snapshot(env):
    db = env["db"]
    user = db.scalar(select(User))
    from datetime import datetime, timezone
    legacy = CashSession(opening_amount=ZERO, opened_by=user.id, opened_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                         closed_at=datetime(2026, 9, 1, 12, tzinfo=timezone.utc), closing_expected=ZERO,
                         closing_actual=ZERO, difference=ZERO)
    db.add(legacy); db.commit()
    assert env["client"].get(f"{API}/cash/sessions/{legacy.id}/inventory-valuation").json()["status"] == "NO_SNAPSHOT"
    cells = env["client"].get(f"{API}/business-accounts/history").json()["sessions"]
    assert all(cell["status"] == "NONE" for row in cells for cell in row["accounts"])


def test_34_35_snapshots_are_immutable_and_a_typing_mistake_is_an_audited_correction(env):
    db, client = env["db"], env["client"]
    first = close_ok(env, balances={"NEQUI": 6000000, "DAVIVIENDA": 0, "NU": 0})      # meant 600.000
    snapshot = accounts_of(env, first)["NEQUI"]
    url = f"{API}/business-account-snapshots/{snapshot['snapshot_id']}/corrections"
    idem = key()
    response = client.post(url, json={"new_observed_balance": "600000", "reason": "Se digitó un cero de más",
                                      "idempotency_key": idem})
    assert response.status_code == 201, response.text
    nequi = {r["code"]: r for r in response.json()["accounts"]}["NEQUI"]
    assert money(nequi["observed_balance"]) == 600000 and money(nequi["original_observed_balance"]) == 6000000
    assert [(money(c["old_observed_balance"]), money(c["new_observed_balance"])) for c in nequi["corrections"]] == [(6000000, 600000)]
    assert client.post(url, json={"new_observed_balance": "600000", "reason": "Se digitó un cero de más",
                                  "idempotency_key": idem}).status_code == 201       # idempotent
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(BusinessAccountSnapshotCorrection)) == 1
    assert db.get(BusinessAccountSnapshot, UUID(snapshot["snapshot_id"])).observed_balance == 6000000
    # the next jornada starts from the corrected balance
    reopen(env)
    second = close_ok(env, balances={"NEQUI": 600000})
    assert money(accounts_of(env, second)["NEQUI"]["previous_observed_balance"]) == 600000
    # once a later balance used it, the older one can no longer be corrected
    late = client.post(url, json={"new_observed_balance": "1", "reason": "tarde", "idempotency_key": key()})
    assert late.status_code == 409


def test_external_movement_reversal_and_window_rules(env):
    client = env["client"]
    rows = movement(env, "OTHER_OUT", "NEQUI", 100000)
    response = client.post(f"{API}/business-account-movements/{rows[0]['id']}/reverse",
                           json={"reason": "Lo registré dos veces", "idempotency_key": key()})
    assert response.status_code == 201 and response.json()[0]["direction"] == "IN"
    twice = client.post(f"{API}/business-account-movements/{rows[0]['id']}/reverse",
                        json={"reason": "Otra vez", "idempotency_key": key()})
    assert twice.status_code == 409
    transfer = movement(env, "TRANSFER_BETWEEN_ACCOUNTS", "NEQUI", 50000, to="NU", note="")
    back = client.post(f"{API}/business-account-movements/{transfer[0]['id']}/reverse",
                       json={"reason": "Transferencia equivocada", "idempotency_key": key()}).json()
    assert sorted((r["account_name"], r["direction"]) for r in back) == [("Nequi", "IN"), ("Nu", "OUT")]
    session_id = client.get(f"{API}/cash/current").json()["session"]["id"]
    view = accounts_of(env, session_id)
    assert all(money(v["registered_external_inflows"]) == money(v["registered_external_outflows"]) for v in view.values())
    movement(env, "TRANSFER_BETWEEN_ACCOUNTS", "NU", 1, to="NU", note="", expect=422)
    # after the close with Nequi's balance registered, a new Nequi movement must wait for the next jornada
    close_ok(env, balances={"NEQUI": 0})
    late = movement(env, "OTHER_OUT", "NEQUI", 1000, expect=409)
    assert "abre la caja" in late["detail"]
    movement(env, "OTHER_OUT", "NU", 1000)                                # Nu still pending: allowed


# ======================================================================================================
# DAVIPLATA — a fourth account with its own balance (never Davivienda)
# ======================================================================================================

FOUR = {"NEQUI": 1000000, "DAVIVIENDA": 5000000, "NU": 2000000, "DAVIPLATA": 300000}


def test_d01_seed_is_idempotent_and_has_four_independent_accounts(env):
    db = env["db"]
    ensure_default_accounts(db); ensure_default_accounts(db); db.commit()
    codes = sorted(a.code for a in db.scalars(select(BusinessAccount)))
    assert codes == ["DAVIPLATA", "DAVIVIENDA", "NEQUI", "NU"]
    assert len(set(env["acc"].values())) == 4 and env["acc"]["DAVIPLATA"] != env["acc"]["DAVIVIENDA"]
    names = [a["name"] for a in env["client"].get(f"{API}/business-accounts").json()]
    assert names == ["Nequi", "Davivienda", "Nu", "DaviPlata"]


def test_d02_daviplata_payment_goes_to_daviplata_or_to_the_account_chosen(env):
    db = env["db"]
    a = sell(env, 1, channel="Daviplata", account="DAVIPLATA")
    b = sell(env, 1, channel="Daviplata", account="NEQUI")              # money really landed in Nequi
    db.expire_all()
    dest = {p.sale_id: p.destination_account_id for p in db.scalars(select(Payment))}
    assert dest[UUID(a["id"])] == env["acc"]["DAVIPLATA"] and dest[UUID(b["id"])] == env["acc"]["NEQUI"]
    assert sell(env, 1, channel="Daviplata", expect=422)["detail"].startswith("Elige a qué cuenta")


def test_d03_d05_d06_four_baselines_then_four_expected_balances_independent_davivienda_and_daviplata(env):
    sell(env, 1, channel="Daviplata", account="DAVIPLATA")
    first = close_ok(env, balances=FOUR)
    rows = accounts_of(env, first)
    assert sorted(rows) == ["DAVIPLATA", "DAVIVIENDA", "NEQUI", "NU"]
    assert all(r["status"] == "CAPTURED" and r["is_baseline"] and r["difference"] is None for r in rows.values())
    reopen(env)
    sell(env, 2, channel="Daviplata", account="DAVIPLATA")               # +20.000 DaviPlata only
    sell(env, 1, channel="Bre-B / Llave", account="DAVIVIENDA")          # +10.000 Davivienda only
    second = close_ok(env, balances={"NEQUI": 1000000, "DAVIVIENDA": 5010000, "NU": 2000000, "DAVIPLATA": 320000})
    rows = accounts_of(env, second)
    assert {code: money(r["expected_balance"]) for code, r in rows.items()} == {
        "NEQUI": 1000000, "DAVIVIENDA": 5010000, "NU": 2000000, "DAVIPLATA": 320000}
    assert all(money(r["difference"]) == 0 for r in rows.values())
    view = env["client"].get(f"{API}/cash/sessions/{second}/accounts").json()
    assert view["status"] == "COMPLETE" and view["verified"] is True


def test_d04_transfer_daviplata_to_davivienda_and_back_never_creates_money(env):
    close_ok(env, balances=FOUR)
    reopen(env)
    movement(env, "TRANSFER_BETWEEN_ACCOUNTS", "DAVIPLATA", 100000, to="DAVIVIENDA", note="")
    movement(env, "TRANSFER_BETWEEN_ACCOUNTS", "DAVIVIENDA", 40000, to="DAVIPLATA", note="")
    session_id = env["client"].get(f"{API}/cash/current").json()["session"]["id"]
    view = accounts_of(env, session_id)
    assert money(view["DAVIPLATA"]["expected_balance"]) == 300000 - 100000 + 40000
    assert money(view["DAVIVIENDA"]["expected_balance"]) == 5000000 + 100000 - 40000
    assert sum(money(v["expected_balance"]) for v in view.values()) == sum(FOUR.values())   # nothing created


def test_d07_electronic_refund_from_daviplata(env):
    client = env["client"]
    close_ok(env, balances=FOUR)
    reopen(env)
    sale = sell(env, 2, channel="Daviplata", account="DAVIPLATA")
    item = detail(env, sale["id"])["lines"][0]["id"]
    refund = client.post(f"{API}/sales/{sale['id']}/returns", json={
        "lines": [{"sale_item_id": item, "quantity": "1", "restock": True}], "reason_code": "CUSTOMER_RETURN",
        "idempotency_key": key()}).json()["pending_refunds"][0]
    assert refund["account_name"] == "DaviPlata"
    client.post(f"{API}/sales/{sale['id']}/refunds/{refund['id']}/confirm", json={})
    session_id = client.get(f"{API}/cash/current").json()["session"]["id"]
    view = accounts_of(env, session_id)
    assert money(view["DAVIPLATA"]["breakdown"]["refunds_out"]) == 10000
    assert money(view["DAVIPLATA"]["expected_balance"]) == 300000 + 20000 - 10000
    assert money(view["DAVIVIENDA"]["expected_balance"]) == 5000000                # never confused with Davivienda


def test_d08_exchange_difference_collected_into_daviplata(env):
    close_ok(env, balances=FOUR)
    reopen(env)
    sale = sell(env, 1)
    ok = exchange(env, sale["id"], 16000, {"channel_id": ch(env, "Daviplata"), "account_id": str(env["acc"]["DAVIPLATA"])})
    assert ok.status_code == 201, ok.text
    assert ok.json()["exchanges"][0]["money"]["account_name"] == "DaviPlata"
    session_id = env["client"].get(f"{API}/cash/current").json()["session"]["id"]
    assert money(accounts_of(env, session_id)["DAVIPLATA"]["breakdown"]["exchange_differences_in"]) == 6000


def test_d09_split_payment_with_different_accounts_including_daviplata(env):
    acc = env["acc"]
    sale = sell(env, 4, payments=[
        {"channel_id": ch(env, "Daviplata"), "amount": "10000", "destination_account_id": str(acc["DAVIPLATA"])},
        {"channel_id": ch(env, "Bre-B / Llave"), "amount": "20000", "destination_account_id": str(acc["DAVIVIENDA"])},
        {"channel_id": ch(env, "Efectivo"), "amount": "10000", "received_amount": "10000"}])
    env["db"].expire_all()
    got = {p.payment_channel_id: p.destination_account_id
           for p in env["db"].scalars(select(Payment).where(Payment.sale_id == UUID(sale["id"])))}
    assert got == {env["ch"]["Daviplata"].id: acc["DAVIPLATA"], env["ch"]["Bre-B / Llave"].id: acc["DAVIVIENDA"],
                   env["ch"]["Efectivo"].id: None}


def test_d10_history_and_reports_show_four_accounts(env):
    close_ok(env, balances=FOUR)
    history = env["client"].get(f"{API}/business-accounts/history").json()
    assert [a["name"] for a in history["accounts"]] == ["Nequi", "Davivienda", "Nu", "DaviPlata"]
    assert len(history["sessions"][0]["accounts"]) == 4
    rows = {r["session"]["id"]: r for r in env["client"].get(f"{API}/cash/history").json()}
    newest = history["sessions"][0]["cash_session_id"]
    assert rows[newest]["accounts_registered"] == 4 and rows[newest]["accounts_total"] == 4


def test_d11_electronic_money_without_account_is_flagged_and_never_verified(env):
    sell(env, 1, channel="Billetera externa")                             # electronic, no destination account
    session_id = close_ok(env, balances=FOUR)
    view = env["client"].get(f"{API}/cash/sessions/{session_id}/accounts").json()
    assert view["status"] == "COMPLETE" and view["verified"] is False
    assert view["unclassified"]["count"] == 1 and money(view["unclassified"]["amount"]) == 10000
    assert "Billetera externa" in view["unclassified"]["items"][0]["label"]
    # never attributed by guessing: neither Davivienda nor DaviPlata received it
    assert all(money(r["pos_inflows"]) == 0 for r in view["accounts"])


# ======================================================================================================
# MANUAL PATRIMONY — the owner's declared value is the official source; the POS valuation is NO VERIFICADA
# ======================================================================================================

def close_declaring(env, value=None, note=None, balances=None):
    client = env["client"]
    current = client.get(f"{API}/cash/current").json()
    body = {"counted_amount": current["expected_cash"], "expected_amount": current["expected_cash"]}
    if value is not None:
        body["declared_patrimony"] = {"value": str(value), "note": note}
    if balances:
        body["account_balances"] = [{"account_id": str(env["acc"][c]), "observed_balance": str(v)} for c, v in balances.items()]
    response = client.post(f"{API}/cash/close", json=body)
    assert response.status_code == 200, response.text
    return current["session"]["id"]


def declaration(env, session_id):
    response = env["client"].get(f"{API}/cash/sessions/{session_id}/patrimony-declaration")
    assert response.status_code == 200, response.text
    return response.json()


def test_p01_close_without_declared_patrimony_never_blocks(env):
    from app.models import PatrimonyDeclaration
    session_id = close_declaring(env)
    assert declaration(env, session_id)["status"] == "NOT_DECLARED"
    assert env["db"].scalar(select(func.count()).select_from(PatrimonyDeclaration)) == 0


def test_p02_p03_close_with_manual_patrimony_and_variation_between_consecutive_declarations(env):
    first = close_declaring(env, 75000000, "Estimado del dueño")
    data = declaration(env, first)
    assert data["status"] == "DECLARED" and data["source"] == "MANUAL" and money(data["declared_value"]) == 75000000
    assert data["is_first"] is True and data["variation_amount"] is None and data["note"] == "Estimado del dueño"
    reopen(env)
    close_declaring(env)                                       # a day without declaration: nothing invented
    reopen(env)
    third = close_declaring(env, 74000000)
    data = declaration(env, third)
    assert money(data["previous_value"]) == 75000000 and money(data["variation_amount"]) == -1000000
    assert money(data["variation_percent"]) == Decimal("-1.33")
    history = env["client"].get(f"{API}/patrimony/history").json()
    assert [row["declared"] and money(row["declared"]["declared_value"]) for row in history["sessions"][:3]] == [74000000, None, 75000000]


def test_p04_declare_after_close_and_audited_correction(env):
    client, db = env["client"], env["db"]
    session_id = close_declaring(env)
    response = client.put(f"{API}/cash/sessions/{session_id}/patrimony-declaration", json={"value": "7400000"})
    assert response.status_code == 200 and money(response.json()["declared_value"]) == 7400000
    again = client.put(f"{API}/cash/sessions/{session_id}/patrimony-declaration", json={"value": "1"})
    assert again.status_code == 409                                       # never overwritten silently
    declaration_id = response.json()["id"]
    idem = key()
    fixed = client.post(f"{API}/patrimony-declarations/{declaration_id}/corrections",
                        json={"new_value": "74000000", "reason": "Faltaba un cero", "idempotency_key": idem})
    assert fixed.status_code == 201, fixed.text
    data = fixed.json()
    assert money(data["declared_value"]) == 74000000 and money(data["original_value"]) == 7400000
    assert [(money(c["old_value"]), money(c["new_value"]), c["reason"]) for c in data["corrections"]] == [(7400000, 74000000, "Faltaba un cero")]
    client.post(f"{API}/patrimony-declarations/{declaration_id}/corrections",
                json={"new_value": "74000000", "reason": "Faltaba un cero", "idempotency_key": idem})
    from app.models import PatrimonyDeclaration, PatrimonyDeclarationCorrection
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(PatrimonyDeclarationCorrection)) == 1     # idempotent
    assert db.get(PatrimonyDeclaration, UUID(declaration_id)).declared_value == Decimal("7400000.00")
    history = {r["session"]["id"]: r for r in client.get(f"{API}/cash/history").json()}
    assert money(history[session_id]["declared_patrimony"]) == 74000000


def test_p05_incomplete_automatic_valuation_is_never_the_official_patrimony(env):
    client = env["client"]
    session_id = close_declaring(env, 50000000)
    auto = client.get(f"{API}/cash/sessions/{session_id}/inventory-valuation").json()
    assert auto["verification"] == "NO VERIFICADA" and auto["is_official"] is False
    source = client.get(f"{API}/patrimony/source").json()
    assert source["source"] == "MANUAL" and source["confirmed_physical_counts"] == 0
    refused = client.post(f"{API}/patrimony/source", json={"source": "AUTOMATIC", "reason": "Quiero usar el POS ya",
                                                           "idempotency_key": key()})
    assert refused.status_code == 409 and "inventario físico confirmado" in refused.json()["detail"]
    history = client.get(f"{API}/patrimony/history").json()
    row = history["sessions"][0]
    assert money(row["declared"]["declared_value"]) == 50000000 and row["automatic"]["is_official"] is False


def test_p06_switch_to_automatic_is_explicit_audited_and_never_compares_across_the_transition(env):
    from app.models import InventoryMovement as IM
    client, db = env["client"], env["db"]
    first = close_declaring(env, 60000000)                                # diagnostic photo (MANUAL era)
    db.add(IM(product_id=env["product"].id, movement_type=InventoryMovementType.ADJUSTMENT, quantity=Decimal("1"),
              unit_cost=Decimal("6000"), reference_id=uuid4(), reason="Conteo físico", user_id=db.scalar(select(User)).id))
    db.commit()                                                          # stands for a confirmed count batch
    changed = client.post(f"{API}/patrimony/source", json={"source": "AUTOMATIC", "idempotency_key": key(),
                                                           "reason": "Inventario físico inicial validado el 08/10"})
    assert changed.status_code == 201 and changed.json()["source"] == "AUTOMATIC"
    reopen(env)
    second = close_declaring(env)
    view = client.get(f"{API}/cash/sessions/{second}/inventory-valuation").json()
    assert view["is_official"] is True and view["verification"] == "OFICIAL"
    assert view["variation_amount"] is None and view["is_baseline"] is True       # no variation across bases
    assert client.get(f"{API}/cash/sessions/{first}/inventory-valuation").json()["is_official"] is False
    changes = client.get(f"{API}/patrimony/source").json()["changes"]
    assert [c["source"] for c in changes] == ["AUTOMATIC"] and "validado" in changes[0]["reason"]


def test_p07_demo_products_are_never_deleted_or_altered(env):
    db = env["db"]
    demo = add_product(env, "Producto demo inactivo", "10000", "5000", "40")
    demo.active = False; db.commit()
    before = (demo.unit_cost, demo.active, ledger(db))
    close_declaring(env, 1000000)
    db.expire_all()
    product = db.get(Product, demo.id)
    assert (product.unit_cost, product.active, ledger(db)) == before
    session = env["client"].get(f"{API}/inventory-valuation/history").json()[0]
    assert money(session["inactive_value"]) == 200000                  # still visible, split apart, untouched


def test_p08_four_accounts_still_work_with_the_manual_patrimony(env):
    session_id = close_declaring(env, 9000000, balances=FOUR)
    view = env["client"].get(f"{API}/cash/sessions/{session_id}/accounts").json()
    assert view["status"] == "COMPLETE" and sorted(r["code"] for r in view["accounts"]) == ["DAVIPLATA", "DAVIVIENDA", "NEQUI", "NU"]
    assert declaration(env, session_id)["status"] == "DECLARED"


def test_p09_invalid_declared_value_refused_before_closing(env):
    client = env["client"]
    current = client.get(f"{API}/cash/current").json()
    response = client.post(f"{API}/cash/close", json={"counted_amount": current["expected_cash"],
                                                      "expected_amount": current["expected_cash"],
                                                      "declared_patrimony": {"value": "-1"}})
    assert response.status_code == 422 and client.get(f"{API}/cash/current").json() is not None
