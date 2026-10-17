from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_db
from app.domain.enums import (CashMovementType, InventoryMovementType,
                              PaymentGroup, PaymentMethod)
from app.main import app
from app.models import (CashMovement, CashSession, Expense, InternalConsumption, InternalConsumptionItem,
                        InventoryMovement, OperationalCategory, Payment, PaymentChannel,
                        Product, Sale, User)
from app.services.inventory import current_stock
from app.services.pos import add_item, create_sale


@pytest.fixture()
def operations_context():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(name="Cajero")
        categories = [
            OperationalCategory(code="CIGARROS", name="Cigarros", display_order=10),
            OperationalCategory(code="MASCOTAS_CON_CODIGO", name="Mascotas con código", display_order=20),
            OperationalCategory(code="MASCOTAS_SIN_CODIGO", name="Mascotas sin código / Bultos", display_order=30),
            OperationalCategory(code="LICORES", name="Licores", display_order=40),
            OperationalCategory(code="SUPER", name="Super", display_order=50),
            OperationalCategory(code="SIN_CLASIFICAR", name="Sin clasificar", display_order=999),
        ]
        channels = [
            PaymentChannel(name="Efectivo", payment_type=PaymentMethod.CASH,
                           payment_group=PaymentGroup.CASH, affects_cash=True, display_order=10),
            PaymentChannel(name="Nequi", payment_type=PaymentMethod.TRANSFER,
                           payment_group=PaymentGroup.ELECTRONIC, affects_cash=False, display_order=20),
            PaymentChannel(name="Daviplata", payment_type=PaymentMethod.TRANSFER,
                           payment_group=PaymentGroup.ELECTRONIC, affects_cash=False, display_order=30),
            PaymentChannel(name="Datáfono", payment_type=PaymentMethod.CARD,
                           payment_group=PaymentGroup.ELECTRONIC, affects_cash=False, display_order=40),
        ]
        db.add_all([user, *categories, *channels]); db.flush()
        products = {
            "cigarros": Product(barcode="op-1", name="Cigarrillos", sale_price=Decimal("40000"), unit_cost=Decimal("20000"), operational_category_id=categories[0].id),
            "mascotas": Product(barcode="op-2", name="Comida perro", sale_price=Decimal("30000"), unit_cost=Decimal("15000"), operational_category_id=categories[1].id),
            "licores": Product(barcode="op-3", name="Ron", sale_price=Decimal("60000"), unit_cost=Decimal("30000"), operational_category_id=categories[3].id),
            "super": Product(barcode="op-4", name="Arroz", sale_price=Decimal("60000"), unit_cost=Decimal("25000"), operational_category_id=categories[4].id),
        }
        db.add_all(products.values()); db.flush()
        for product in products.values():
            db.add(InventoryMovement(product_id=product.id, movement_type=InventoryMovementType.PURCHASE,
                quantity=Decimal("100"), unit_cost=product.unit_cost, user_id=user.id))
        # Opened a few seconds in the past: SQLite's server-side now() has only
        # second precision, which can otherwise tie/precede a same-second opened_at.
        db.add(CashSession(opening_amount=Decimal("500000"), opened_by=user.id,
                           opened_at=datetime.now(timezone.utc) - timedelta(seconds=5)))
        db.commit()

        def override_db(): yield db
        app.dependency_overrides[get_db] = override_db
        try:
            yield db, TestClient(app), products, {row.name: row for row in channels}
        finally:
            app.dependency_overrides.clear()


def sell(db, client, product, channel, received=None):
    sale = add_item(db, create_sale(db).id, product.barcode, Decimal("1"))
    body = {"channel_id": str(channel.id)}
    if channel.affects_cash: body["received_amount"] = received or str(sale.total)
    response = client.post(f"/api/v1/sales/{sale.id}/checkout", json=body)
    assert response.status_code == 200
    return response.json()


@pytest.mark.parametrize("product_key,channel_name,category,cash,electronic", [
    ("cigarros", "Efectivo", "CIGARROS", "40000", "0"),
    ("cigarros", "Nequi", "CIGARROS", "0", "40000"),
    ("mascotas", "Daviplata", "MASCOTAS_CON_CODIGO", "0", "30000"),
    ("licores", "Efectivo", "LICORES", "60000", "0"),
    ("licores", "Nequi", "LICORES", "0", "60000"),
])
def test_normal_sales_classified_by_category_and_group(operations_context, product_key, channel_name, category, cash, electronic):
    db, client, products, channels = operations_context
    sell(db, client, products[product_key], channels[channel_name])
    rows = {row["category_code"]: row for row in client.get("/api/v1/daily-operations/summary").json()["categories"]}
    assert Decimal(rows[category]["cash"]) == Decimal(cash)
    assert Decimal(rows[category]["electronic"]) == Decimal(electronic)


