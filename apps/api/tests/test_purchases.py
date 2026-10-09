from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_db
from app.domain.enums import CashMovementType, InventoryMovementType, PaymentMethod, PurchaseStatus
from app.main import app
from app.models import CashMovement, CashSession, InventoryMovement, PaymentChannel, Product, Purchase, Supplier, SupplierPayment, User
from app.schemas.purchases import PurchaseCreate, PurchaseItemIn
from app.services.inventory import current_stock
from app.services.purchases import create_purchase, receive_purchase


@pytest.fixture()
def purchasing():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(name="Comprador")
        supplier = Supplier(name="Distribuciones ABC", active=True)
        product = Product(barcode="7702004001234", name="Café Colombiano",
                          sale_price=Decimal("18500"), unit_cost=Decimal("12000"),
                          supplier_id=None)
        cash = PaymentChannel(name="Efectivo", payment_type=PaymentMethod.CASH, affects_cash=True)
        transfer = PaymentChannel(name="Transferencia", payment_type=PaymentMethod.TRANSFER, affects_cash=False)
        db.add_all([user, supplier, product, cash, transfer]); db.flush(); db.add(CashSession(opening_amount=Decimal("200000"), opened_by=user.id))
        db.add(InventoryMovement(product_id=product.id,
                                 movement_type=InventoryMovementType.ADJUSTMENT,
                                 quantity=Decimal("8"), unit_cost=product.unit_cost,
                                 reference_id=None, user_id=user.id, reason="Saldo inicial"))
        db.commit()

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        try:
            yield db, TestClient(app), supplier, product
        finally:
            app.dependency_overrides.clear()


def purchase_body(supplier, product, quantity="10", cost="13000"):
    return {"supplier_id": str(supplier.id), "invoice_number": "FAC-100",
            "items": [{"product_id": str(product.id), "quantity": quantity,
                       "unit_cost": cost}]}


def make_draft(db, supplier, product, quantity="10", cost="13000"):
    return create_purchase(db, PurchaseCreate(
        supplier_id=supplier.id, invoice_number="FAC-100",
        items=[PurchaseItemIn(product_id=product.id, quantity=Decimal(quantity),
                              unit_cost=Decimal(cost))]))


def test_create_supplier(purchasing):
    _, client, _, _ = purchasing
    response = client.post("/api/v1/suppliers", json={"name": "Proveedor Nuevo",
                           "document": "900123456", "phone": "3001234567"})
    assert response.status_code == 201
    assert response.json()["name"] == "Proveedor Nuevo"
    assert response.json()["active"] is True


def test_edit_supplier(purchasing):
    _, client, supplier, _ = purchasing
    response = client.put(f"/api/v1/suppliers/{supplier.id}", json={
        "name": "Distribuciones ABC SAS", "document": "900111222",
        "phone": "6011234567", "email": "compras@abc.co",
        "address": None, "contact_name": "Ana", "notes": None})
    assert response.status_code == 200
    assert response.json()["contact_name"] == "Ana"


def test_create_draft_does_not_modify_stock(purchasing):
    db, client, supplier, product = purchasing
    before = current_stock(db, product.id)
    response = client.post("/api/v1/purchases", json=purchase_body(supplier, product))
    assert response.status_code == 201
    assert response.json()["status"] == "DRAFT"
    assert Decimal(response.json()["total"]) == Decimal("130000")
    assert current_stock(db, product.id) == before


def test_receive_increases_stock_and_creates_purchase_movement(purchasing):
    db, client, supplier, product = purchasing
    draft = client.post("/api/v1/purchases", json=purchase_body(supplier, product)).json()
    response = client.post(f"/api/v1/purchases/{draft['id']}/receive")
    assert response.status_code == 200
    assert response.json()["status"] == "RECEIVED"
    assert current_stock(db, product.id) == Decimal("18")
    movement = db.scalar(select(InventoryMovement).where(
        InventoryMovement.reference_id == UUID(draft["id"])))
    assert movement.movement_type == InventoryMovementType.PURCHASE
    assert movement.quantity == Decimal("10")
    assert movement.unit_cost == Decimal("13000")
    db.refresh(product)
    assert product.unit_cost == Decimal("13000")
    assert product.supplier_id == supplier.id


