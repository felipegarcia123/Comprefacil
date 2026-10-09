"""PRODUCT EXCHANGES (0027): "devuelve A, se lleva B" — the business's usual post-sale case.

Fake data only (fixture `ctx` of test_customers: fake Alegra, nothing reaches the network). Real row-lock
concurrency lives in test_post_sale_postgres.py (run against a disposable COPY of the database)."""
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.domain.enums import CashMovementType, ElectronicInvoiceStatus, InventoryMovementType, PaymentMethod
from app.models import (CashMovement, ElectronicInvoice, InventoryMovement, Payment, PaymentChannel, Product, Sale,
                        SaleExchange, SaleExchangeSettlement, SaleReturn, User)
from app.services.inventory import current_stock
from app.services.pos import create_sale
from tests.test_customers import (API, add_manual, block1_categories, cash, ctx, manual_categories, nequi,  # noqa: F401
                                  recon, sell)
from tests.test_post_sale import (cash_total, count, detail, do_correct, do_return, do_void, key, lines_of,
                                  payment_of, summary)

ZERO = Decimal("0")


# ---- helpers ----------------------------------------------------------------------------------------------

def product(db, name, price, stock=20, barcode=None):
    item = Product(barcode=barcode, name=name, sale_price=Decimal(price), unit_cost=Decimal(price) / 2)
    db.add(item); db.flush()
    db.add(InventoryMovement(product_id=item.id, movement_type=InventoryMovementType.PURCHASE, quantity=Decimal(stock),
                             unit_cost=item.unit_cost, user_id=db.scalar(select(User.id))))
    db.commit()
    return item


@pytest.fixture()
def shop(ctx):
    db, client, fake, arroz, channels, settings = ctx
    made = {"A": product(db, "Concentrado A", 30000, barcode="8801"), "B": product(db, "Concentrado B", 38000, barcode="8802"),
            "C": product(db, "Concentrado C", 30000, barcode="8803"), "D": product(db, "Concentrado D", 25000, barcode="8804"),
            "arroz": arroz}
    return ctx, made


def sell_a(ctx, made, qty=1, pay=None):
    db, client, fake, arroz, channels, settings = ctx
    return sell(client, db, channels, [("scan", made["A"], qty)], pay or cash(30000 * qty))


def exchange(client, sale_id, returned, replacements, *, payment=None, reason="CHANGED_MIND", idem=None, text=None):
    """returned: [(sale_item_id | ("R", replacement_line_id), qty, restock)]; replacements: [{"product_id"|...}].
    `payment` = the settlement of the difference (customer pays it, or the store gives it back)."""
    def source(i):
        return {"replacement_line_id": str(i[1])} if isinstance(i, tuple) else {"sale_item_id": str(i)}
    body = {"return_lines": [{**source(i), "quantity": str(q), "restock": r} for i, q, r in returned],
            "replacement_lines": replacements, "reason_code": reason, "reason_text": text,
            "idempotency_key": idem or key()}
    if payment:
        body["settlement"] = payment
    return client.post(f"{API}/sales/{sale_id}/exchanges", json=body)


def item_of(client, sale_id, index=0):
    return lines_of(client, sale_id)[index]["id"]


def take(prod, qty=1):
    return {"product_id": str(prod.id), "quantity": str(qty)}


def movements(db, kind, product_id=None):
    db.expire_all()
    stmt = select(func.coalesce(func.sum(InventoryMovement.quantity), 0)).where(InventoryMovement.movement_type == kind)
    if product_id is not None:
        stmt = stmt.where(InventoryMovement.product_id == product_id)
    return Decimal(db.scalar(stmt))


# ---- 1-7. same value ---------------------------------------------------------------------------------------

