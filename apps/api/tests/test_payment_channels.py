from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_db
from app.domain.enums import InventoryMovementType, PaymentMethod, SaleStatus
from app.main import app
from app.models import (CashSession, InventoryMovement, Payment, PaymentChannel,
                        Product, Sale, User)
from app.services.inventory import current_stock
from app.services.pos import add_item, create_sale


@pytest.fixture()
def payments_context():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(name="Cajero")
        product = Product(barcode="7701", name="Producto", sale_price=Decimal("100000"), unit_cost=Decimal("50000"))
        channels = [
            PaymentChannel(name="Efectivo", payment_type=PaymentMethod.CASH, affects_cash=True, display_order=10),
            PaymentChannel(name="Nequi", payment_type=PaymentMethod.TRANSFER, affects_cash=False, display_order=20),
            PaymentChannel(name="Daviplata", payment_type=PaymentMethod.TRANSFER, affects_cash=False, display_order=30),
            PaymentChannel(name="Bre-B / Llave", payment_type=PaymentMethod.TRANSFER, affects_cash=False, display_order=40),
            PaymentChannel(name="Datáfono", payment_type=PaymentMethod.CARD, affects_cash=False, display_order=50),
        ]
        db.add_all([user, product, *channels]); db.flush()
        db.add(InventoryMovement(product_id=product.id, movement_type=InventoryMovementType.PURCHASE,
               quantity=Decimal("100"), unit_cost=product.unit_cost, user_id=user.id))
        db.add(CashSession(opening_amount=Decimal("0"), opened_by=user.id)); db.commit()

        def override_db(): yield db
        app.dependency_overrides[get_db] = override_db
        try:
            yield db, TestClient(app), product, {channel.name: channel for channel in channels}
        finally:
            app.dependency_overrides.clear()


def open_sale(db, product):
    sale = create_sale(db)
    return add_item(db, sale.id, product.barcode, Decimal("1"))


@pytest.mark.parametrize("channel_name", ["Efectivo", "Nequi", "Daviplata", "Bre-B / Llave", "Datáfono"])
def test_full_payment_by_each_seed_channel(payments_context, channel_name):
    db, client, product, channels = payments_context
    sale = open_sale(db, product)
    channel = channels[channel_name]
    body = {"channel_id": str(channel.id)}
    if channel.affects_cash: body["received_amount"] = "100000"
    response = client.post(f"/api/v1/sales/{sale.id}/checkout", json=body)
    assert response.status_code == 200
    assert response.json()["payments"][0]["channel_name"] == channel_name
    assert Decimal(response.json()["payments"][0]["amount"]) == Decimal("100000")


@pytest.mark.parametrize("names,amounts", [
    (("Efectivo", "Nequi"), ("40000", "60000")),
    (("Efectivo", "Nequi", "Datáfono"), ("40000", "35000", "25000")),
])
def test_mixed_payments(payments_context, names, amounts):
    db, client, product, channels = payments_context
    sale = open_sale(db, product)
    allocations = [{"channel_id": str(channels[name].id), "amount": amount,
                    **({"received_amount": amount} if channels[name].affects_cash else {})}
                   for name, amount in zip(names, amounts)]
    response = client.post(f"/api/v1/sales/{sale.id}/checkout", json={"payments": allocations})
    assert response.status_code == 200
    assert [payment["channel_name"] for payment in response.json()["payments"]] == list(names)


@pytest.mark.parametrize("amount,status", [("99999", 409), ("100001", 409)])
def test_payment_sum_must_equal_sale_total(payments_context, amount, status):
    db, client, product, channels = payments_context
    sale = open_sale(db, product)
    response = client.post(f"/api/v1/sales/{sale.id}/checkout", json={"payments": [
        {"channel_id": str(channels["Nequi"].id), "amount": amount}]})
    assert response.status_code == status
    db.refresh(sale)
    assert sale.status == SaleStatus.OPEN
    assert not db.scalars(select(Payment).where(Payment.sale_id == sale.id)).all()


def test_cash_change_does_not_increase_payment_amount(payments_context):
    db, client, product, channels = payments_context
    sale = open_sale(db, product)
    response = client.post(f"/api/v1/sales/{sale.id}/checkout", json={
        "channel_id": str(channels["Efectivo"].id), "received_amount": "150000"})
    payment = response.json()["payments"][0]
    assert Decimal(payment["amount"]) == Decimal("100000")
    assert Decimal(payment["received_amount"]) == Decimal("150000")
    assert Decimal(payment["change_amount"]) == Decimal("50000")


def test_inactive_channel_is_rejected_but_historical_payment_remains(payments_context):
    db, client, product, channels = payments_context
    nequi = channels["Nequi"]
    first = open_sale(db, product)
    assert client.post(f"/api/v1/sales/{first.id}/checkout", json={"channel_id": str(nequi.id)}).status_code == 200
    assert client.patch(f"/api/v1/payment-channels/{nequi.id}/active", json={"active": False}).status_code == 200
    second = open_sale(db, product)
    assert client.post(f"/api/v1/sales/{second.id}/checkout", json={"channel_id": str(nequi.id)}).status_code == 409
    historical = db.scalar(select(Payment).where(Payment.sale_id == first.id))
    assert historical.channel.name == "Nequi"


def test_double_checkout_does_not_duplicate_payment_or_inventory(payments_context):
    db, client, product, channels = payments_context
    sale = open_sale(db, product)
    body = {"channel_id": str(channels["Nequi"].id)}
    assert client.post(f"/api/v1/sales/{sale.id}/checkout", json=body).status_code == 200
    assert client.post(f"/api/v1/sales/{sale.id}/checkout", json=body).status_code == 404
    assert len(db.scalars(select(Payment).where(Payment.sale_id == sale.id)).all()) == 1
    movements = db.scalars(select(InventoryMovement).where(
        InventoryMovement.reference_id == sale.id,
        InventoryMovement.movement_type == InventoryMovementType.SALE)).all()
    assert len(movements) == 1
    assert current_stock(db, product.id) == Decimal("99")


def test_summary_groups_amount_and_count_by_channel(payments_context):
    db, client, product, channels = payments_context
    sale = open_sale(db, product)
    response = client.post(f"/api/v1/sales/{sale.id}/checkout", json={"payments": [
        {"channel_id": str(channels["Nequi"].id), "amount": "60000"},
        {"channel_id": str(channels["Datáfono"].id), "amount": "40000"}]})
    assert response.status_code == 200
    summary = client.get("/api/v1/payments/summary").json()
    assert Decimal(summary["total_sales"]) == Decimal("100000")
    rows = {row["channel_name"]: row for row in summary["channels"]}
    assert Decimal(rows["Nequi"]["amount"]) == Decimal("60000")
    assert rows["Nequi"]["payment_count"] == 1
    assert Decimal(rows["Datáfono"]["percentage"]) == Decimal("40")
