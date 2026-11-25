"""Scheduled purchase contracts, using only an in-memory test database."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_db
from app.domain.enums import InventoryMovementType, PaymentMethod
from app.main import app
from app.models import CashMovement, CashSession, InventoryMovement, PaymentChannel, Product, Purchase, Supplier, SupplierPayment, User
from app.services.inventory import current_stock, inventory_products_query
from app.schemas.purchases import ReceiveComparisonIn
from app.services.purchases import receive_purchase


@pytest.fixture()
def shop():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(name="Operador de prueba")
        supplier = Supplier(name="Proveedor ficticio")
        product = Product(name="Producto ficticio", barcode="7701234567890",
                          sale_price=Decimal("20000"), unit_cost=Decimal("10000"))
        cash = PaymentChannel(name="Efectivo", payment_type=PaymentMethod.CASH, affects_cash=True)
        digital = PaymentChannel(name="Transferencia", payment_type=PaymentMethod.TRANSFER, affects_cash=False)
        db.add_all([user, supplier, product, cash, digital]); db.flush()
        db.add(CashSession(opening_amount=Decimal("200000"), opened_by=user.id))
        db.add(InventoryMovement(product_id=product.id, movement_type=InventoryMovementType.INITIAL_STOCK,
                                 quantity=Decimal("5"), unit_cost=product.unit_cost, user_id=user.id))
        db.commit()

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        try:
            yield db, TestClient(app), supplier, product, cash, digital
        finally:
            app.dependency_overrides.clear()
    engine.dispose()


def draft(client, supplier, product, invoice="ORD-1", quantity="3", cost="12000"):
    response = client.post("/api/v1/purchases", json={"supplier_id": str(supplier.id),
        "invoice_number": invoice, "items": [{"product_id": str(product.id),
        "quantity": quantity, "unit_cost": cost}]})
    assert response.status_code == 201, response.text
    return response.json()


def schedule(client, purchase, terms="CASH_ON_DELIVERY", delivery="2026-12-10", due=None, **extra):
    body = {"expected_delivery_date": delivery, "payment_terms": terms,
            "payment_due_date": due, **extra}
    return client.post(f"/api/v1/purchases/{purchase['id']}/schedule", json=body)


def commitments(client, start="2026-12-01", end="2027-01-31"):
    response = client.get("/api/v1/purchases/commitments", params={"date_from": start, "date_to": end})
    assert response.status_code == 200, response.text
    return response.json()


def comparison(purchase, *, quantity="3", cost="12000", reason=None, request_id=None):
    return {"request_id": str(request_id or uuid4()), "items": [{
        "product_id": purchase["items"][0]["product_id"], "received_quantity": quantity,
        "actual_unit_cost": cost, "difference_reason": reason}]}


def test_scheduling_freezes_original_order_without_inventory_cash_or_payment(shop):
    db, client, supplier, product, _, _ = shop
    purchase = draft(client, supplier, product)
    stock_before = current_stock(db, product.id)
    cash_before = client.get("/api/v1/cash/current").json()["expected_cash"]
    movement_count = db.scalar(select(func.count()).select_from(InventoryMovement))
    body = {"expected_delivery_date": "2026-12-10", "payment_terms": "CASH_ON_DELIVERY",
            "offer_notes": "Oferta de diciembre", "offers": [{"product_id": str(product.id),
            "list_unit_cost": "13000", "discount_per_unit": "1000", "note": "Descuento por unidad"}]}
    first = client.post(f"/api/v1/purchases/{purchase['id']}/schedule", json=body)
    assert first.status_code == 200, first.text
    data = first.json()
    assert data["status"] == "SCHEDULED" and Decimal(data["estimated_total"]) == Decimal("36000")
    assert Decimal(data["items"][0]["ordered_quantity"]) == Decimal("3")
    assert Decimal(data["items"][0]["ordered_unit_cost"]) == Decimal("12000")
    assert data["items"][0]["offer_discount_per_unit"] == "1000.00"
    assert data["paid_amount"] == "0" and data["outstanding_amount"] is None
    assert current_stock(db, product.id) == stock_before
    assert db.scalar(select(func.count()).select_from(InventoryMovement)) == movement_count
    assert db.scalar(select(func.count()).select_from(CashMovement)) == 0
    assert db.scalar(select(func.count()).select_from(SupplierPayment)) == 0
    assert client.get("/api/v1/cash/current").json()["expected_cash"] == cash_before
    again = client.post(f"/api/v1/purchases/{purchase['id']}/schedule", json=body)
    assert again.status_code == 200 and again.json()["scheduled_at"] == data["scheduled_at"]
    assert schedule(client, purchase, delivery="2026-12-11").status_code == 409
    assert client.put(f"/api/v1/purchases/{purchase['id']}", json={"supplier_id": str(supplier.id),
        "items": [{"product_id": str(product.id), "quantity": "4", "unit_cost": "12000"}]}).status_code == 409


@pytest.mark.parametrize("body", [
    {"expected_delivery_date": "2026-12-10", "payment_terms": "CREDIT"},
    {"expected_delivery_date": "2026-12-10", "payment_terms": "CREDIT", "payment_due_date": "2026-12-10"},
    {"expected_delivery_date": "2026-12-10", "payment_terms": "CASH_ON_DELIVERY", "payment_due_date": "2026-12-20"},
    {"expected_delivery_date": "2026-12-10", "payment_terms": "INVALID"},
])
def test_invalid_schedule_leaves_draft_unchanged(shop, body):
    _, client, supplier, product, _, _ = shop
    purchase = draft(client, supplier, product)
    response = client.post(f"/api/v1/purchases/{purchase['id']}/schedule", json=body)
    assert response.status_code == 422
    after = client.get(f"/api/v1/purchases/{purchase['id']}").json()
    assert after["status"] == "DRAFT" and after["scheduled_at"] is None


def test_bad_offer_and_invoice_duplicate_are_rejected(shop):
    _, client, supplier, product, _, _ = shop
    first = draft(client, supplier, product, invoice="FACT-1")
    second = draft(client, supplier, product, invoice=" fact-1 ")
    assert schedule(client, first, offers=[{"product_id": str(product.id),
        "list_unit_cost": "13000", "discount_per_unit": "500"}]).status_code == 422
    assert schedule(client, first, offers=[{"product_id": str(product.id),
        "list_unit_cost": "13000"}]).status_code == 422
    assert schedule(client, first).status_code == 409
    assert schedule(client, second).status_code == 409
    assert client.get(f"/api/v1/purchases/{first['id']}").json()["status"] == "DRAFT"


def test_agenda_and_commitments_are_dated_and_separate_modes(shop):
    _, client, supplier, product, _, _ = shop
    cash = draft(client, supplier, product, invoice=None, cost="10000")
    digital = draft(client, supplier, product, invoice=None, cost="11000")
    credit = draft(client, supplier, product, invoice=None, cost="12000")
    assert schedule(client, cash, delivery="2026-12-08").status_code == 200
    assert schedule(client, digital, terms="DIGITAL_ON_DELIVERY", delivery="2026-12-12").status_code == 200
    assert schedule(client, credit, terms="CREDIT", delivery="2026-12-20", due="2027-01-15").status_code == 200
    agenda = client.get("/api/v1/purchases/agenda", params={"as_of": "2026-12-10", "horizon_days": 30})
    assert agenda.status_code == 200
    assert [row["alert"] for row in agenda.json()] == ["OVERDUE", "DUE_SOON", "UPCOMING"]
    rows = commitments(client)["days"]
    assert [(row["date"], Decimal(row["estimated_cash"]), Decimal(row["estimated_digital"]),
             Decimal(row["estimated_credit"]))
            for row in rows] == [
        ("2026-12-08", Decimal("30000"), Decimal("0"), Decimal("0")),
        ("2026-12-12", Decimal("0"), Decimal("33000"), Decimal("0")),
        ("2027-01-15", Decimal("0"), Decimal("0"), Decimal("36000"))]
    assert Decimal(commitments(client)["estimated_total"]) == Decimal("99000")
    assert Decimal(commitments(client)["actual_outstanding_total"]) == Decimal("0")
    assert client.post(f"/api/v1/purchases/{cash['id']}/cancel").status_code == 200
    assert Decimal(commitments(client)["estimated_total"]) == Decimal("69000")
    assert len(client.get("/api/v1/purchases/agenda", params={"as_of": "2026-12-10"}).json()) == 2


def test_credit_receipt_becomes_actual_obligation_then_full_payment_removes_it(shop):
    db, client, supplier, product, cash, digital = shop
    purchase = draft(client, supplier, product)
    assert schedule(client, purchase, terms="CREDIT", due="2027-01-15").status_code == 200
    before = commitments(client)
    assert Decimal(before["estimated_total"]) == Decimal("36000")
    assert Decimal(before["actual_outstanding_total"]) == Decimal("0")
    assert client.post(f"/api/v1/purchases/{purchase['id']}/receive-with-payment",
        json={"payment_channel_id": str(cash.id), "comparison": comparison(purchase)}).status_code == 409
    received = client.post(f"/api/v1/purchases/{purchase['id']}/receive", json=comparison(purchase))
    assert received.status_code == 200, received.text
    assert received.json()["status"] == "RECEIVED"
    assert received.json()["items"][0]["ordered_quantity"] == "3.000"
    assert received.json()["items"][0]["ordered_unit_cost"] == "12000.00"
    assert received.json()["outstanding_amount"] == "36000.00"
    assert current_stock(db, product.id) == Decimal("8")
    assert client.post(f"/api/v1/purchases/{purchase['id']}/receive").status_code == 409
    after = commitments(client)
    assert Decimal(after["estimated_total"]) == Decimal("0")
    assert Decimal(after["actual_outstanding_total"]) == Decimal("36000")
    assert Decimal(after["days"][0]["actual_outstanding_credit"]) == Decimal("36000")
    paid = client.post(f"/api/v1/purchases/{purchase['id']}/payment",
        json={"payment_channel_id": str(digital.id), "reference": "FICT-1"})
    assert paid.status_code == 201 and paid.json()["outstanding_amount"] == "0.00"
    assert Decimal(commitments(client)["actual_outstanding_total"]) == Decimal("0")
    assert db.scalar(select(func.count()).select_from(CashMovement)) == 0


@pytest.mark.parametrize("terms,pay_cash", [("CASH_ON_DELIVERY", True), ("DIGITAL_ON_DELIVERY", False)])
def test_delivery_payment_is_atomic_and_matches_planned_mode(shop, terms, pay_cash):
    db, client, supplier, product, cash, digital = shop
    purchase = draft(client, supplier, product)
    assert schedule(client, purchase, terms=terms).status_code == 200
    assert client.post(f"/api/v1/purchases/{purchase['id']}/receive").status_code == 422
    wrong = digital if pay_cash else cash
    right = cash if pay_cash else digital
    assert client.post(f"/api/v1/purchases/{purchase['id']}/receive-with-payment",
        json={"payment_channel_id": str(wrong.id), "comparison": comparison(purchase)}).status_code == 422
    assert current_stock(db, product.id) == Decimal("5")
    compared = comparison(purchase)
    response = client.post(f"/api/v1/purchases/{purchase['id']}/receive-with-payment",
        json={"payment_channel_id": str(right.id), "comparison": compared})
    assert response.status_code == 201, response.text
    assert response.json()["items"][0]["ordered_quantity"] == "3.000"
    assert response.json()["outstanding_amount"] == "0.00"
    assert client.post(f"/api/v1/purchases/{purchase['id']}/receive-with-payment",
        json={"payment_channel_id": str(right.id), "comparison": compared}).status_code == 201
    assert current_stock(db, product.id) == Decimal("8")
    assert db.scalar(select(func.count()).select_from(CashMovement)) == (1 if pay_cash else 0)
    assert Decimal(commitments(client)["estimated_total"]) == Decimal("0")


def test_legacy_purchase_contract_and_unclassified_debt_remain_intact(shop):
    _, client, supplier, product, _, digital = shop
    purchase = draft(client, supplier, product)
    assert purchase["scheduled_at"] is None and purchase["estimated_total"] is None
    received = client.post(f"/api/v1/purchases/{purchase['id']}/receive")
    assert received.status_code == 200
    assert received.json()["items"][0]["ordered_quantity"] is None
    assert received.json()["outstanding_amount"] == "36000.00"
    today = date.today()
    start, end = (today - timedelta(days=2)).isoformat(), (today + timedelta(days=2)).isoformat()
    overdue = commitments(client, start=start, end=end)
    assert Decimal(overdue["actual_outstanding_total"]) == Decimal("36000")
    assert Decimal(overdue["days"][0]["actual_outstanding_unclassified"]) == Decimal("36000")
    assert client.post(f"/api/v1/purchases/{purchase['id']}/payment",
        json={"payment_channel_id": str(digital.id)}).status_code == 201
    assert Decimal(commitments(client, start=start, end=end)["actual_outstanding_total"]) == Decimal("0")


def test_commitment_range_validation(shop):
    _, client, _, _, _, _ = shop
    for start, end in (("2026-12-12", "2026-12-10"), ("2026-01-01", "2027-12-31")):
        response = client.get("/api/v1/purchases/commitments", params={"date_from": start, "date_to": end})
        assert response.status_code == 422


def test_creation_key_reuses_draft_and_rejects_changed_body(shop):
    db, client, supplier, product, _, _ = shop
    key = str(uuid4())
    body = {"supplier_id": str(supplier.id), "items": [{
        "product_id": str(product.id), "quantity": "2", "unit_cost": "12000"}]}
    first = client.post("/api/v1/purchases", json=body, headers={"Idempotency-Key": key})
    again = client.post("/api/v1/purchases", json=body, headers={"Idempotency-Key": key})
    assert first.status_code == again.status_code == 201
    assert first.json()["id"] == again.json()["id"]
    changed = {**body, "items": [{**body["items"][0], "quantity": "3"}]}
    assert client.post("/api/v1/purchases", json=changed,
                       headers={"Idempotency-Key": key}).status_code == 409
    assert db.scalar(select(func.count()).select_from(Purchase)) == 1


def test_compared_credit_receipt_preserves_order_and_shows_overdue_real_amount(shop):
    db, client, supplier, product, _, digital = shop
    purchase = draft(client, supplier, product)
    assert schedule(client, purchase, terms="CREDIT", due="2027-01-15").status_code == 200
    payload = comparison(purchase, quantity="2", cost="11000", reason="Llegó menos y cambió el precio")
    response = client.post(f"/api/v1/purchases/{purchase['id']}/receive", json=payload)
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert (item["ordered_quantity"], item["ordered_unit_cost"]) == ("3.000", "12000.00")
    assert (item["quantity"], item["unit_cost"], item["line_total"]) == ("2.000", "11000.00", "22000.00")
    assert (item["quantity_difference"], item["unit_cost_difference"], item["amount_difference"]) == (
        "-1.000", "-1000.00", "-14000.00")
    assert response.json()["estimated_total"] == "36000.00"
    assert response.json()["outstanding_amount"] == "22000.00"
    assert current_stock(db, product.id) == Decimal("7")
    assert db.scalar(select(func.count()).select_from(SupplierPayment)) == 0
    assert db.scalar(select(func.count()).select_from(CashMovement)) == 0
    assert commitments(client)["estimated_total"] == "0"
    assert commitments(client)["actual_outstanding_total"] == "22000.00"
    overdue = commitments(client, start="2027-01-16", end="2027-01-31")
    assert overdue["actual_outstanding_total"] == "0"
    assert overdue["overdue_outstanding_credit"] == "22000.00"
    assert overdue["overdue_outstanding_total"] == "22000.00"
    retry = client.post(f"/api/v1/purchases/{purchase['id']}/receive", json=payload)
    assert retry.status_code == 200 and retry.json()["received_at"] == response.json()["received_at"]
    assert current_stock(db, product.id) == Decimal("7")
    assert client.post(f"/api/v1/purchases/{purchase['id']}/receive", json={
        **payload, "items": [{**payload["items"][0], "received_quantity": "1"}]}).status_code == 409
    assert client.post(f"/api/v1/purchases/{purchase['id']}/receive", json={
        **payload, "request_id": str(uuid4())}).status_code == 409
    paid = client.post(f"/api/v1/purchases/{purchase['id']}/payment",
                       json={"payment_channel_id": str(digital.id)})
    assert paid.status_code == 201 and paid.json()["payment"]["amount"] == "22000.00"
    assert commitments(client, start="2027-01-16", end="2027-01-31")["overdue_outstanding_total"] == "0"


def test_zero_line_keeps_order_but_does_not_move_stock_or_last_cost(shop):
    db, client, supplier, product, _, _ = shop
    second = Product(name="Sin entrega", barcode="7701234567891", sale_price=Decimal("5000"),
                     unit_cost=Decimal("900"))
    db.add(second); db.commit()
    body = {"supplier_id": str(supplier.id), "items": [
        {"product_id": str(product.id), "quantity": "2", "unit_cost": "12000"},
        {"product_id": str(second.id), "quantity": "1", "unit_cost": "1000"}]}
    purchase = client.post("/api/v1/purchases", json=body).json()
    assert schedule(client, purchase, terms="CREDIT", due="2027-01-15").status_code == 200
    payload = {"request_id": str(uuid4()), "items": [
        {"product_id": str(product.id), "received_quantity": "2", "actual_unit_cost": "12000"},
        {"product_id": str(second.id), "received_quantity": "0", "difference_reason": "No entregado"}]}
    response = client.post(f"/api/v1/purchases/{purchase['id']}/receive", json=payload)
    assert response.status_code == 200, response.text
    zero = next(item for item in response.json()["items"] if item["product_id"] == str(second.id))
    assert zero["ordered_quantity"] == "1.000" and zero["quantity"] == "0.000"
    assert zero["actual_unit_cost"] is None and zero["line_total"] == "0.00"
    assert current_stock(db, second.id) == 0
    assert db.scalar(select(func.count()).select_from(InventoryMovement).where(
        InventoryMovement.product_id == second.id)) == 0
    assert db.get(Product, second.id).unit_cost == Decimal("900")
    row = db.execute(inventory_products_query().where(Product.id == second.id)).one()
    assert row.last_purchase_cost is None


def test_all_zero_and_missing_reason_leave_order_scheduled(shop):
    db, client, supplier, product, _, _ = shop
    purchase = draft(client, supplier, product)
    assert schedule(client, purchase, terms="CREDIT", due="2027-01-15").status_code == 200
    for payload in (comparison(purchase, quantity="0", cost=None, reason="Nada llegó"),
                    comparison(purchase, quantity="2", cost="11000")):
        response = client.post(f"/api/v1/purchases/{purchase['id']}/receive", json=payload)
        assert response.status_code == 422
    assert client.get(f"/api/v1/purchases/{purchase['id']}").json()["status"] == "SCHEDULED"
    assert current_stock(db, product.id) == Decimal("5")
    assert db.scalar(select(func.count()).select_from(SupplierPayment)) == 0


@pytest.mark.parametrize("terms,use_cash", [("CASH_ON_DELIVERY", True),
                                            ("DIGITAL_ON_DELIVERY", False)])
def test_compared_delivery_payment_uses_actual_total_once(shop, terms, use_cash):
    db, client, supplier, product, cash, digital = shop
    purchase = draft(client, supplier, product)
    assert schedule(client, purchase, terms=terms).status_code == 200
    payload = {"payment_channel_id": str(cash.id if use_cash else digital.id),
               "comparison": comparison(purchase, quantity="2", cost="11000", reason="Diferencia de factura")}
    before = client.get("/api/v1/cash/current").json()["expected_cash"]
    first = client.post(f"/api/v1/purchases/{purchase['id']}/receive-with-payment", json=payload)
    assert first.status_code == 201, first.text
    assert first.json()["total"] == first.json()["payment"]["amount"] == "22000.00"
    again = client.post(f"/api/v1/purchases/{purchase['id']}/receive-with-payment", json=payload)
    assert again.status_code == 201 and again.json()["id"] == first.json()["id"]
    assert current_stock(db, product.id) == Decimal("7")
    assert db.scalar(select(func.count()).select_from(SupplierPayment)) == 1
    assert db.scalar(select(func.count()).select_from(CashMovement)) == (1 if use_cash else 0)
    after = client.get("/api/v1/cash/current").json()["expected_cash"]
    assert Decimal(after) == Decimal(before) - (Decimal("22000") if use_cash else 0)


def test_duplicate_invoice_at_receipt_is_rejected_without_side_effects(shop):
    db, client, supplier, product, _, _ = shop
    purchase = draft(client, supplier, product, invoice=None)
    assert schedule(client, purchase, terms="CREDIT", due="2027-01-15").status_code == 200
    draft(client, supplier, product, invoice="FACT-REPETIDA")
    payload = comparison(purchase)
    payload["invoice_number"] = " fact-repetida "
    response = client.post(f"/api/v1/purchases/{purchase['id']}/receive", json=payload)
    assert response.status_code == 409
    assert current_stock(db, product.id) == Decimal("5")
    assert client.get(f"/api/v1/purchases/{purchase['id']}").json()["status"] == "SCHEDULED"


def test_line_rounding_is_half_up_and_cash_failure_rolls_back(shop):
    db, client, supplier, product, cash, _ = shop
    purchase = draft(client, supplier, product, invoice=None, quantity="1.555", cost="0.01")
    assert purchase["total"] == "0.02"
    assert schedule(client, purchase).status_code == 200
    compared = comparison(purchase, quantity="1.555", cost="0.01")
    success = client.post(f"/api/v1/purchases/{purchase['id']}/receive-with-payment",
                          json={"payment_channel_id": str(cash.id), "comparison": compared})
    assert success.status_code == 201 and success.json()["total"] == "0.02"
    expensive = draft(client, supplier, product, invoice=None, quantity="3", cost="100000")
    assert schedule(client, expensive).status_code == 200
    response = client.post(f"/api/v1/purchases/{expensive['id']}/receive-with-payment", json={
        "payment_channel_id": str(cash.id),
        "comparison": comparison(expensive, quantity="3", cost="100000")})
    assert response.status_code == 409
    assert client.get(f"/api/v1/purchases/{expensive['id']}").json()["status"] == "SCHEDULED"
    assert db.scalar(select(func.count()).select_from(SupplierPayment)) == 1
    assert current_stock(db, product.id) == Decimal("6.555")


def test_compared_receipt_rolls_back_after_database_flush_failure(shop, monkeypatch):
    db, client, supplier, product, _, _ = shop
    purchase = draft(client, supplier, product)
    assert schedule(client, purchase, terms="CREDIT", due="2027-01-15").status_code == 200
    real_commit = db.commit

    def fail_commit():
        db.flush()
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(db, "commit", fail_commit)
    with pytest.raises(RuntimeError):
        receive_purchase(db, UUID(purchase["id"]), ReceiveComparisonIn(**comparison(
            purchase, quantity="2", cost="11000", reason="Entrega incompleta")))
    monkeypatch.setattr(db, "commit", real_commit)
    db.expire_all()
    saved = db.get(Purchase, UUID(purchase["id"]))
    assert saved.status.value == "SCHEDULED" and saved.total == Decimal("36000")
    assert saved.items[0].quantity == Decimal("3")
    assert current_stock(db, product.id) == Decimal("5")
    assert db.scalar(select(func.count()).select_from(InventoryMovement).where(
        InventoryMovement.reference_id == UUID(purchase["id"]))) == 0
