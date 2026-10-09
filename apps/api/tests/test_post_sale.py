"""POST-SALE MANAGEMENT (migration 0027): detail, notes, returns, refunds, VOID and payment corrections.

Everything runs on SQLite in memory with fake data (fixture `ctx` of test_customers: fake Alegra, nothing
reaches the network). Concurrency with real row locks lives in test_post_sale_postgres.py (run against a
COPY of the database, never the real one)."""
import re
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.domain.enums import (CashMovementType, ElectronicInvoiceStatus, InventoryMovementType, PaymentMethod,
                              SaleStatus)
from app.models import (CashMovement, CashSession, ElectronicInvoice, InventoryMovement, Payment, PaymentChannel,
                        PaymentCorrection, Sale, SaleNote, SaleRefund, SaleReturn)
from app.services.inventory import current_stock
from app.services.pos import create_sale
from tests.test_customers import (API, add_manual, block1_categories, cash, close_session, ctx,  # noqa: F401
                                  granel_electronic, manual_categories, nequi, recon, sell)

ZERO = Decimal("0")


# ---- helpers --------------------------------------------------------------------------------------------

def key():
    return f"test-{uuid4().hex}"


def sell_arroz(ctx, qty=3, pay=None):
    db, client, fake, product, channels, settings = ctx
    return sell(client, db, channels, [("scan", product, qty)], pay or cash(10000 * qty))


def detail(client, sale_id):
    response = client.get(f"{API}/sales/{sale_id}/detail")
    assert response.status_code == 200, response.text
    return response.json()


def lines_of(client, sale_id):
    return detail(client, sale_id)["lines"]


def do_return(client, sale_id, items, *, channel_id=None, reason="CUSTOMER_RETURN", idem=None, text=None):
    """items: [(sale_item_id, qty, restock)]"""
    body = {"lines": [{"sale_item_id": i, "quantity": str(q), "restock": r} for i, q, r in items],
            "reason_code": reason, "reason_text": text, "idempotency_key": idem or key()}
    if channel_id:
        body["refund_channel_id"] = str(channel_id)
    return client.post(f"{API}/sales/{sale_id}/returns", json=body)


def do_void(client, sale_id, idem=None, restock=True, reason="WRONG_CHARGE"):
    return client.post(f"{API}/sales/{sale_id}/void", json={"reason_code": reason, "restock": restock,
                                                             "idempotency_key": idem or key()})


def do_correct(client, sale_id, payment_id, channel, idem=None, reason="Se marcó mal el medio"):
    return client.post(f"{API}/sales/{sale_id}/payments/{payment_id}/correct",
                       json={"to_channel_id": str(channel.id), "reason": reason, "idempotency_key": idem or key()})


def cash_total(db):
    db.expire_all()
    return Decimal(db.scalar(select(func.coalesce(func.sum(CashMovement.amount), 0))))


def count(db, model, *where):
    db.expire_all()
    return db.scalar(select(func.count()).select_from(model).where(*where))


def payment_of(db, sale_id):
    db.expire_all()
    return db.scalar(select(Payment).where(Payment.sale_id == sale_id))


def add_channel(db, name, affects_cash=False):
    channel = PaymentChannel(name=name, payment_type=PaymentMethod.CASH if affects_cash else PaymentMethod.TRANSFER,
                             affects_cash=affects_cash, display_order=30, alegra_payment_method="CREDIT_TRANSFER")
    db.add(channel); db.commit()
    return channel


def reopen(client, amount="0"):
    response = client.post(f"{API}/cash/open", json={"opening_amount": amount, "note": "Nueva jornada prueba"})
    assert response.status_code == 201, response.text


def summary(client):
    response = client.get(f"{API}/daily-operations/summary")
    assert response.status_code == 200, response.text
    return response.json()


# ---- 1. detail ------------------------------------------------------------------------------------------

