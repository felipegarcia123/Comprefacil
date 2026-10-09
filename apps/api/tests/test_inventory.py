from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_db
from app.domain.enums import InventoryMovementType
from app.main import app
from app.models import CashSession, InventoryMovement, Product, Supplier, User
from app.services.inventory import create_adjustment, current_stock


@pytest.fixture()
def inventory_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(name="Tester")
        supplier = Supplier(name="Proveedor")
        db.add_all([user, supplier]); db.flush(); db.add(CashSession(opening_amount=Decimal("0"), opened_by=user.id))
        product = Product(barcode="7702004001234", name="Café Colombiano",
                          sale_price=Decimal("18500"), unit_cost=Decimal("13000"),
                          supplier_id=supplier.id)
        db.add(product); db.flush()
        db.add(InventoryMovement(product_id=product.id,
                                 movement_type=InventoryMovementType.PURCHASE,
                                 quantity=Decimal("10"), unit_cost=product.unit_cost,
                                 reference_id=None, user_id=user.id))
        db.commit()
        yield db, product


def add_movement(db, product, kind, quantity, created_at=None):
    movement = InventoryMovement(product_id=product.id, movement_type=kind,
                                 quantity=Decimal(quantity), unit_cost=product.unit_cost,
                                 reference_id=None, user_id=db.query(User).first().id)
    if created_at:
        movement.created_at = created_at
    db.add(movement); db.commit()
    return movement


def test_stock_is_calculated_from_movements(inventory_db):
    db, product = inventory_db
    add_movement(db, product, InventoryMovementType.SALE, "-2")
    add_movement(db, product, InventoryMovementType.RETURN, "1")
    assert current_stock(db, product.id) == Decimal("9.000")


def test_purchase_increases_stock(inventory_db):
    db, product = inventory_db
    before = current_stock(db, product.id)
    add_movement(db, product, InventoryMovementType.PURCHASE, "4")
    assert current_stock(db, product.id) == before + Decimal("4")


def test_sale_decreases_stock(inventory_db):
    db, product = inventory_db
    before = current_stock(db, product.id)
    add_movement(db, product, InventoryMovementType.SALE, "-1")
    assert current_stock(db, product.id) == before - Decimal("1")


@pytest.mark.parametrize(("delta", "expected"), [("3", "13"), ("-2", "8")])
def test_positive_and_negative_adjustments(inventory_db, delta, expected):
    db, product = inventory_db
    movement, _, balance = create_adjustment(db, product.id, Decimal(delta), "Conteo físico")
    assert movement.movement_type == InventoryMovementType.ADJUSTMENT
    assert movement.quantity == Decimal(delta)
    assert movement.reason == "Conteo físico"
    assert balance == Decimal(expected)
    assert current_stock(db, product.id) == Decimal(expected)


def test_adjustment_cannot_leave_negative_stock(inventory_db):
    db, product = inventory_db
    with pytest.raises(Exception) as error:
        create_adjustment(db, product.id, Decimal("-11"), "Merma encontrada")
    assert getattr(error.value, "status_code", None) == 409
    assert current_stock(db, product.id) == Decimal("10")


def test_movement_filters_and_product_stock_endpoint(inventory_db):
    db, product = inventory_db
    old = datetime.now(timezone.utc) - timedelta(days=10)
    add_movement(db, product, InventoryMovementType.SALE, "-2", old)
    recent = add_movement(db, product, InventoryMovementType.ADJUSTMENT, "1")

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        product_response = client.get(f"/api/v1/inventory/{product.id}")
        assert product_response.status_code == 200
        assert Decimal(product_response.json()["current_stock"]) == Decimal("9")
        assert product_response.json()["supplier"] == "Proveedor"

        response = client.get("/api/v1/inventory/movements", params={
            "product_id": str(product.id),
            "movement_type": "ADJUSTMENT",
            "date_from": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            "date_to": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        })
        assert response.status_code == 200
        assert [row["id"] for row in response.json()] == [str(recent.id)]
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def paged_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(name="Tester")
        db.add(user); db.flush()
        db.add(CashSession(opening_amount=Decimal("0"), opened_by=user.id))
        products = []
        for i in range(23):
            product = Product(barcode=f"9000000{i:04d}", name=f"Producto {i:02d}",
                              sale_price=Decimal("1000") + i, unit_cost=Decimal("600"),
                              minimum_stock=5, active=i != 22)
            db.add(product); db.flush()
            if i < 20:  # products[20] and [21] stay at zero stock (out of stock / low stock cases below)
                db.add(InventoryMovement(product_id=product.id, movement_type=InventoryMovementType.PURCHASE,
                                         quantity=Decimal(3 if i % 5 == 0 else 40), unit_cost=Decimal("600"), user_id=user.id))
            products.append(product)
        db.commit()

        def override_db(): yield db
        app.dependency_overrides[get_db] = override_db
        try:
            yield db, TestClient(app), products
        finally:
            app.dependency_overrides.clear()