def test_double_receipt_is_blocked_without_changing_stock(purchasing):
    db, client, supplier, product = purchasing
    draft = client.post("/api/v1/purchases", json=purchase_body(supplier, product)).json()
    assert client.post(f"/api/v1/purchases/{draft['id']}/receive").status_code == 200
    assert client.post(f"/api/v1/purchases/{draft['id']}/receive").status_code == 409
    assert current_stock(db, product.id) == Decimal("18")
    assert len(db.scalars(select(InventoryMovement).where(
        InventoryMovement.reference_id == UUID(draft["id"]))).all()) == 1


@pytest.mark.parametrize(("quantity", "cost"), [("0", "13000"), ("-1", "13000"), ("1", "-1")])
def test_invalid_quantity_or_cost_is_rejected(purchasing, quantity, cost):
    _, client, supplier, product = purchasing
    response = client.post("/api/v1/purchases", json=purchase_body(
        supplier, product, quantity=quantity, cost=cost))
    assert response.status_code == 422


def test_empty_purchase_is_rejected(purchasing):
    _, client, supplier, _ = purchasing
    response = client.post("/api/v1/purchases", json={
        "supplier_id": str(supplier.id), "items": []})
    assert response.status_code == 422


def test_receive_with_payment_also_rejects_an_empty_purchase(purchasing):
    """Defensive parity with receive_purchase's own guard: the API can never produce an
    empty DRAFT (PurchaseCreate requires >=1 item), but if one existed by any other means,
    receiving it (with or without a combined payment) must still be refused, untouched."""
    db, client, supplier, product = purchasing
    empty = Purchase(supplier_id=supplier.id, status=PurchaseStatus.DRAFT,
                     created_by=db.scalar(select(User.id)), subtotal=Decimal("0"), total=Decimal("0"))
    db.add(empty); db.commit()
    cash = db.scalar(select(PaymentChannel).where(PaymentChannel.name == "Efectivo"))
    before = current_stock(db, product.id)
    response = client.post(f"/api/v1/purchases/{empty.id}/receive-with-payment",
                           json={"payment_channel_id": str(cash.id)})
    assert response.status_code == 422
    assert client.post(f"/api/v1/purchases/{empty.id}/receive").status_code == 422
    db.expire_all()
    assert db.get(Purchase, empty.id).status == PurchaseStatus.DRAFT
    assert current_stock(db, product.id) == before
    assert not db.scalars(select(SupplierPayment).where(SupplierPayment.purchase_id == empty.id)).all()
    assert not db.scalars(select(CashMovement)).all()


