from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_db
from app.domain.enums import InventoryMovementType, PaymentGroup, PaymentMethod, SaleStatus
from app.main import app
from app.models import (CashMovement, CashSession, InventoryMovement, Payment, PaymentChannel,
                        Product, Sale, SaleItem, User)
from app.services.inventory import current_stock
from app.services.pos import add_item, create_sale

API = "/api/v1"


@pytest.fixture()
def ctx():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(name="Cajero")
        cash = PaymentChannel(name="Efectivo", payment_type=PaymentMethod.CASH, payment_group=PaymentGroup.CASH,
                              affects_cash=True, display_order=10)
        db.add_all([user, cash]); db.flush()
        product = Product(barcode="cx-1", name="Arroz", sale_price=Decimal("5000"), unit_cost=Decimal("3000"))
        db.add(product); db.flush()
        db.add(InventoryMovement(product_id=product.id, movement_type=InventoryMovementType.PURCHASE,
                                 quantity=Decimal("50"), unit_cost=Decimal("3000"), user_id=user.id))
        db.add(CashSession(opening_amount=Decimal("100000"), opened_by=user.id,
                           opened_at=datetime.now(timezone.utc) - timedelta(seconds=5)))
        db.commit()
        def override_db(): yield db
        app.dependency_overrides[get_db] = override_db
        try:
            yield db, TestClient(app), product, cash, user
        finally:
            app.dependency_overrides.clear()


def snapshot(db, product):
    """Everything a cancellation must leave untouched."""
    money = lambda column: db.scalar(select(func.coalesce(func.sum(column), 0)))
    return {
        "stock": current_stock(db, product.id),
        "movements": db.scalar(select(func.count()).select_from(InventoryMovement)),
        "movement_qty": money(InventoryMovement.quantity),
        "cash_movements": db.scalar(select(func.count()).select_from(CashMovement)),
        "cash_amount": money(CashMovement.amount),
        "payments": db.scalar(select(func.count()).select_from(Payment)),
        "completed": db.scalar(select(func.count()).select_from(Sale).where(Sale.status == SaleStatus.COMPLETED)),
    }


def test_cancel_empty_open_sale(ctx):
    db, client, product, *_ = ctx
    sale = create_sale(db)
    before = snapshot(db, product)
    response = client.post(f"{API}/sales/{sale.id}/cancel")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "CANCELLED" and body["cancelled_at"] is not None
    assert snapshot(db, product) == before
    assert client.get(f"{API}/sales/open").json() == []


def test_cancel_open_sale_with_lines_keeps_lines_and_touches_nothing_else(ctx):
    db, client, product, *_ = ctx
    sale = add_item(db, create_sale(db).id, product.barcode, Decimal("3"))
    assert sale.total == Decimal("15000")
    before = snapshot(db, product)
    response = client.post(f"{API}/sales/{sale.id}/cancel")
    assert response.status_code == 200 and response.json()["status"] == "CANCELLED"
    after = snapshot(db, product)
    assert after == before                       # stock, InventoryMovement, CashMovement and Payment all identical
    assert after["stock"] == Decimal("50")
    db.expire_all()
    stored = db.get(Sale, sale.id)
    assert stored.status == SaleStatus.CANCELLED and stored.cancelled_by is not None   # audit trail
    assert db.scalar(select(func.count()).select_from(SaleItem).where(SaleItem.sale_id == sale.id)) == 1   # not deleted
    assert [s["id"] for s in client.get(f"{API}/sales/open").json()] == []            # its tab disappears


def test_cancelling_twice_is_safe_and_keeps_the_first_audit_data(ctx):
    db, client, product, *_ = ctx
    sale = add_item(db, create_sale(db).id, product.barcode, Decimal("1"))
    first = client.post(f"{API}/sales/{sale.id}/cancel").json()
    before = snapshot(db, product)
    second = client.post(f"{API}/sales/{sale.id}/cancel")
    assert second.status_code == 200 and second.json()["status"] == "CANCELLED"
    assert second.json()["cancelled_at"] == first["cancelled_at"]
    assert snapshot(db, product) == before


def test_completed_sale_cannot_be_cancelled(ctx):
    db, client, product, cash, _ = ctx
    sale = add_item(db, create_sale(db).id, product.barcode, Decimal("2"))
    done = client.post(f"{API}/sales/{sale.id}/checkout", json={"channel_id": str(cash.id), "received_amount": "10000"})
    assert done.status_code == 200
    before = snapshot(db, product)
    response = client.post(f"{API}/sales/{sale.id}/cancel")
    assert response.status_code == 409 and "cobrada" in response.json()["detail"]
    assert snapshot(db, product) == before
    db.expire_all()
    assert db.get(Sale, sale.id).status == SaleStatus.COMPLETED and db.get(Sale, sale.id).cancelled_at is None


def test_unknown_sale_is_404(ctx):
    _, client, *_ = ctx
    assert client.post(f"{API}/sales/00000000-0000-0000-0000-000000000000/cancel").status_code == 404


def test_cancelled_sale_is_closed_for_every_other_operation(ctx):
    db, client, product, cash, _ = ctx
    sale = add_item(db, create_sale(db).id, product.barcode, Decimal("1"))
    item_id = sale.items[0].id
    client.post(f"{API}/sales/{sale.id}/cancel")
    assert client.post(f"{API}/sales/{sale.id}/items", json={"barcode": product.barcode, "quantity": "1"}).status_code == 404
    assert client.patch(f"{API}/sales/{sale.id}/items/{item_id}", json={"quantity": "2"}).status_code == 404
    assert client.delete(f"{API}/sales/{sale.id}/items/{item_id}").status_code == 404
    before = snapshot(db, product)
    charge = client.post(f"{API}/sales/{sale.id}/checkout", json={"channel_id": str(cash.id), "received_amount": "9999"})
    assert charge.status_code == 404
    assert snapshot(db, product) == before


def test_cancel_works_with_the_jornada_closed(ctx):
    db, client, product, _, user = ctx
    sale = add_item(db, create_sale(db).id, product.barcode, Decimal("1"))
    session = db.scalar(select(CashSession)); session.closed_at = datetime.now(timezone.utc); session.closed_by = user.id; db.commit()
    before = snapshot(db, product)
    assert client.post(f"{API}/sales/{sale.id}/cancel").status_code == 200
    assert snapshot(db, product) == before


def test_cancelled_sales_do_not_count_in_reports_and_others_are_unaffected(ctx):
    db, client, product, cash, _ = ctx
    kept = add_item(db, create_sale(db).id, product.barcode, Decimal("1"))
    dropped = add_item(db, create_sale(db).id, product.barcode, Decimal("4"))
    client.post(f"{API}/sales/{dropped.id}/cancel")
    assert [s["id"] for s in client.get(f"{API}/sales/open").json()] == [str(kept.id)]
    assert client.post(f"{API}/sales/{kept.id}/checkout", json={"channel_id": str(cash.id), "received_amount": "5000"}).status_code == 200
    assert current_stock(db, product.id) == Decimal("49")              # only the charged unit left the ledger
    summary = client.get(f"{API}/cash/current").json()
    assert Decimal(summary["cash_sales"]) == Decimal("5000")           # the cancelled 20.000 never reached the cash box