def test_inventory_list_is_paginated_not_a_full_dump(paged_db):
    _, client, products = paged_db
    response = client.get("/api/v1/inventory", params={"page_size": 10})
    assert response.status_code == 200
    body = response.json()
    total = len(products)  # default `active` filter is None → no filter applied, active and inactive both count
    assert body["total"] == total
    assert len(body["items"]) == 10
    assert body["page"] == 1 and body["page_size"] == 10
    assert body["total_pages"] == -(-total // 10)


def test_inventory_pages_do_not_overlap_and_cover_everything(paged_db):
    _, client, products = paged_db
    page_size = 7
    seen = []
    total_pages = client.get("/api/v1/inventory", params={"page_size": page_size}).json()["total_pages"]
    for page in range(1, total_pages + 1):
        body = client.get("/api/v1/inventory", params={"page": page, "page_size": page_size}).json()
        seen.extend(item["id"] for item in body["items"])
    assert len(seen) == len(set(seen)) == body["total"]


def test_inventory_active_filter_and_out_of_stock_and_low_stock(paged_db):
    db, client, products = paged_db
    active_count = sum(1 for p in products if p.active)
    assert client.get("/api/v1/inventory", params={"active": True, "page_size": 1}).json()["total"] == active_count
    assert client.get("/api/v1/inventory", params={"active": False, "page_size": 1}).json()["total"] == 1

    out = client.get("/api/v1/inventory", params={"out_of_stock": True, "page_size": 50}).json()
    assert {item["name"] for item in out["items"]} == {"Producto 20", "Producto 21", "Producto 22"}  # zero stock (22 is also inactive, but no active filter applied here)

    low = client.get("/api/v1/inventory", params={"low_stock": True, "page_size": 50}).json()
    assert {item["name"] for item in low["items"]} == {f"Producto {i:02d}" for i in (0, 5, 10, 15)}  # stock=3 <= minimum_stock=5, >0


def test_inventory_search_matches_name_or_barcode(paged_db):
    _, client, products = paged_db
    by_name = client.get("/api/v1/inventory", params={"q": "Producto 07"}).json()
    assert by_name["total"] == 1 and by_name["items"][0]["barcode"] == "90000000007"
    by_code = client.get("/api/v1/inventory", params={"q": "90000000007"}).json()
    assert by_code["total"] == 1 and by_code["items"][0]["name"] == "Producto 07"
    assert client.get("/api/v1/inventory", params={"q": "no-existe-xyz"}).json()["total"] == 0


def test_inventory_sort_by_stock_and_price(paged_db):
    _, client, products = paged_db
    asc = client.get("/api/v1/inventory", params={"sort": "price", "sort_dir": "asc", "page_size": 3}).json()
    assert [Decimal(x["sale_price"]) for x in asc["items"]] == sorted(Decimal(x["sale_price"]) for x in asc["items"])
    desc = client.get("/api/v1/inventory", params={"sort": "price", "sort_dir": "desc", "page_size": 3}).json()
    assert Decimal(desc["items"][0]["sale_price"]) > Decimal(asc["items"][0]["sale_price"])
    by_stock = client.get("/api/v1/inventory", params={"sort": "stock", "sort_dir": "desc", "page_size": 50}).json()
    stocks = [Decimal(x["current_stock"]) for x in by_stock["items"]]
    assert stocks == sorted(stocks, reverse=True)


def test_inventory_page_size_is_capped(paged_db):
    _, client, products = paged_db
    assert client.get("/api/v1/inventory", params={"page_size": 500}).status_code == 422
    assert client.get("/api/v1/inventory", params={"page": 0}).status_code == 422