def test_cancel_draft_does_not_change_stock(purchasing):
    db, client, supplier, product = purchasing
    draft = client.post("/api/v1/purchases", json=purchase_body(supplier, product)).json()
    response = client.post(f"/api/v1/purchases/{draft['id']}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"
    assert current_stock(db, product.id) == Decimal("8")
    assert client.post(f"/api/v1/purchases/{draft['id']}/receive").status_code == 409


def test_inactive_supplier_cannot_be_used_for_new_purchase(purchasing):
    _, client, supplier, product = purchasing
    assert client.patch(f"/api/v1/suppliers/{supplier.id}/active",
                        json={"active": False}).status_code == 200
    response = client.post("/api/v1/purchases", json=purchase_body(supplier, product))
    assert response.status_code == 409


def test_receipt_is_atomic_when_commit_fails(purchasing, monkeypatch):
    db, _, supplier, product = purchasing
    purchase = make_draft(db, supplier, product)
    original_commit = db.commit

    def fail_commit():
        db.flush()
        raise RuntimeError("database failure")

    monkeypatch.setattr(db, "commit", fail_commit)
    with pytest.raises(RuntimeError):
        receive_purchase(db, purchase.id)
    monkeypatch.setattr(db, "commit", original_commit)
    db.expire_all()
    assert db.get(Purchase, purchase.id).status == PurchaseStatus.DRAFT
    assert current_stock(db, product.id) == Decimal("8")
    assert not db.scalars(select(InventoryMovement).where(
        InventoryMovement.reference_id == purchase.id)).all()


def test_received_purchase_appears_in_supplier_history(purchasing):
    _, client, supplier, product = purchasing
    draft = client.post("/api/v1/purchases", json=purchase_body(supplier, product)).json()
    client.post(f"/api/v1/purchases/{draft['id']}/receive")
    detail = client.get(f"/api/v1/suppliers/{supplier.id}").json()
    assert detail["purchases"][0]["id"] == draft["id"]
    assert Decimal(detail["total_purchased"]) == Decimal("130000")


def test_receiving_purchase_does_not_change_sale_price(purchasing):
    db, client, supplier, product = purchasing
    original_price = product.sale_price
    draft = client.post("/api/v1/purchases", json=purchase_body(supplier, product)).json()
    assert client.post(f"/api/v1/purchases/{draft['id']}/receive").status_code == 200
    db.refresh(product)
    assert product.sale_price == original_price


def test_received_purchase_can_be_paid_once_and_cash_reduces_expected(purchasing):
    db, client, supplier, product = purchasing
    draft = client.post("/api/v1/purchases", json=purchase_body(supplier, product)).json()
    assert client.post(f"/api/v1/purchases/{draft['id']}/receive").status_code == 200
    cash = db.scalar(select(PaymentChannel).where(PaymentChannel.name == "Efectivo"))
    response = client.post(f"/api/v1/purchases/{draft['id']}/payment", json={"payment_channel_id": str(cash.id)})
    assert response.status_code == 201
    assert Decimal(response.json()["payment"]["amount"]) == Decimal("130000")
    assert Decimal(client.get("/api/v1/cash/current").json()["expected_cash"]) == Decimal("70000")
    assert db.scalar(select(CashMovement).where(CashMovement.movement_type == CashMovementType.SUPPLIER_PAYMENT)).amount == Decimal("-130000")
    assert client.post(f"/api/v1/purchases/{draft['id']}/payment", json={"payment_channel_id": str(cash.id)}).status_code == 409


def test_transfer_payment_does_not_require_cash_or_reduce_it(purchasing):
    db, client, supplier, product = purchasing
    draft = client.post("/api/v1/purchases", json=purchase_body(supplier, product)).json()
    client.post(f"/api/v1/purchases/{draft['id']}/receive")
    transfer = db.scalar(select(PaymentChannel).where(PaymentChannel.name == "Transferencia"))
    response = client.post(f"/api/v1/purchases/{draft['id']}/payment", json={"payment_channel_id": str(transfer.id), "reference": "TRX-1"})
    assert response.status_code == 201
    assert not db.scalars(select(CashMovement)).all()
    assert db.scalar(select(SupplierPayment).where(SupplierPayment.purchase_id == UUID(draft["id"]))).supplier_id == supplier.id


def test_receive_with_payment_cash_is_atomic_and_updates_cash(purchasing):
    db, client, supplier, product = purchasing
    draft = client.post("/api/v1/purchases", json=purchase_body(supplier, product)).json()
    cash = db.scalar(select(PaymentChannel).where(PaymentChannel.name == "Efectivo"))
    response = client.post(f"/api/v1/purchases/{draft['id']}/receive-with-payment", json={"payment_channel_id": str(cash.id)})
    assert response.status_code == 201
    assert response.json()["status"] == "RECEIVED" and response.json()["payment"]["channel_name"] == "Efectivo"
    assert len(db.scalars(select(InventoryMovement).where(InventoryMovement.reference_id == UUID(draft["id"]))).all()) == 1
    assert db.scalar(select(SupplierPayment).where(SupplierPayment.purchase_id == UUID(draft["id"]))).amount == Decimal("130000")
    assert db.scalar(select(CashMovement).where(CashMovement.reference_id == UUID(draft["id"]))).amount == Decimal("-130000")
    assert Decimal(client.get("/api/v1/cash/current").json()["expected_cash"]) == Decimal("70000")


def test_receive_with_payment_transfer_does_not_affect_cash_or_duplicate(purchasing):
    db, client, supplier, product = purchasing
    draft = client.post("/api/v1/purchases", json=purchase_body(supplier, product)).json()
    transfer = db.scalar(select(PaymentChannel).where(PaymentChannel.name == "Transferencia"))
    response = client.post(f"/api/v1/purchases/{draft['id']}/receive-with-payment", json={"payment_channel_id": str(transfer.id), "reference":"TRX-1"})
    assert response.status_code == 201 and response.json()["payment"]["reference"] == "TRX-1"
    assert not db.scalars(select(CashMovement).where(CashMovement.reference_id == UUID(draft["id"]))).all()
    assert Decimal(client.get("/api/v1/cash/current").json()["expected_cash"]) == Decimal("200000")
    assert client.post(f"/api/v1/purchases/{draft['id']}/receive-with-payment", json={"payment_channel_id": str(transfer.id)}).status_code == 409


def test_receive_with_payment_requires_open_journey(purchasing):
    db, client, supplier, product = purchasing
    draft = client.post("/api/v1/purchases", json=purchase_body(supplier, product)).json()
    session = db.scalar(select(CashSession)); session.closed_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc); db.commit()
    cash = db.scalar(select(PaymentChannel).where(PaymentChannel.name == "Efectivo"))
    response = client.post(f"/api/v1/purchases/{draft['id']}/receive-with-payment", json={"payment_channel_id": str(cash.id)})
    assert response.status_code == 409
    assert response.json()["detail"] == {"code":"OPERATIONAL_SESSION_CLOSED", "message":"La jornada está cerrada. Abre la caja para registrar esta operación."}