def test_01_07_same_value_exchange_moves_products_and_no_money(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    sale = sell_a(ctx, made)
    stock_a, stock_c = current_stock(db, made["A"].id), current_stock(db, made["C"].id)
    payments, cash_movements, cash_before = count(db, Payment), count(db, CashMovement), cash_total(db)
    response = exchange(client, sale.id, [(item_of(client, sale.id), 1, True)], [take(made["C"])])
    assert response.status_code == 201, response.text
    data = response.json()
    ex = data["exchanges"][0]
    assert Decimal(ex["returned_credit"]) == Decimal(ex["replacement_total"]) == 30000 and Decimal(ex["difference"]) == 0
    assert ex["money"] is None
    assert count(db, Payment) == payments and count(db, CashMovement) == cash_movements and cash_total(db) == cash_before
    assert count(db, SaleExchangeSettlement) == 0 and count(db, SaleReturn) == 0          # no refund of any kind
    assert movements(db, InventoryMovementType.RETURN, made["A"].id) == 1                # A: SALE -1, RETURN +1
    assert movements(db, InventoryMovementType.EXCHANGE_OUT, made["C"].id) == -1        # C: EXCHANGE_OUT -1
    assert current_stock(db, made["A"].id) == stock_a + 1 and current_stock(db, made["C"].id) == stock_c - 1
    db.expire_all()
    original = db.get(Sale, sale.id)                                                      # the sale is history
    assert original.total == Decimal("30000") and original.items[0].quantity == 1
    assert [p.amount for p in original.payments] == [Decimal("30000")]
    assert data["display_status"] == "EXCHANGED" and data["display_status_label"] == "Con cambio de producto"
    assert Decimal(data["lines"][0]["exchanged_quantity"]) == 1 and Decimal(data["lines"][0]["returnable_quantity"]) == 0


# ---- 8-12. positive difference -------------------------------------------------------------------------------

def test_08_11_more_expensive_collects_only_the_difference_in_cash(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    sale = sell_a(ctx, made)
    expected_before = Decimal(client.get(f"{API}/cash/current").json()["expected_cash"])
    payments = count(db, Payment)
    response = exchange(client, sale.id, [(item_of(client, sale.id), 1, True)], [take(made["B"])],
                        payment={"channel_id": str(channels["Efectivo"].id), "received_amount": "10000"})
    assert response.status_code == 201, response.text
    ex = response.json()["exchanges"][0]
    assert Decimal(ex["difference"]) == 8000
    assert Decimal(ex["money"]["amount"]) == 8000 and Decimal(ex["money"]["change_amount"]) == 2000
    assert count(db, Payment) == payments                                              # no new sale payment
    moves = db.scalars(select(CashMovement).where(CashMovement.movement_type == CashMovementType.EXCHANGE)).all()
    assert [m.amount for m in moves] == [Decimal("8000")]                               # never +38.000 / -30.000
    current = client.get(f"{API}/cash/current").json()
    assert Decimal(current["expected_cash"]) == expected_before + 8000 and Decimal(current["exchanges_cash"]) == 8000
    assert Decimal(current["refunds"]) == 0


def test_12_electronic_difference_does_not_touch_the_drawer(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    sale = sell_a(ctx, made)
    cash_before = cash_total(db)
    response = exchange(client, sale.id, [(item_of(client, sale.id), 1, True)], [take(made["B"])],
                        payment={"channel_id": str(channels["Nequi"].id)})
    assert response.status_code == 201
    assert response.json()["exchanges"][0]["money"]["affects_cash"] is False
    assert cash_total(db) == cash_before and count(db, CashMovement, CashMovement.movement_type == CashMovementType.EXCHANGE) == 0


def test_12b_difference_needs_a_payment_and_no_difference_refuses_one(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    sale = sell_a(ctx, made)
    item = item_of(client, sale.id)
    assert exchange(client, sale.id, [(item, 1, True)], [take(made["B"])]).status_code == 422
    assert exchange(client, sale.id, [(item, 1, True)], [take(made["C"])],
                    payment={"channel_id": str(channels["Nequi"].id)}).status_code == 422
    assert count(db, SaleExchange) == 0


# ---- 13. cheaper replacement: add another product or give the difference back ---------------------------

def test_13_cheaper_replacement_needs_a_choice_never_store_credit(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    sale = sell_a(ctx, made)
    before = (count(db, InventoryMovement), cash_total(db), count(db, SaleExchange))
    item = item_of(client, sale.id)
    preview = client.post(f"{API}/sales/{sale.id}/exchanges/preview", json={
        "return_lines": [{"sale_item_id": item, "quantity": "1"}], "replacement_lines": [take(made["D"])]}).json()
    assert preview["settlement"] == "STORE_REFUNDS" and Decimal(preview["difference"]) == -5000
    assert Decimal(preview["settlement_amount"]) == 5000
    response = exchange(client, sale.id, [(item, 1, True)], [take(made["D"])])     # no explicit choice yet
    assert response.status_code == 409 and response.json()["detail"]["code"] == "EXCHANGE_CREDIT_REMAINING"
    assert "agrega otro producto o elige devolver" in response.json()["detail"]["message"]
    assert (count(db, InventoryMovement), cash_total(db), count(db, SaleExchange)) == before


# ---- 14-15. prices -------------------------------------------------------------------------------------------

def test_14_15_credit_uses_the_original_price_and_the_new_product_the_current_one(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    sale = sell_a(ctx, made)                                    # paid 30.000
    made["A"].sale_price = Decimal("32000")                     # today A costs 32.000
    made["B"].sale_price = Decimal("40000")                     # and B 40.000
    db.commit()
    preview = client.post(f"{API}/sales/{sale.id}/exchanges/preview", json={
        "return_lines": [{"sale_item_id": item_of(client, sale.id), "quantity": "1"}],
        "replacement_lines": [take(made["B"])]}).json()
    assert Decimal(preview["returned_credit"]) == 30000 and Decimal(preview["replacement_total"]) == 40000
    assert Decimal(preview["difference"]) == 10000


# ---- 16-20. shapes ------------------------------------------------------------------------------------------

def test_16_several_units(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    sale = sell_a(ctx, made, qty=3)
    data = exchange(client, sale.id, [(item_of(client, sale.id), 2, True)], [take(made["C"], 2)]).json()
    assert Decimal(data["exchanges"][0]["returned_credit"]) == 60000 and Decimal(data["exchanges"][0]["difference"]) == 0
    assert Decimal(data["lines"][0]["returnable_quantity"]) == 1
    assert movements(db, InventoryMovementType.EXCHANGE_OUT, made["C"].id) == -2


def test_17_several_new_products(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    sale = sell_a(ctx, made)
    data = exchange(client, sale.id, [(item_of(client, sale.id), 1, True)], [take(made["D"]), take(made["arroz"])],
                    payment={"channel_id": str(channels["Nequi"].id)}).json()
    ex = data["exchanges"][0]
    assert Decimal(ex["replacement_total"]) == 35000 and Decimal(ex["difference"]) == 5000
    assert {r["product_name"] for r in ex["replacements"]} == {"Concentrado D", "Arroz"}


def test_18_returned_without_restock_creates_no_return(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    sale = sell_a(ctx, made)
    stock_a = current_stock(db, made["A"].id)
    data = exchange(client, sale.id, [(item_of(client, sale.id), 1, False)], [take(made["C"])], reason="DEFECTIVE").json()
    assert data["exchanges"][0]["returned"][0]["restock"] is False
    assert current_stock(db, made["A"].id) == stock_a and movements(db, InventoryMovementType.RETURN) == 0


def test_19_manual_returned_line_never_invents_a_return(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    manual_categories(db)
    sale = sell(client, db, channels, [("manual", "SUPER", 30000)], cash(30000))
    data = exchange(client, sale.id, [(item_of(client, sale.id), 1, True)], [take(made["C"])]).json()
    assert data["exchanges"][0]["returned"][0]["restock"] is False
    assert movements(db, InventoryMovementType.RETURN) == 0
    assert movements(db, InventoryMovementType.EXCHANGE_OUT, made["C"].id) == -1


def test_20_manual_replacement_never_invents_an_exchange_out(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    manual_categories(db)
    sale = sell_a(ctx, made)
    data = exchange(client, sale.id, [(item_of(client, sale.id), 1, True)],
                    [{"manual_kind": "SUPER", "description": "Mercado surtido", "amount": "30000"}]).json()
    replacement = data["exchanges"][0]["replacements"][0]
    assert replacement["manual_kind"] == "SUPER" and replacement["product_id"] is None
    assert movements(db, InventoryMovementType.EXCHANGE_OUT) == 0
    # granel is never handed over in an exchange (it belongs to the scale)
    sale2 = sell_a(ctx, made)
    response = exchange(client, sale2.id, [(item_of(client, sale2.id), 1, True)],
                        [{"manual_kind": "GRANEL", "description": "Granel", "amount": "30000"}])
    assert response.status_code == 422 and "báscula" in response.json()["detail"]


# ---- 21-23. quantities and idempotency -------------------------------------------------------------------------

def test_21_returned_or_exchanged_units_are_spent_once(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    sale = sell_a(ctx, made, qty=2)
    item = item_of(client, sale.id)
    assert do_return(client, sale.id, [(item, 1, True)]).status_code == 201           # money return of one
    assert exchange(client, sale.id, [(item, 2, True)], [take(made["C"], 2)]).status_code == 409
    assert exchange(client, sale.id, [(item, 1, True)], [take(made["C"])]).status_code == 201
    assert do_return(client, sale.id, [(item, 1, True)]).status_code == 409           # nothing left
    assert exchange(client, sale.id, [(item, 1, True)], [take(made["C"])]).status_code == 409
    assert detail(client, sale.id)["display_status"] == "EXCHANGED_AND_RETURNED"


def test_23_exchange_is_idempotent(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    sale = sell_a(ctx, made)
    idem = key()
    for _ in range(3):
        assert exchange(client, sale.id, [(item_of(client, sale.id), 1, True)], [take(made["B"])],
                        payment={"channel_id": str(channels["Efectivo"].id)}, idem=idem).status_code == 201
    assert count(db, SaleExchange) == 1 and count(db, SaleExchangeSettlement) == 1
    assert count(db, CashMovement, CashMovement.movement_type == CashMovementType.EXCHANGE) == 1
    assert movements(db, InventoryMovementType.EXCHANGE_OUT) == -1


# ---- 24. invoice ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("status,text", [(ElectronicInvoiceStatus.ISSUED, "El cambio requiere manejo fiscal"),
                                         (ElectronicInvoiceStatus.PENDING, "proceso de factura electrónica")])
def test_24_exchange_blocked_on_invoiced_sale(shop, status, text):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    sale = sell_a(ctx, made)
    db.add(ElectronicInvoice(sale_id=sale.id, status=status)); db.commit()
    data = detail(client, sale.id)
    assert data["allowed_actions"]["exchange"]["allowed"] is False and text in data["allowed_actions"]["exchange"]["reason"]
    response = exchange(client, sale.id, [(data["lines"][0]["id"], 1, True)], [take(made["C"])])
    assert response.status_code == 409 and text in response.json()["detail"]["message"]
    assert count(db, SaleExchange) == 0 and fake.invoices == []


# ---- 25-26. granel ---------------------------------------------------------------------------------------------

def test_25_26_external_granel_never_becomes_credit_and_pos_lines_can_be_exchanged(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    manual_categories(db)
    sale = create_sale(db)
    add_manual(client, sale, "SUPER", 20000, "Mercado")
    add_manual(client, sale, "GRANEL", 10000, "Granel báscula")
    assert client.post(f"{API}/sales/{sale.id}/checkout", json={"channel_id": str(channels["Efectivo"].id),
                                                                "received_amount": "30000"}).status_code == 200
    lines = lines_of(client, sale.id)
    assert [line["product_name"] for line in lines] == ["Mercado"]           # the scale part is no POS line
    data = exchange(client, sale.id, [(lines[0]["id"], 1, True)], [take(made["C"])],
                    payment={"channel_id": str(channels["Nequi"].id)}).json()
    ex = data["exchanges"][0]
    assert Decimal(ex["returned_credit"]) == 20000 and Decimal(ex["difference"]) == 10000   # never 30.000 of credit


def test_26b_pos_granel_paid_electronically_gives_credit_without_inventory(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    manual_categories(db)
    sale = create_sale(db)
    add_manual(client, sale, "GRANEL", 30000, "Granel electrónico")
    client.put(f"{API}/sales/{sale.id}/granel-settlement", json={"settlement": "POS"})
    assert client.post(f"{API}/sales/{sale.id}/checkout", json={"channel_id": str(channels["Nequi"].id)}).status_code == 200
    data = exchange(client, sale.id, [(item_of(client, sale.id), 1, True)], [take(made["C"])]).json()
    assert Decimal(data["exchanges"][0]["difference"]) == 0
    assert movements(db, InventoryMovementType.RETURN) == 0


# ---- 27-29. reports and history ---------------------------------------------------------------------------------

def test_27_same_value_exchange_is_not_a_money_refund_in_reports(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    sale = sell_a(ctx, made)
    exchange(client, sale.id, [(item_of(client, sale.id), 1, True)], [take(made["C"])])
    data = summary(client)
    assert Decimal(data["sales_total"]) == 30000
    assert Decimal(data["returns"]["returns_total"]) == 0 and Decimal(data["returns"]["refunds_cash"]) == 0
    assert data["exchanges"]["exchange_count"] == 1 and data["exchanges"]["same_value_count"] == 1
    assert Decimal(data["exchanges"]["difference_collected"]) == 0
    live = client.get(f"{API}/reports/current").json()["report_data"]
    assert Decimal(live["returns"]["returns_total"]) == 0 and live["exchanges"]["exchange_count"] == 1


def test_28_exchange_with_difference_reports_only_the_real_money(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    sale = sell_a(ctx, made)
    exchange(client, sale.id, [(item_of(client, sale.id), 1, True)], [take(made["B"])],
             payment={"channel_id": str(channels["Efectivo"].id)})
    data = summary(client)
    assert Decimal(data["sales_total"]) == 30000                                   # no artificial 38.000 sale
    assert Decimal(data["returns"]["returns_total"]) == 0                          # no artificial 30.000 refund
    assert Decimal(data["exchanges"]["difference_collected"]) == 8000 and Decimal(data["exchanges"]["difference_cash"]) == 8000
    dashboard = client.get(f"{API}/reports/dashboard?preset=today").json()["summary"]
    assert dashboard["exchange_count"] == 1 and Decimal(dashboard["exchange_difference"]) == 8000
    assert Decimal(dashboard["returns_total"]) == 0


def test_29_history_shows_product_exchange_never_refund(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    sale = sell_a(ctx, made)
    exchange(client, sale.id, [(item_of(client, sale.id), 1, True)], [take(made["C"])])
    rows = client.get(f"{API}/cash/current").json()["financial_movements"]
    row = next(r for r in rows if r["type"] == "EXCHANGE")
    assert row["title"] == f"Cambio de producto · Venta {sale.number}" and row["subtitle"] == "Sin diferencia por pagar"
    assert Decimal(row["amount"]) == 0 and row["reference_id"] == str(sale.id)
    assert not any(r["title"] == "Reembolso" for r in rows)


# ---- 30-33. the rest of post-sale still works -------------------------------------------------------------------

def test_30_32_money_return_void_and_correction_still_work_next_to_exchanges(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    sale = sell_a(ctx, made, qty=2, pay=nequi)
    item = item_of(client, sale.id)
    exchange(client, sale.id, [(item, 1, True)], [take(made["C"])])
    data = detail(client, sale.id)
    assert data["allowed_actions"]["void"]["allowed"] is False                      # VOID is not an exchange
    assert do_void(client, sale.id).status_code == 409
    assert do_correct(client, sale.id, payment_of(db, sale.id).id, channels["Datáfono"]).status_code == 201
    assert do_return(client, sale.id, [(item, 1, True)]).status_code == 201          # money return of the rest
    other = sell_a(ctx, made)
    assert do_void(client, other.id).status_code == 201


def test_33_block1_formula_intact_and_exchange_cash_shown_apart(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    p = block1_categories(db)
    sale = sell(client, db, channels, [("scan", p["CIGARROS"], 2)], cash(2000))
    before = recon(client)
    item = item_of(client, sale.id)
    exchange(client, sale.id, [(item, 1, True)], [take(p["LICORES"])])                # same value: no money
    same = recon(client)
    assert same["by"] == before["by"] and same["pos_cash_subtotal"] == before["pos_cash_subtotal"]
    assert Decimal(same["returns_cash_total"]) == 0 and Decimal(same["exchange_difference_cash_total"]) == 0
    exchange(client, sale.id, [(item, 1, True)], [take(p["LICORES"], 2)],
             payment={"channel_id": str(channels["Efectivo"].id)})                     # +1.000 in cash
    after = recon(client)
    assert after["by"] == before["by"] and after["block1_subtotal"] == before["block1_subtotal"]
    aside = {r["category_code"]: Decimal(r["amount"]) for r in after["exchange_difference_cash_by_category"]}
    assert aside["LICORES"] == 1000 and Decimal(after["exchange_difference_cash_total"]) == 1000
    assert Decimal(after["returns_cash_total"]) == 0                                    # never a "cash return"


# ======================================================================================================
# FINAL POLICY: a cheaper replacement -> add another product OR the cashier gives the difference back
# ======================================================================================================

def unit_key(client, sale_id, name):
    """The provenance key of a unit the customer can bring back (original line or exchange line)."""
    for unit in detail(client, sale_id)["units"]:
        if unit["product_name"] == name and Decimal(unit["returnable_quantity"]) > 0:
            return unit["source_id"] if unit["source_type"] == "SALE_ITEM" else ("R", unit["source_id"])
    raise AssertionError(f"{name} no está disponible")


def test_n01_cheaper_then_another_product_uses_the_customer_pays_flow(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    sale = sell_a(ctx, made)
    snack = product(db, "Snack", 7000)
    data = exchange(client, sale.id, [(item_of(client, sale.id), 1, True)], [take(made["D"]), take(snack)],
                    payment={"channel_id": str(channels["Efectivo"].id)}).json()
    ex = data["exchanges"][0]
    assert ex["settlement"] == "CUSTOMER_PAYS" and Decimal(ex["difference"]) == 2000
    assert ex["money"]["direction"] == "IN" and Decimal(ex["money"]["amount"]) == 2000


def test_n02_n05_store_refunds_only_the_difference_in_cash(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    sale = sell_a(ctx, made)
    cash_before, payments = cash_total(db), count(db, Payment)
    response = exchange(client, sale.id, [(item_of(client, sale.id), 1, True)], [take(made["D"])],
                        payment={"channel_id": str(channels["Efectivo"].id)})
    assert response.status_code == 201, response.text
    ex = response.json()["exchanges"][0]
    assert ex["settlement"] == "STORE_REFUNDS" and Decimal(ex["settlement_amount"]) == 5000
    assert ex["money"]["direction"] == "OUT" and ex["money"]["status"] == "COMPLETED"
    moves = db.scalars(select(CashMovement).where(CashMovement.movement_type == CashMovementType.EXCHANGE_REFUND)).all()
    assert [m.amount for m in moves] == [Decimal("-5000")]                    # only $5.000 leave the drawer
    assert cash_total(db) == cash_before - 5000
    assert count(db, CashMovement, CashMovement.movement_type == CashMovementType.REFUND) == 0   # no $30.000 refund
    assert count(db, SaleReturn) == 0
    assert count(db, Payment) == payments and count(db, Sale) == 1             # no artificial $25.000 sale
    assert movements(db, InventoryMovementType.EXCHANGE_OUT, made["D"].id) == -1


def test_n06_n07_electronic_difference_given_back_is_pending_until_confirmed_once(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    sale = sell_a(ctx, made)
    cash_before = cash_total(db)
    data = exchange(client, sale.id, [(item_of(client, sale.id), 1, True)], [take(made["D"])],
                    payment={"channel_id": str(channels["Nequi"].id)}).json()
    ex = data["exchanges"][0]
    assert ex["money"]["status"] == "PENDING" and cash_total(db) == cash_before
    pending = data["pending_refunds"]
    assert len(pending) == 1 and pending[0]["kind"] == "EXCHANGE" and Decimal(pending[0]["amount"]) == 5000
    url = f"{API}/sales/{sale.id}/exchanges/{ex['id']}/refund/confirm"
    first = client.post(url, json={"performed_by_text": "Ana"}).json()
    second = client.post(url, json={}).json()
    assert first["pending_refunds"] == [] and first["exchanges"][0]["money"]["status"] == "COMPLETED"
    assert first["exchanges"][0]["money"]["confirmed_at"] == second["exchanges"][0]["money"]["confirmed_at"]
    assert cash_total(db) == cash_before


def test_n08_n10_settlements_carry_only_the_difference(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    efectivo = {"channel_id": str(channels["Efectivo"].id)}
    even = sell_a(ctx, made)
    pays = sell_a(ctx, made)
    refunds = sell_a(ctx, made)
    exchange(client, even.id, [(item_of(client, even.id), 1, True)], [take(made["C"])])
    exchange(client, pays.id, [(item_of(client, pays.id), 1, True)], [take(made["B"])], payment=efectivo)
    exchange(client, refunds.id, [(item_of(client, refunds.id), 1, True)], [take(made["D"])], payment=efectivo)
    rows = {e.settlement: e for e in db.scalars(select(SaleExchange)).all()}
    assert rows["EVEN"].money is None and rows["EVEN"].difference == 0
    assert rows["CUSTOMER_PAYS"].money.amount == Decimal("8000") and rows["CUSTOMER_PAYS"].money.direction == "IN"
    assert rows["STORE_REFUNDS"].money.amount == Decimal("5000") and rows["STORE_REFUNDS"].difference == Decimal("-5000")


def test_n11_n13_reports_cash_and_block1_tell_collected_from_given_back(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    p = block1_categories(db)
    efectivo = {"channel_id": str(channels["Efectivo"].id)}
    sale = sell(client, db, channels, [("scan", p["LICORES"], 3)], cash(3000))
    before = recon(client)
    item = item_of(client, sale.id)
    exchange(client, sale.id, [(item, 1, True)], [take(p["CIGARROS"], 2)], payment=efectivo)   # +1.000
    cheap = product(db, "Mecha", 600)
    exchange(client, sale.id, [(item, 1, True)], [take(cheap)], payment=efectivo)             # -400
    data = summary(client)["exchanges"]
    assert data["exchange_count"] == 2 and data["customer_pays_count"] == 1 and data["store_refunds_count"] == 1
    assert Decimal(data["difference_collected"]) == 1000 and Decimal(data["difference_refunded"]) == 400
    assert Decimal(data["refunded_cash"]) == 400
    assert Decimal(summary(client)["sales_total"]) == 3000                     # sales never inflated
    current = client.get(f"{API}/cash/current").json()
    assert Decimal(current["exchanges_cash"]) == 1000 and Decimal(current["exchange_refunds_cash"]) == 400
    rows = [r for r in current["financial_movements"] if r["type"] == "EXCHANGE"]
    out = next(r for r in rows if r["direction"] == "OUT")
    assert out["subtitle"] == "Diferencia devuelta" and Decimal(out["amount"]) == 400 and out["reference_id"] == str(sale.id)
    after = recon(client)
    assert after["by"] == before["by"] and after["block1_subtotal"] == before["block1_subtotal"]   # formula intact
    assert Decimal(after["exchange_difference_cash_total"]) == 1000
    assert Decimal(after["exchange_refund_cash_total"]) == 400
    assert Decimal(after["returns_cash_total"]) == 0


# ======================================================================================================
# CHAINED EXCHANGES: units delivered by an exchange can be exchanged / returned later (provenance)
# ======================================================================================================

def test_c14_c22_a_to_b_to_c_with_full_traceability(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    sale = sell_a(ctx, made)                                                   # 14. Venta A
    stock = {k: current_stock(db, made[k].id) for k in ("A", "C", "D")}
    item = item_of(client, sale.id)
    exchange(client, sale.id, [(item, 1, True)], [take(made["C"])])            # 15. A -> C (same value)
    b_key = unit_key(client, sale.id, "Concentrado C")                         # 16. C (delivered) is eligible
    assert isinstance(b_key, tuple)
    data = exchange(client, sale.id, [(b_key, 1, True)], [take(made["D"]), take(made["arroz"])],
                    payment={"channel_id": str(channels["Nequi"].id)}).json()   # 17. C -> D + arroz
    second = data["exchanges"][1]
    assert second["returned"][0]["origin"] == "Cambio 1" and second["returned"][0]["product_name"] == "Concentrado C"
    assert Decimal(second["returned_credit"]) == 30000 and Decimal(second["difference"]) == 5000
    # 18-20. inventory of each product, every movement linked to its exchange
    assert current_stock(db, made["A"].id) == stock["A"] + 1                    # SALE -1, RETURN +1
    assert current_stock(db, made["C"].id) == stock["C"]                        # EXCHANGE_OUT -1, RETURN +1
    assert current_stock(db, made["D"].id) == stock["D"] - 1                    # EXCHANGE_OUT -1
    # 21-22. nothing is consumed twice
    assert exchange(client, sale.id, [(item, 1, True)], [take(made["C"])]).status_code == 409
    assert exchange(client, sale.id, [(b_key, 1, True)], [take(made["C"])]).status_code == 409
    # 27. history shows the chain A -> C -> D
    ex = detail(client, sale.id)["exchanges"]
    assert [e["returned"][0]["product_name"] for e in ex] == ["Concentrado A", "Concentrado C"]
    assert Decimal(ex[0]["replacements"][0]["later_consumed_quantity"]) == 1
    # 30. the original sale is untouched
    db.expire_all()
    original = db.get(Sale, sale.id)
    assert original.total == Decimal("30000") and len(original.items) == 1 and original.items[0].quantity == 1


def test_c23_c24_partial_units_of_a_replacement_line(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    sale = sell_a(ctx, made, qty=3)
    exchange(client, sale.id, [(item_of(client, sale.id), 3, True)], [take(made["C"], 3)])     # 3 A -> 3 C
    c_key = unit_key(client, sale.id, "Concentrado C")
    exchange(client, sale.id, [(c_key, 2, True)], [take(made["A"], 2)])                       # 2 of those 3 C
    units = {u["product_name"] + "@" + u["origin"]: u for u in detail(client, sale.id)["units"]}
    assert Decimal(units["Concentrado C@Cambio 1"]["returnable_quantity"]) == 1
    assert Decimal(units["Concentrado A@Cambio 2"]["returnable_quantity"]) == 2
    assert exchange(client, sale.id, [(c_key, 2, True)], [take(made["A"], 2)]).status_code == 409


def test_c26_chained_exchange_is_idempotent(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    sale = sell_a(ctx, made)
    exchange(client, sale.id, [(item_of(client, sale.id), 1, True)], [take(made["C"])])
    c_key, idem = unit_key(client, sale.id, "Concentrado C"), key()
    for _ in range(3):
        assert exchange(client, sale.id, [(c_key, 1, True)], [take(made["A"])], idem=idem).status_code == 201
    assert count(db, SaleExchange) == 2 and movements(db, InventoryMovementType.EXCHANGE_OUT, made["A"].id) == -1


def test_c28_c29_credit_of_a_delivered_unit_is_its_delivery_value(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    sale = sell_a(ctx, made)                                                   # A sold at 30.000
    exchange(client, sale.id, [(item_of(client, sale.id), 1, True)], [take(made["B"])],
             payment={"channel_id": str(channels["Efectivo"].id)})             # B delivered at 38.000
    made["B"].sale_price = Decimal("45000")
    made["A"].sale_price = Decimal("50000")
    db.commit()
    preview = client.post(f"{API}/sales/{sale.id}/exchanges/preview", json={
        "return_lines": [{"replacement_line_id": str(unit_key(client, sale.id, "Concentrado B")[1]), "quantity": "1"}],
        "replacement_lines": [take(made["C"])]}).json()
    assert Decimal(preview["returned_credit"]) == 38000                       # never A's 30.000, never today's 45.000
    assert preview["settlement"] == "STORE_REFUNDS" and Decimal(preview["settlement_amount"]) == 8000


def test_c_money_return_of_a_delivered_unit_uses_its_delivery_value(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    sale = sell_a(ctx, made)
    exchange(client, sale.id, [(item_of(client, sale.id), 1, True)], [take(made["C"])])
    c_key = unit_key(client, sale.id, "Concentrado C")
    stock_c = current_stock(db, made["C"].id)
    response = client.post(f"{API}/sales/{sale.id}/returns", json={
        "lines": [{"replacement_line_id": str(c_key[1]), "quantity": "1", "restock": True}],
        "reason_code": "CUSTOMER_RETURN", "idempotency_key": key()})
    assert response.status_code == 201, response.text
    data = response.json()
    assert Decimal(data["returns"][0]["total_amount"]) == 30000 and data["returns"][0]["lines"][0]["origin"] == "Cambio 1"
    assert current_stock(db, made["C"].id) == stock_c + 1
    assert data["display_status"] == "EXCHANGED_AND_RETURNED"


# ---- regression of the rest of post-sale next to the final policy ---------------------------------------

def test_r31_r36_money_return_void_correction_invoice_and_sales(shop):
    ctx, made = shop
    db, client, fake, arroz, channels, settings = ctx
    plain = sell_a(ctx, made, qty=2)
    assert do_return(client, plain.id, [(item_of(client, plain.id), 1, True)]).status_code == 201       # 31
    other = sell_a(ctx, made)
    assert do_void(client, other.id).status_code == 201                                                  # 32
    third = sell_a(ctx, made, pay=nequi)
    assert do_correct(client, third.id, payment_of(db, third.id).id, channels["Datáfono"]).status_code == 201   # 33
    invoiced = sell_a(ctx, made)
    db.add(ElectronicInvoice(sale_id=invoiced.id, status=ElectronicInvoiceStatus.ISSUED))
    db.commit()
    blocked = exchange(client, invoiced.id, [(item_of(client, invoiced.id), 1, True)], [take(made["D"])],
                       payment={"channel_id": str(channels["Efectivo"].id)})
    assert blocked.status_code == 409 and "manejo fiscal" in blocked.json()["detail"]["message"]          # 34
    assert Decimal(summary(client)["sales_total"]) == 30000 * 5                                           # 36
    assert fake.invoices == []