def test_01_detail_shows_lines_payments_status_and_allowed_actions(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = sell_arroz(ctx, 3)
    data = detail(client, sale.id)
    assert data["number"] == sale.number and data["status"] == "COMPLETED"
    assert data["display_status"] == "COMPLETED" and data["display_status_label"] == "Completada"
    line = data["lines"][0]
    assert Decimal(line["quantity"]) == 3 and Decimal(line["returnable_quantity"]) == 3
    assert Decimal(line["returned_quantity"]) == 0
    payment = data["payments"][0]
    assert payment["original_channel_name"] == payment["effective_channel_name"] == "Efectivo"
    assert payment["corrected"] is False and payment["corrections"] == []
    assert data["invoice"] is None and data["notes"] == [] and data["returns"] == []
    actions = data["allowed_actions"]
    assert all(actions[name]["allowed"] for name in ("note", "return", "void", "correct_payment"))


# ---- 2-3. notes -----------------------------------------------------------------------------------------

def test_02_note_is_append_only_and_changes_nothing_else(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = sell_arroz(ctx, 1)
    before = (cash_total(db), count(db, InventoryMovement), count(db, Payment), db.get(Sale, sale.id).total)
    for text in ("El cliente pagó con billete de 50", "Segunda aclaración"):
        response = client.post(f"{API}/sales/{sale.id}/notes", json={"text": text, "performed_by_text": "Ana"})
        assert response.status_code == 201
    notes = detail(client, sale.id)["notes"]
    assert [n["text"] for n in notes] == ["El cliente pagó con billete de 50", "Segunda aclaración"]
    assert notes[0]["performed_by_text"] == "Ana"
    db.expire_all()
    assert (cash_total(db), count(db, InventoryMovement), count(db, Payment), db.get(Sale, sale.id).total) == before
    # No endpoint edits or deletes a note.
    note_id = notes[0]["id"]
    assert client.delete(f"{API}/sales/{sale.id}/notes/{note_id}").status_code in (404, 405)
    assert client.put(f"{API}/sales/{sale.id}/notes/{note_id}", json={"text": "x"}).status_code in (404, 405)


def test_03_note_idempotency_key_creates_one_note(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = sell_arroz(ctx, 1)
    idem = key()
    for _ in range(2):
        assert client.post(f"{API}/sales/{sale.id}/notes", json={"text": "Una vez", "idempotency_key": idem}).status_code == 201
    assert count(db, SaleNote) == 1


# ---- 4-11. returns --------------------------------------------------------------------------------------

def test_04_partial_return_restocks_once_and_refunds_cash(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = sell_arroz(ctx, 3)
    stock_before, cash_before = current_stock(db, product.id), cash_total(db)
    item = lines_of(client, sale.id)[0]["id"]
    response = do_return(client, sale.id, [(item, 1, True)])
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["display_status"] == "PARTIALLY_RETURNED" and data["display_status_label"] == "Devolución parcial"
    assert data["returns"][0]["kind"] == "PARTIAL" and Decimal(data["returns"][0]["total_amount"]) == 10000
    assert Decimal(data["lines"][0]["returnable_quantity"]) == 2
    assert current_stock(db, product.id) == stock_before + 1
    assert cash_total(db) == cash_before - 10000
    assert count(db, InventoryMovement, InventoryMovement.movement_type == InventoryMovementType.RETURN) == 1
    # The sale itself is untouched history.
    db.expire_all()
    stored = db.get(Sale, sale.id)
    assert stored.status == SaleStatus.COMPLETED and stored.total == Decimal("30000") and stored.items[0].quantity == 3


def test_05_total_return_marks_returned(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = sell_arroz(ctx, 2)
    item = lines_of(client, sale.id)[0]["id"]
    data = do_return(client, sale.id, [(item, 2, True)]).json()
    assert data["returns"][0]["kind"] == "TOTAL" and data["display_status"] == "RETURNED"
    assert data["allowed_actions"]["return"]["allowed"] is False


def test_06_two_partial_returns_accumulate_and_the_rest_is_total(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = sell_arroz(ctx, 3)
    item = lines_of(client, sale.id)[0]["id"]
    assert do_return(client, sale.id, [(item, 1, True)]).status_code == 201
    assert do_return(client, sale.id, [(item, 1, True)]).status_code == 201
    data = do_return(client, sale.id, [(item, 1, True)]).json()
    assert [r["kind"] for r in data["returns"]] == ["PARTIAL", "PARTIAL", "TOTAL"]
    assert Decimal(data["returned_total"]) == 30000 and Decimal(data["net_total"]) == 0


def test_07_cannot_return_more_than_sold_or_left(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = sell_arroz(ctx, 2)
    item = lines_of(client, sale.id)[0]["id"]
    assert do_return(client, sale.id, [(item, 3, True)]).status_code == 409
    assert do_return(client, sale.id, [(item, 2, True)]).status_code == 201
    assert do_return(client, sale.id, [(item, 1, True)]).status_code == 409
    assert count(db, SaleReturn) == 1


def test_08_restock_false_keeps_the_decision_without_inventory(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = sell_arroz(ctx, 2)
    stock = current_stock(db, product.id)
    item = lines_of(client, sale.id)[0]["id"]
    data = do_return(client, sale.id, [(item, 1, False)], reason="DEFECTIVE").json()
    assert data["returns"][0]["lines"][0]["restock"] is False
    assert current_stock(db, product.id) == stock
    assert count(db, InventoryMovement, InventoryMovement.movement_type == InventoryMovementType.RETURN) == 0


@pytest.mark.parametrize("kind", ["SUPER", "BULTO", "PET_OTHER"])
def test_09_manual_lines_refund_money_without_return_movement(ctx, kind):
    db, client, fake, product, channels, settings = ctx
    manual_categories(db)
    sale = sell(client, db, channels, [("manual", kind, 8000)], cash(8000))
    line = lines_of(client, sale.id)[0]
    assert line["restockable"] is False
    movements = count(db, InventoryMovement)
    data = do_return(client, sale.id, [(line["id"], 1, True)]).json()        # restock ignored: no product
    assert data["returns"][0]["lines"][0]["restock"] is False
    assert count(db, InventoryMovement) == movements
    assert Decimal(data["returns"][0]["refunds"][0]["amount"]) == 8000


def test_10_return_is_idempotent_by_key(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = sell_arroz(ctx, 3)
    item = lines_of(client, sale.id)[0]["id"]
    idem = key()
    for _ in range(3):
        assert do_return(client, sale.id, [(item, 1, True)], idem=idem).status_code == 201
    assert count(db, SaleReturn) == 1
    assert count(db, InventoryMovement, InventoryMovement.movement_type == InventoryMovementType.RETURN) == 1
    assert count(db, CashMovement, CashMovement.movement_type == CashMovementType.REFUND) == 1


def test_11_reason_other_requires_text_and_unknown_reason_rejected(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = sell_arroz(ctx, 1)
    item = lines_of(client, sale.id)[0]["id"]
    assert do_return(client, sale.id, [(item, 1, True)], reason="OTHER").status_code == 422
    assert do_return(client, sale.id, [(item, 1, True)], reason="NOPE").status_code == 422
    assert do_return(client, sale.id, [(item, 1, True)], reason="OTHER", text="Cambio de opinión").status_code == 201


# ---- 12-17. refunds -------------------------------------------------------------------------------------

def test_12_cash_refund_is_one_negative_refund_movement_and_no_negative_payment(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = sell_arroz(ctx, 2)
    payments = count(db, Payment)
    item = lines_of(client, sale.id)[0]["id"]
    data = do_return(client, sale.id, [(item, 1, True)]).json()
    refund = data["returns"][0]["refunds"][0]
    assert refund["status"] == "COMPLETED" and refund["affects_cash"] is True
    movements = db.scalars(select(CashMovement).where(CashMovement.movement_type == CashMovementType.REFUND)).all()
    assert [m.amount for m in movements] == [Decimal("-10000")] and movements[0].reference_id == sale.id
    assert count(db, Payment) == payments and count(db, Payment, Payment.amount < 0) == 0
    current = client.get(f"{API}/cash/current").json()
    assert Decimal(current["refunds"]) == 10000 and Decimal(current["expected_cash"]) == 10000


def test_13_return_requires_an_open_jornada(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = sell_arroz(ctx, 1)
    close_session(client, 10000)
    data = detail(client, sale.id)
    assert data["allowed_actions"]["return"]["allowed"] is False
    assert "jornada" in data["allowed_actions"]["return"]["reason"].lower()
    response = do_return(client, sale.id, [(data["lines"][0]["id"], 1, True)])
    assert response.status_code == 409 and response.json()["detail"]["code"] == "OPERATIONAL_SESSION_CLOSED"
    assert count(db, SaleReturn) == 0


def test_14_electronic_refund_is_pending_and_moves_no_cash(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = sell_arroz(ctx, 2, nequi)
    cash_before = cash_total(db)
    item = lines_of(client, sale.id)[0]["id"]
    data = do_return(client, sale.id, [(item, 1, True)]).json()
    assert cash_total(db) == cash_before
    assert data["refund_status"] == "PENDING"
    pending = data["pending_refunds"]
    assert len(pending) == 1 and pending[0]["channel_name"] == "Nequi" and Decimal(pending[0]["amount"]) == 10000
    # The cash session lists it for traceability without touching the drawer.
    rows = [m for m in client.get(f"{API}/cash/current").json()["financial_movements"] if m["type"] == "REFUND"]
    assert rows and rows[0]["affects_cash"] is False and "Pendiente devolver por Nequi" in rows[0]["subtitle"]


def test_15_confirm_electronic_refund_records_who_and_when_without_stock(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = sell_arroz(ctx, 2, nequi)
    item = lines_of(client, sale.id)[0]["id"]
    refund_id = do_return(client, sale.id, [(item, 1, True)]).json()["pending_refunds"][0]["id"]
    returns_before = count(db, InventoryMovement, InventoryMovement.movement_type == InventoryMovementType.RETURN)
    response = client.post(f"{API}/sales/{sale.id}/refunds/{refund_id}/confirm", json={"performed_by_text": "Ana"})
    assert response.status_code == 200
    data = response.json()
    assert data["pending_refunds"] == [] and data["refund_status"] == "COMPLETED"
    refund = data["returns"][0]["refunds"][0]
    assert refund["status"] == "COMPLETED" and refund["confirmed_at"] and refund["confirmed_by_text"] == "Ana"
    assert count(db, InventoryMovement, InventoryMovement.movement_type == InventoryMovementType.RETURN) == returns_before


def test_16_double_confirmation_is_idempotent(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = sell_arroz(ctx, 1, nequi)
    item = lines_of(client, sale.id)[0]["id"]
    refund_id = do_return(client, sale.id, [(item, 1, True)]).json()["pending_refunds"][0]["id"]
    first = client.post(f"{API}/sales/{sale.id}/refunds/{refund_id}/confirm", json={}).json()
    second = client.post(f"{API}/sales/{sale.id}/refunds/{refund_id}/confirm", json={}).json()
    assert first["returns"][0]["refunds"][0]["confirmed_at"] == second["returns"][0]["refunds"][0]["confirmed_at"]
    assert count(db, CashMovement, CashMovement.movement_type == CashMovementType.REFUND) == 0


def test_17_confirm_refund_works_after_the_jornada_closed(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = sell_arroz(ctx, 1, nequi)
    item = lines_of(client, sale.id)[0]["id"]
    refund_id = do_return(client, sale.id, [(item, 1, True)]).json()["pending_refunds"][0]["id"]
    close_session(client, 0)
    assert client.post(f"{API}/sales/{sale.id}/refunds/{refund_id}/confirm", json={}).status_code == 200


def test_18_refund_channel_must_be_a_paid_channel_and_within_its_amount(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = sell(client, db, channels, [("scan", product, 3)],
                lambda ch: {"payments": [{"channel_id": str(ch["Efectivo"].id), "amount": "10000",
                                          "received_amount": "10000"},
                                         {"channel_id": str(ch["Nequi"].id), "amount": "20000"}]})
    item = lines_of(client, sale.id)[0]["id"]
    assert do_return(client, sale.id, [(item, 1, True)]).status_code == 422          # two channels: choose one
    assert do_return(client, sale.id, [(item, 1, True)], channel_id=channels["Datáfono"].id).status_code == 422
    assert do_return(client, sale.id, [(item, 2, True)], channel_id=channels["Efectivo"].id).status_code == 409
    assert do_return(client, sale.id, [(item, 2, True)], channel_id=channels["Nequi"].id).status_code == 201


# ---- 19-23. VOID ----------------------------------------------------------------------------------------

def test_19_void_returns_everything_and_keeps_the_sale_completed(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = sell_arroz(ctx, 2)
    stock, cash_before = current_stock(db, product.id), cash_total(db)
    response = do_void(client, sale.id)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["status"] == "COMPLETED" and data["display_status"] == "VOIDED" and data["display_status_label"] == "Anulada"
    assert data["returns"][0]["kind"] == "VOID" and Decimal(data["returns"][0]["total_amount"]) == 20000
    assert current_stock(db, product.id) == stock + 2 and cash_total(db) == cash_before - 20000
    db.expire_all()
    stored = db.get(Sale, sale.id)
    assert stored.status == SaleStatus.COMPLETED and stored.total == Decimal("20000")
    assert stored.payments[0].amount == Decimal("20000")
    assert all(not data["allowed_actions"][a]["allowed"] for a in ("return", "void", "correct_payment"))
    assert data["allowed_actions"]["note"]["allowed"] is True


def test_20_void_requires_reason(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = sell_arroz(ctx, 1)
    response = client.post(f"{API}/sales/{sale.id}/void", json={"idempotency_key": key()})
    assert response.status_code == 422
    assert client.post(f"{API}/sales/{sale.id}/void", json={"reason_code": "OTHER", "idempotency_key": key()}).status_code == 422


def test_21_second_void_rejected_same_key_idempotent(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = sell_arroz(ctx, 1)
    idem = key()
    assert do_void(client, sale.id, idem).status_code == 201
    assert do_void(client, sale.id, idem).status_code == 201                       # same key -> same VOID
    assert do_void(client, sale.id).status_code == 409                             # another VOID -> rejected
    assert count(db, SaleReturn, SaleReturn.kind == "VOID") == 1
    assert count(db, CashMovement, CashMovement.movement_type == CashMovementType.REFUND) == 1


def test_22_void_mixed_payment_refunds_each_effective_channel(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = sell(client, db, channels, [("scan", product, 3)],
                lambda ch: {"payments": [{"channel_id": str(ch["Efectivo"].id), "amount": "10000",
                                          "received_amount": "10000"},
                                         {"channel_id": str(ch["Nequi"].id), "amount": "20000"}]})
    data = do_void(client, sale.id).json()
    refunds = {r["channel_name"]: r for r in data["returns"][0]["refunds"]}
    assert refunds["Efectivo"]["status"] == "COMPLETED" and Decimal(refunds["Efectivo"]["amount"]) == 10000
    assert refunds["Nequi"]["status"] == "PENDING" and Decimal(refunds["Nequi"]["amount"]) == 20000
    assert data["refund_status"] == "PENDING"                                      # not fully refunded yet


def test_23_void_blocked_after_a_partial_return(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = sell_arroz(ctx, 2)
    item = lines_of(client, sale.id)[0]["id"]
    do_return(client, sale.id, [(item, 1, True)])
    assert detail(client, sale.id)["allowed_actions"]["void"]["allowed"] is False
    assert do_void(client, sale.id).status_code == 409


# ---- 24-31. payment corrections + effective channel ------------------------------------------------------

def test_24_original_payment_never_changes_and_effective_channel_follows_the_chain(ctx):
    db, client, fake, product, channels, settings = ctx
    daviplata = add_channel(db, "Daviplata")
    sale = sell_arroz(ctx, 1, nequi)
    payment = payment_of(db, sale.id)
    assert do_correct(client, sale.id, payment.id, daviplata).status_code == 201
    data = detail(client, sale.id)
    entry = data["payments"][0]
    assert entry["original_channel_name"] == "Nequi" and entry["effective_channel_name"] == "Daviplata"
    assert entry["corrected"] is True and entry["corrections"][0]["reason"] == "Se marcó mal el medio"
    db.expire_all()
    assert db.get(Payment, payment.id).payment_channel_id == channels["Nequi"].id


def test_25_electronic_to_electronic_moves_no_cash(ctx):
    db, client, fake, product, channels, settings = ctx
    daviplata = add_channel(db, "Daviplata")
    sale = sell_arroz(ctx, 1, nequi)
    before = (cash_total(db), count(db, CashMovement))
    do_correct(client, sale.id, payment_of(db, sale.id).id, daviplata)
    assert (cash_total(db), count(db, CashMovement)) == before


def test_26_cash_to_nequi_adds_a_negative_compensation(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = sell_arroz(ctx, 1)
    assert do_correct(client, sale.id, payment_of(db, sale.id).id, channels["Nequi"]).status_code == 201
    movement = db.scalar(select(CashMovement).where(CashMovement.movement_type == CashMovementType.PAYMENT_CORRECTION))
    assert movement.amount == Decimal("-10000") and movement.reference_id == sale.id
    assert db.scalar(select(PaymentCorrection)).cash_movement_id == movement.id
    current = client.get(f"{API}/cash/current").json()
    assert Decimal(current["expected_cash"]) == 0 and Decimal(current["payment_corrections_cash"]) == -10000


def test_27_nequi_to_cash_adds_a_positive_compensation(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = sell_arroz(ctx, 1, nequi)
    do_correct(client, sale.id, payment_of(db, sale.id).id, channels["Efectivo"])
    assert cash_total(db) == Decimal("10000")
    assert Decimal(client.get(f"{API}/cash/current").json()["expected_cash"]) == 10000


def test_28_chain_compares_effective_before_and_after_without_duplicates(ctx):
    db, client, fake, product, channels, settings = ctx
    daviplata = add_channel(db, "Daviplata")
    sale = sell_arroz(ctx, 1)                                         # cash 10.000 in the drawer
    payment_id = payment_of(db, sale.id).id
    do_correct(client, sale.id, payment_id, channels["Nequi"])        # cash -> Nequi : -10.000
    do_correct(client, sale.id, payment_id, daviplata)                # Nequi -> Daviplata : 0
    do_correct(client, sale.id, payment_id, channels["Efectivo"])     # Daviplata -> cash : +10.000
    assert cash_total(db) == Decimal("10000")
    corrections = detail(client, sale.id)["payments"][0]["corrections"]
    assert [(c["sequence"], c["from_channel_name"], c["to_channel_name"], Decimal(c["cash_effect"])) for c in corrections] == [
        (1, "Efectivo", "Nequi", Decimal("-10000")), (2, "Nequi", "Daviplata", ZERO), (3, "Daviplata", "Efectivo", Decimal("10000"))]
    assert do_correct(client, sale.id, payment_id, channels["Efectivo"]).status_code == 409   # already Efectivo


def test_29_cash_moving_correction_needs_the_open_jornada_of_the_sale(ctx):
    db, client, fake, product, channels, settings = ctx
    daviplata = add_channel(db, "Daviplata")
    sale = sell_arroz(ctx, 1)
    payment_id = payment_of(db, sale.id).id
    close_session(client, 10000)
    response = do_correct(client, sale.id, payment_id, channels["Nequi"])
    assert response.status_code == 409 and response.json()["detail"]["code"] == "OPERATIONAL_SESSION_CLOSED"
    reopen(client)
    cash_action = detail(client, sale.id)["allowed_actions"]["correct_payment_cash"]          # the UI knows beforehand
    assert cash_action["allowed"] is False and "jornada ya cerrada" in cash_action["reason"]
    assert detail(client, sale.id)["allowed_actions"]["correct_payment"]["allowed"] is True   # e -> e still allowed
    assert do_correct(client, sale.id, payment_id, channels["Nequi"]).status_code == 409    # sale of a closed jornada
    assert count(db, PaymentCorrection) == 0 and count(db, CashMovement, CashMovement.movement_type ==
                                                        CashMovementType.PAYMENT_CORRECTION) == 0
    nequi_sale = sell_arroz(ctx, 1, nequi)
    assert do_correct(client, nequi_sale.id, payment_of(db, nequi_sale.id).id, daviplata).status_code == 201


def test_30_correction_idempotency_key(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = sell_arroz(ctx, 1)
    idem = key()
    payment_id = payment_of(db, sale.id).id
    for _ in range(2):
        assert do_correct(client, sale.id, payment_id, channels["Nequi"], idem=idem).status_code == 201
    assert count(db, PaymentCorrection) == 1 and cash_total(db) == ZERO


def test_31_reports_use_the_effective_channel(ctx):
    db, client, fake, product, channels, settings = ctx
    daviplata = add_channel(db, "Daviplata")
    sale = sell_arroz(ctx, 2, nequi)
    do_correct(client, sale.id, payment_of(db, sale.id).id, daviplata)
    names = lambda rows, key_: {r[key_] for r in rows}
    # Caja
    current = client.get(f"{API}/cash/current").json()
    assert names(current["payment_channels"], "channel_name") == {"Daviplata"}
    assert {m["payment_channel"] for m in current["financial_movements"] if m["type"] == "SALE"} == {"Daviplata"}
    # Resumen operativo
    channels_in_summary = {ch["channel_name"] for cat in summary(client)["categories"] for ch in cat["channels"]}
    assert channels_in_summary == {"Daviplata"}
    # Informes: rango + jornada viva
    dashboard = client.get(f"{API}/reports/dashboard?preset=today").json()
    assert names(dashboard["payment_channels"], "name") == {"Daviplata"}
    live = client.get(f"{API}/reports/current").json()
    assert names(live["report_data"]["payment_channels"], "channel_name") == {"Daviplata"}
    # Payment summary endpoint
    assert names(client.get(f"{API}/payments/summary").json()["channels"], "channel_name") == {"Daviplata"}
    # Fiscal payment method (invoice preview) follows the effective channel too
    from app.services.electronic_invoicing import payment_terms
    db.expire_all()
    terms, problem = payment_terms(db.get(Sale, sale.id))
    assert problem is None and terms["paymentMethod"] == daviplata.alegra_payment_method


def test_32_reconciliation_uses_effective_channel_for_block1_cash(ctx):
    db, client, fake, product, channels, settings = ctx
    p = block1_categories(db)
    sale = sell(client, db, channels, [("scan", p["CIGARROS"], 2)], cash(2000))
    assert recon(client)["by"]["CIGARROS"] == Decimal("2000")
    do_correct(client, sale.id, payment_of(db, sale.id).id, channels["Nequi"])
    assert recon(client)["by"]["CIGARROS"] == ZERO                       # it was really Nequi


def test_33_no_code_reads_the_historical_channel_directly():
    """Static guard: outside the resolver (and the model / supplier-payment code) nothing may read a sale
    payment's channel straight from Payment.payment_channel_id or payment.channel."""
    root = Path(__file__).resolve().parents[1] / "app"
    allowed = {"effective_payments.py", "entities.py", "purchases.py"}
    forbidden = [re.compile(r"(?<!Supplier)Payment\.payment_channel_id"),
                 re.compile(r"\b(payment|p)\.(channel\b|channel_name\b|affects_cash\b)")]
    offenders = []
    for path in root.rglob("*.py"):
        if path.name in allowed:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(pattern.search(line) for pattern in forbidden):
                offenders.append(f"{path.relative_to(root)}:{number}: {line.strip()}")
    assert offenders == []


# ---- 34-36. reports: gross / returns / net ---------------------------------------------------------------

def test_34_gross_returns_net_in_summary_and_reports(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = sell_arroz(ctx, 3)
    item = lines_of(client, sale.id)[0]["id"]
    do_return(client, sale.id, [(item, 1, True)])
    returns = summary(client)["returns"]
    assert Decimal(returns["gross_sales"]) == 30000 and Decimal(returns["returns_total"]) == 10000
    assert Decimal(returns["net_sales"]) == 20000 and Decimal(returns["refunds_cash"]) == 10000
    assert Decimal(summary(client)["sales_total"]) == 30000                   # gross stays gross
    dashboard = client.get(f"{API}/reports/dashboard?preset=today").json()["summary"]
    assert Decimal(dashboard["sales_total"]) == 30000 and Decimal(dashboard["returns_total"]) == 10000
    assert Decimal(dashboard["net_sales"]) == 20000
    live = client.get(f"{API}/reports/current").json()["report_data"]["returns"]
    assert Decimal(live["returns_total"]) == 10000 and Decimal(live["net_sales"]) == 20000


def test_35_return_belongs_to_the_jornada_where_it_was_made_and_closed_reports_do_not_change(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = sell_arroz(ctx, 2)
    close_session(client, 20000)
    closed_report = client.get(f"{API}/reports/current").json()
    reopen(client)
    item = lines_of(client, sale.id)[0]["id"]
    assert do_return(client, sale.id, [(item, 1, True)]).status_code == 201
    returns = summary(client)["returns"]                                       # current (new) jornada
    assert Decimal(returns["gross_sales"]) == 0 and Decimal(returns["returns_total"]) == 10000
    assert Decimal(returns["net_sales"]) == -10000
    report = client.get(f"{API}/reports/daily/{closed_report['daily_report_id']}").json()
    assert report["report_data"] == closed_report["report_data"] and Decimal(report["sales_total"]) == 20000
    first = db.scalars(select(CashSession).order_by(CashSession.opened_at)).first()
    assert first.closing_expected == Decimal("20000")                         # historical close untouched


def test_36_block1_math_intact_and_returns_shown_apart(ctx):
    db, client, fake, product, channels, settings = ctx
    p = block1_categories(db)
    sale = sell(client, db, channels, [("scan", p["CIGARROS"], 2), ("scan", p["LICORES"], 1)], cash(3000))
    before = recon(client)
    items = {line["product_name"]: line["id"] for line in lines_of(client, sale.id)}
    do_return(client, sale.id, [(items["Producto CIGARROS"], 1, True)])
    after = recon(client)
    assert after["by"] == before["by"] and after["pos_cash_subtotal"] == before["pos_cash_subtotal"]
    assert after["block1_subtotal"] == before["block1_subtotal"]
    aside = {r["category_code"]: Decimal(r["amount"]) for r in after["returns_cash_by_category"]}
    assert aside["CIGARROS"] == Decimal("1000") and aside["LICORES"] == ZERO
    assert Decimal(after["returns_cash_total"]) == Decimal("1000")


# ---- 37-38. electronic invoice safety --------------------------------------------------------------------

@pytest.mark.parametrize("status", [ElectronicInvoiceStatus.PENDING, ElectronicInvoiceStatus.PROCESSING,
                                    ElectronicInvoiceStatus.ISSUED, ElectronicInvoiceStatus.BLOCKED,
                                    ElectronicInvoiceStatus.ERROR])
def test_37_any_invoice_blocks_return_void_and_correction(ctx, status):
    db, client, fake, product, channels, settings = ctx
    sale = sell_arroz(ctx, 2)
    db.add(ElectronicInvoice(sale_id=sale.id, status=status)); db.commit()
    snapshot = (cash_total(db), count(db, InventoryMovement), count(db, SaleReturn), count(db, PaymentCorrection))
    data = detail(client, sale.id)
    expected = ("requiere una nota crédito" if status == ElectronicInvoiceStatus.ISSUED
                else "proceso de factura electrónica asociado")
    for action in ("return", "void", "correct_payment"):
        assert data["allowed_actions"][action]["allowed"] is False and expected in data["allowed_actions"][action]["reason"]
    item = data["lines"][0]["id"]
    for response in (do_return(client, sale.id, [(item, 1, True)]), do_void(client, sale.id),
                     do_correct(client, sale.id, payment_of(db, sale.id).id, channels["Nequi"])):
        assert response.status_code == 409 and expected in response.json()["detail"]["message"]
    assert (cash_total(db), count(db, InventoryMovement), count(db, SaleReturn), count(db, PaymentCorrection)) == snapshot
    db.expire_all()
    invoice = db.scalar(select(ElectronicInvoice))
    assert invoice.status == status and db.get(Sale, sale.id).requires_electronic_invoice is False
    assert fake.invoices == []                                                  # 0 POST to (fake) Alegra


def test_38_note_allowed_with_invoice(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = sell_arroz(ctx, 1)
    db.add(ElectronicInvoice(sale_id=sale.id, status=ElectronicInvoiceStatus.ISSUED)); db.commit()
    assert client.post(f"{API}/sales/{sale.id}/notes", json={"text": "Cliente pide nota crédito"}).status_code == 201
    assert detail(client, sale.id)["allowed_actions"]["note"]["allowed"] is True


# ---- 39-40. granel ---------------------------------------------------------------------------------------

def test_39_external_scale_granel_is_never_refunded_by_the_pos(ctx):
    db, client, fake, product, channels, settings = ctx
    manual_categories(db)
    sale = create_sale(db)
    add_manual(client, sale, "SUPER", 5000, "Mercado")
    add_manual(client, sale, "GRANEL", 10000, "Granel báscula")
    assert client.post(f"{API}/sales/{sale.id}/checkout", json={"channel_id": str(channels["Efectivo"].id),
                                                                "received_amount": "15000"}).status_code == 200
    data = detail(client, sale.id)
    assert [line["product_name"] for line in data["lines"]] == ["Mercado"]       # granel is not a POS line
    assert data["external_items"][0]["description"] == "Granel báscula"
    assert data["allowed_actions"]["void"]["allowed"] is False and "báscula" in data["allowed_actions"]["void"]["reason"]
    assert do_void(client, sale.id).status_code == 409
    response = do_return(client, sale.id, [(data["lines"][0]["id"], 1, True)])
    assert response.status_code == 201
    assert Decimal(response.json()["returns"][0]["refunds"][0]["amount"]) == 5000   # only the POS part
    assert cash_total(db) == ZERO                                                   # 5.000 in, 5.000 out


def test_40_pos_granel_paid_electronically_refunds_money_only(ctx):
    db, client, fake, product, channels, settings = ctx
    manual_categories(db)
    sale = create_sale(db)
    add_manual(client, sale, "GRANEL", 12000, "Granel electrónico")
    granel_electronic(client, sale)
    assert client.post(f"{API}/sales/{sale.id}/checkout", json={"channel_id": str(channels["Nequi"].id)}).status_code == 200
    line = lines_of(client, sale.id)[0]
    assert line["is_granel"] is True and line["restockable"] is False
    movements = count(db, InventoryMovement)
    data = do_return(client, sale.id, [(line["id"], 1, True)]).json()
    assert count(db, InventoryMovement) == movements and data["pending_refunds"][0]["channel_name"] == "Nequi"


# ---- 41. OPEN cancellation is unchanged ------------------------------------------------------------------

def test_41_open_sale_cancellation_unchanged_and_not_a_post_sale_operation(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = create_sale(db)
    client.post(f"{API}/sales/{sale.id}/items", json={"barcode": product.barcode, "quantity": "1"})
    assert client.post(f"{API}/sales/{sale.id}/cancel").json()["status"] == "CANCELLED"
    data = detail(client, sale.id)
    assert data["display_status"] == "CANCELLED" and data["allowed_actions"]["return"]["allowed"] is False
    assert do_void(client, sale.id).status_code == 409
    assert count(db, SaleReturn) == 0
    completed = sell_arroz(ctx, 1)
    assert client.post(f"{API}/sales/{completed.id}/cancel").status_code == 409      # COMPLETED never CANCELLED