def test_receive_with_payment_uses_transfer_type_not_channel_name(purchasing):
    db, client, supplier, product = purchasing
    transfer = db.scalar(select(PaymentChannel).where(
        PaymentChannel.payment_type == PaymentMethod.TRANSFER))
    transfer.name = "Banco principal"
    db.commit()
    draft = client.post("/api/v1/purchases", json=purchase_body(supplier, product)).json()
    response = client.post(f"/api/v1/purchases/{draft['id']}/receive-with-payment", json={
        "payment_channel_id": str(transfer.id), "reference": "TRX-SEMANTIC"})
    assert response.status_code == 201
    assert response.json()["payment"]["channel_name"] == "Banco principal"
    assert not db.scalars(select(CashMovement).where(
        CashMovement.reference_id == UUID(draft["id"]))).all()


def test_draft_purchase_cannot_be_paid(purchasing):
    db, client, supplier, product = purchasing
    draft = client.post("/api/v1/purchases", json=purchase_body(supplier, product)).json()
    cash = db.scalar(select(PaymentChannel).where(PaymentChannel.name == "Efectivo"))
    assert client.post(f"/api/v1/purchases/{draft['id']}/payment", json={"payment_channel_id": str(cash.id)}).status_code == 409


def test_new_receipt_updates_current_cost_without_changing_historical_item(purchasing):
    db, client, supplier, product = purchasing
    first = client.post("/api/v1/purchases", json=purchase_body(supplier, product, cost="12500")).json()
    client.post(f"/api/v1/purchases/{first['id']}/receive")
    second = client.post("/api/v1/purchases", json=purchase_body(supplier, product, cost="13000")).json()
    client.post(f"/api/v1/purchases/{second['id']}/receive")
    first_detail = client.get(f"/api/v1/purchases/{first['id']}").json()
    db.refresh(product)
    assert Decimal(first_detail["items"][0]["unit_cost"]) == Decimal("12500")
    assert product.unit_cost == Decimal("13000")
    inventory = client.get(f"/api/v1/inventory/{product.id}").json()
    assert Decimal(inventory["last_purchase_cost"]) == Decimal("13000")


def test_apply_suggested_price_is_explicit_and_preserves_purchase_cost(purchasing):
    db, client, supplier, product = purchasing
    draft = client.post("/api/v1/purchases", json=purchase_body(supplier, product)).json()
    response = client.post(f"/api/v1/products/{product.id}/sale-price/suggested", json={
        "unit_cost": "13000", "target_margin_percent": "30"})
    assert response.status_code == 200
    assert Decimal(response.json()["sale_price"]) == Decimal("19000")
    db.refresh(product)
    assert product.sale_price == Decimal("19000")
    purchase_detail = client.get(f"/api/v1/purchases/{draft['id']}").json()
    assert Decimal(purchase_detail["items"][0]["unit_cost"]) == Decimal("13000")


@pytest.mark.parametrize("margin", ["100", "-1"])
def test_apply_price_rejects_invalid_margin(purchasing, margin):
    _, client, _, product = purchasing
    response = client.post(f"/api/v1/products/{product.id}/sale-price/suggested", json={
        "unit_cost": "13000", "target_margin_percent": margin})
    assert response.status_code == 422