@pytest.mark.parametrize("channel_name", ["Nequi", "Daviplata"])
def test_manual_bulk_digital_allowed_without_inventory(operations_context, channel_name):
    db, client, _, channels = operations_context
    before = db.scalar(select(func.count(InventoryMovement.id)))
    response = client.post("/api/v1/sales/manual", json={
        "source":"MANUAL_BULK", "description":"Comida a granel", "amount":"25000",
        "channel_id":str(channels[channel_name].id), "idempotency_key":f"bulk-{channel_name}-123"})
    assert response.status_code == 201
    assert response.json()["source"] == "MANUAL_BULK"
    assert db.scalar(select(func.count(InventoryMovement.id))) == before
    assert db.scalar(select(func.count(CashMovement.id))) == 0


def test_manual_bulk_cash_rejected(operations_context):
    _, client, _, channels = operations_context
    response = client.post("/api/v1/sales/manual", json={
        "source":"MANUAL_BULK", "description":"Granel", "amount":"25000",
        "channel_id":str(channels["Efectivo"].id), "idempotency_key":"bulk-cash-123"})
    assert response.status_code == 409
    assert "báscula" in response.json()["detail"]


@pytest.mark.parametrize("channel_name", ["Efectivo", "Nequi", "Datáfono"])
def test_manual_sack_all_channels_and_cash_effect(operations_context, channel_name):
    db, client, _, channels = operations_context
    response = client.post("/api/v1/sales/manual", json={
        "source":"MANUAL_SACK", "description":"Bulto 30 kg", "amount":"145000",
        "channel_id":str(channels[channel_name].id), "idempotency_key":f"sack-{channel_name}-123"})
    assert response.status_code == 201
    cash = db.scalars(select(CashMovement).where(
        CashMovement.reference_id == UUID(response.json()["id"]))).all()
    assert len(cash) == (1 if channel_name == "Efectivo" else 0)


def test_bultos_are_split_in_summary_without_changing_total_or_manual_marker(operations_context):
    db, client, _, channels = operations_context
    # Historical manual sack: no manual_kind, so the immutable Sale.source is the marker.
    sack = client.post("/api/v1/sales/manual", json={
        "source": "MANUAL_SACK", "description": "Bulto histórico", "amount": "145000",
        "channel_id": str(channels["Efectivo"].id), "idempotency_key": "summary-sack-123"})
    assert sack.status_code == 201
    # Current POS manual line: the persisted manual_kind is the marker.
    sale = create_sale(db)
    added = client.post(f"/api/v1/sales/{sale.id}/manual-items", json={
        "kind": "BULTO", "description": "Bulto POS", "amount": "55000"})
    assert added.status_code == 200
    assert added.json()["items"][0]["manual_kind"] == "BULTO"
    paid = client.post(f"/api/v1/sales/{sale.id}/checkout", json={"channel_id": str(channels["Nequi"].id)})
    assert paid.status_code == 200
    # A non-bulto line stored in the same legacy category stays there.
    bulk = client.post("/api/v1/sales/manual", json={
        "source": "MANUAL_BULK", "description": "Granel", "amount": "25000",
        "channel_id": str(channels["Nequi"].id), "idempotency_key": "summary-bulk-123"})
    assert bulk.status_code == 201

    summary = client.get("/api/v1/daily-operations/summary").json()
    rows = {row["category_code"]: row for row in summary["categories"]}
    assert rows["BULTOS"]["category_name"] == "Bultos"
    assert Decimal(rows["BULTOS"]["cash"]) == Decimal("145000")
    assert Decimal(rows["BULTOS"]["electronic"]) == Decimal("55000")
    assert Decimal(rows["BULTOS"]["total"]) == Decimal("200000")
    assert Decimal(rows["MASCOTAS_SIN_CODIGO"]["electronic"]) == Decimal("25000")
    assert sum(Decimal(row["total"]) for row in summary["categories"]) == Decimal(summary["sales_total"])


def test_manual_inactive_channel_and_idempotency(operations_context):
    db, client, _, channels = operations_context
    channel = channels["Nequi"]
    body = {"source":"MANUAL_SACK", "description":"Bulto", "amount":"10000",
            "channel_id":str(channel.id), "idempotency_key":"same-manual-123"}
    assert client.post("/api/v1/sales/manual", json=body).status_code == 201
    assert client.post("/api/v1/sales/manual", json=body).status_code == 201
    assert db.scalar(select(func.count(Sale.id)).where(Sale.idempotency_key == "same-manual-123")) == 1
    channel.active = False; db.commit()
    body["idempotency_key"] = "inactive-123"
    assert client.post("/api/v1/sales/manual", json=body).status_code == 409


def test_cash_expense_affects_cash_and_digital_does_not(operations_context):
    db, client, _, channels = operations_context
    cash = client.post("/api/v1/expenses", json={"amount":"80000", "category":"Compra externa",
        "description":"Bolsas", "payment_source":"CASH", "payment_channel_id":str(channels["Efectivo"].id),
        "idempotency_key":"expense-cash-123"})
    assert cash.status_code == 201
    digital = client.post("/api/v1/expenses", json={"amount":"50000", "category":"Transporte",
        "description":"Domicilio", "payment_source":"ELECTRONIC", "payment_channel_id":str(channels["Nequi"].id),
        "idempotency_key":"expense-digital-123"})
    assert digital.status_code == 201
    movements = db.scalars(select(CashMovement).where(CashMovement.movement_type == CashMovementType.EXPENSE)).all()
    assert len(movements) == 1 and movements[0].amount == Decimal("-80000")
    summary = client.get("/api/v1/daily-operations/summary").json()["outgoing"]
    assert Decimal(summary["expenses_cash"]) == Decimal("80000")
    assert Decimal(summary["expenses_electronic"]) == Decimal("50000")


def test_internal_consumption_reduces_stock_and_does_not_create_sale(operations_context):
    db, client, products, _ = operations_context
    product = products["super"]; sales_before = db.scalar(select(func.count(Sale.id)))
    response = client.post("/api/v1/internal-consumption", json={
        "product_id":str(product.id), "quantity":"2", "description":"Arroz para casa",
        "notes":"Consumo familiar", "idempotency_key":"home-product-123"})
    assert response.status_code == 201
    assert Decimal(response.json()["cost_total"]) == Decimal("50000")
    assert current_stock(db, product.id) == Decimal("98")
    movement = db.scalar(select(InventoryMovement).where(
        InventoryMovement.reference_id == UUID(response.json()["id"])))
    assert movement.movement_type == InventoryMovementType.INTERNAL_CONSUMPTION
    assert db.scalar(select(func.count(Sale.id))) == sales_before


def test_manual_internal_consumption_has_no_fake_inventory(operations_context):
    db, client, _, _ = operations_context
    before = db.scalar(select(func.count(InventoryMovement.id)))
    response = client.post("/api/v1/internal-consumption", json={
        "description":"Producto sin código para casa", "estimated_value":"12000",
        "idempotency_key":"home-manual-123"})
    assert response.status_code == 201 and response.json()["product_id"] is None
    assert db.scalar(select(func.count(InventoryMovement.id))) == before


def test_internal_consumption_cart_is_atomic_idempotent_and_not_a_sale(operations_context):
    db, client, products, _ = operations_context
    sales_before = db.scalar(select(func.count(Sale.id)))
    payments_before = db.scalar(select(func.count(Payment.id)))
    cash_before = db.scalar(select(func.count(CashMovement.id)))
    body = {"items": [{"product_id": str(products["super"].id), "quantity": "2"},
                      {"product_id": str(products["cigarros"].id), "quantity": "3"}],
            "notes": "Mercado de la casa", "idempotency_key": "home-cart-multi-123"}
    first = client.post("/api/v1/internal-consumption/batch", json=body)
    assert first.status_code == 201
    again = client.post("/api/v1/internal-consumption/batch", json=body)
    assert again.status_code == 201 and again.json()["id"] == first.json()["id"]
    assert len(first.json()["items"]) == 2
    assert Decimal(first.json()["cost_total"]) == Decimal("110000")
    assert current_stock(db, products["super"].id) == Decimal("98")
    assert current_stock(db, products["cigarros"].id) == Decimal("97")
    consumption_id = UUID(first.json()["id"])
    assert db.scalar(select(func.count(InternalConsumptionItem.id)).where(
        InternalConsumptionItem.internal_consumption_id == consumption_id)) == 2
    assert db.scalar(select(func.count(InventoryMovement.id)).where(
        InventoryMovement.reference_id == consumption_id)) == 2
    listed = client.get("/api/v1/internal-consumption").json()
    assert listed[0]["id"] == first.json()["id"] and len(listed[0]["items"]) == 2
    assert db.scalar(select(func.count(Sale.id))) == sales_before
    assert db.scalar(select(func.count(Payment.id))) == payments_before
    assert db.scalar(select(func.count(CashMovement.id))) == cash_before
    summary = client.get("/api/v1/daily-operations/summary").json()
    assert Decimal(summary["outgoing"]["internal_consumption_cost"]) == Decimal("110000")
    assert Decimal(summary["sales_total"]) == Decimal("0")


def test_internal_consumption_cart_insufficient_stock_rolls_back_every_line(operations_context):
    db, client, products, _ = operations_context
    before_consumptions = db.scalar(select(func.count(InternalConsumption.id)))
    before_movements = db.scalar(select(func.count(InventoryMovement.id)))
    response = client.post("/api/v1/internal-consumption/batch", json={
        "items": [{"product_id": str(products["super"].id), "quantity": "1"},
                  {"product_id": str(products["cigarros"].id), "quantity": "101"}],
        "idempotency_key": "home-cart-insufficient-123"})
    assert response.status_code == 409
    assert current_stock(db, products["super"].id) == Decimal("100")
    assert current_stock(db, products["cigarros"].id) == Decimal("100")
    assert db.scalar(select(func.count(InternalConsumption.id))) == before_consumptions
    assert db.scalar(select(func.count(InventoryMovement.id))) == before_movements


def test_home_withdrawal_appears_in_casa_and_other_does_not(operations_context):
    _, client, _, _ = operations_context
    home = client.post("/api/v1/cash/withdrawal", json={
        "amount": "20000", "reason": "Mercado de la casa", "withdrawal_reason": "HOME"})
    assert home.status_code == 201
    other = client.post("/api/v1/cash/withdrawal", json={
        "amount": "15000", "reason": "Pago mensajero", "withdrawal_reason": "OTHER"})
    assert other.status_code == 201
    outgoing = client.get("/api/v1/daily-operations/summary").json()["outgoing"]
    assert Decimal(outgoing["home_withdrawals"]) == Decimal("20000")
    assert Decimal(outgoing["other_withdrawals"]) == Decimal("15000")


def test_withdrawal_reason_is_required(operations_context):
    _, client, _, _ = operations_context
    response = client.post("/api/v1/cash/withdrawal", json={"amount": "5000", "reason": "Sin motivo"})
    assert response.status_code == 422


def test_withdrawal_reason_does_not_affect_cash_ledger_totals(operations_context):
    _, client, _, _ = operations_context
    client.post("/api/v1/cash/withdrawal", json={"amount": "20000", "reason": "Casa", "withdrawal_reason": "HOME"})
    client.post("/api/v1/cash/withdrawal", json={"amount": "15000", "reason": "Otro", "withdrawal_reason": "OTHER"})
    current = client.get("/api/v1/cash/current").json()
    assert Decimal(current["withdrawals"]) == Decimal("35000")
    assert Decimal(current["expected_cash"]) == Decimal("500000") - Decimal("35000")
    types = [row["type"] for row in current["financial_movements"]]
    assert types.count("WITHDRAWAL") == 2


def test_mixed_payment_prorates_multiple_categories_and_keeps_channels(operations_context):
    db, client, products, channels = operations_context
    sale = create_sale(db)
    sale = add_item(db, sale.id, products["cigarros"].barcode, Decimal("1"))
    sale = add_item(db, sale.id, products["super"].barcode, Decimal("1"))
    response = client.post(f"/api/v1/sales/{sale.id}/checkout", json={"payments":[
        {"channel_id":str(channels["Efectivo"].id), "amount":"50000", "received_amount":"50000"},
        {"channel_id":str(channels["Nequi"].id), "amount":"50000"}]})
    assert response.status_code == 200
    rows = {row["category_code"]:row for row in client.get("/api/v1/daily-operations/summary").json()["categories"]}
    assert Decimal(rows["CIGARROS"]["cash"]) == Decimal("20000")
    assert Decimal(rows["CIGARROS"]["electronic"]) == Decimal("20000")
    assert Decimal(rows["SUPER"]["cash"]) == Decimal("30000")
    assert Decimal(rows["SUPER"]["electronic"]) == Decimal("30000")
    assert {row["channel_name"] for row in rows["SUPER"]["channels"]} == {"Efectivo", "Nequi"}
