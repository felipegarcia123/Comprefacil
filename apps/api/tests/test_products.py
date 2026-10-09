from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_db
from app.domain.enums import InventoryMovementType
from app.main import app
from app.models import CashSession, InventoryMovement, OperationalCategory, Product, Supplier, User
from app.schemas.products import ProductCreate
from app.services.inventory import current_stock
from app.services.products import create_product


@pytest.fixture()
def product_context():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(name="Administrador")
        category = OperationalCategory(code="CIGARROS", name="Cigarros", display_order=10)
        other_category = OperationalCategory(code="SUPER", name="Super", display_order=20)
        supplier = Supplier(name="Proveedor X", active=True)
        other_supplier = Supplier(name="Proveedor Y", active=True)
        db.add_all([user, category, other_category, supplier, other_supplier]); db.flush(); db.add(CashSession(opening_amount=Decimal("0"), opened_by=user.id)); db.commit()
        def override_db(): yield db
        app.dependency_overrides[get_db] = override_db
        try:
            yield db, TestClient(app), category, other_category, supplier, other_supplier
        finally:
            app.dependency_overrides.clear()


def body(category=None, supplier=None, **changes):
    data = {"barcode":"TEST001", "name":"Producto prueba", "sale_price":"12500",
            "purchase_cost":"8000", "initial_stock":0, "minimum_stock":5,
            "unit":"unidad", "active":True}
    if category: data["category_id"] = str(category.id)
    if supplier: data["supplier_id"] = str(supplier.id)
    data.update(changes)
    return data


def create(client, category=None, supplier=None, **changes):
    return client.post("/api/v1/products", json=body(category, supplier, **changes))


def update_payload(product, **changes):
    data = {"barcode":product.barcode, "name":product.name,
            "sale_price":str(product.sale_price), "purchase_cost":str(product.unit_cost),
            "category_id":str(product.operational_category_id) if product.operational_category_id else None,
            "supplier_id":str(product.supplier_id) if product.supplier_id else None,
            "minimum_stock":product.minimum_stock, "unit":product.unit,
            "description":product.description, "active":product.active}
    data.update(changes); return data


def test_create_product_without_stock(product_context):
    db, client, category, _, supplier, _ = product_context
    response = create(client, category, supplier)
    assert response.status_code == 201
    assert Decimal(response.json()["current_stock"]) == 0
    assert db.scalar(select(func.count(InventoryMovement.id))) == 0


def test_initial_stock_creates_ledger_movement_and_balance(product_context):
    db, client, category, _, supplier, _ = product_context
    response = create(client, category, supplier, barcode="TEST002", initial_stock=20)
    assert response.status_code == 201
    product_id = UUID(response.json()["id"])
    movement = db.scalar(select(InventoryMovement).where(
        InventoryMovement.product_id == product_id))
    assert movement.movement_type == InventoryMovementType.INITIAL_STOCK
    assert movement.quantity == Decimal("20")
    assert movement.reason == "Stock inicial al crear producto"
    assert Decimal(response.json()["current_stock"]) == 20


def test_creation_rolls_back_if_initial_movement_fails(product_context, monkeypatch):
    db, _, category, _, supplier, _ = product_context
    def fail(_db): raise HTTPException(500, "fallo simulado")
    monkeypatch.setattr("app.services.products.get_operator", fail)
    with pytest.raises(HTTPException):
        create_product(db, ProductCreate(**body(category, supplier, barcode="ROLLBACK", initial_stock=2)))
    assert db.scalar(select(Product).where(Product.barcode == "ROLLBACK")) is None


def test_duplicate_barcode_is_rejected(product_context):
    _, client, category, _, supplier, _ = product_context
    assert create(client, category, supplier).status_code == 201
    response = create(client, category, supplier, name="Duplicado")
    assert response.status_code == 409
    assert response.json()["detail"] == "Ya existe un producto con este código."


@pytest.mark.parametrize("changes", [
    {"name":"   "}, {"purchase_cost":"-1"}, {"sale_price":"0"},
    {"initial_stock":-1}, {"minimum_stock":-1}])
def test_invalid_product_values_rejected(product_context, changes):
    _, client, category, _, supplier, _ = product_context
    assert create(client, category, supplier, **changes).status_code == 422


def test_missing_supplier_and_category_rejected(product_context):
    _, client, _, _, _, _ = product_context
    assert create(client, supplier_id=str(uuid4())).status_code == 404
    assert create(client, category_id=str(uuid4())).status_code == 404


def test_edit_product_fields_without_changing_stock_or_history(product_context):
    db, client, category, other_category, supplier, other_supplier = product_context
    created = create(client, category, supplier, initial_stock=20).json()
    product = db.get(Product, UUID(created["id"]))
    movement = db.scalar(select(InventoryMovement).where(InventoryMovement.product_id == product.id))
    movement_snapshot = (movement.quantity, movement.unit_cost, movement.reason)
    response = client.patch(f"/api/v1/products/{product.id}", json=update_payload(product,
        barcode="ABC-001", name="Nombre editado", sale_price="16000", purchase_cost="9000",
        category_id=str(other_category.id), supplier_id=str(other_supplier.id),
        minimum_stock=7, unit="paquete", description="Descripción editada"))
    assert response.status_code == 200
    data = response.json()
    assert data["barcode"] == "ABC-001" and data["name"] == "Nombre editado"
    assert Decimal(data["sale_price"]) == 16000
    assert data["category"] == "Super" and data["supplier"] == "Proveedor Y"
    assert Decimal(data["current_stock"]) == 20
    db.refresh(movement)
    assert (movement.quantity, movement.unit_cost, movement.reason) == movement_snapshot


def test_edit_barcode_duplicate_rejected(product_context):
    db, client, category, _, supplier, _ = product_context
    first = create(client, category, supplier, barcode="ONE").json()
    create(client, category, supplier, barcode="TWO", name="Segundo")
    product = db.get(Product, UUID(first["id"]))
    assert client.patch(f"/api/v1/products/{product.id}", json=update_payload(
        product, barcode="TWO")).status_code == 409


def test_deactivated_product_hidden_from_pos_but_history_available(product_context):
    db, client, category, _, supplier, _ = product_context
    created = create(client, category, supplier, initial_stock=5).json()
    product_id = created["id"]
    response = client.patch(f"/api/v1/products/{product_id}/active", json={"active":False})
    assert response.status_code == 200 and response.json()["active"] is False
    assert client.get("/api/v1/products", params={"q":"TEST001"}).json() == []
    detail = client.get(f"/api/v1/inventory/{product_id}")
    assert detail.status_code == 200 and detail.json()["active"] is False
    movements = client.get("/api/v1/inventory/movements", params={"product_id":product_id}).json()
    assert len(movements) == 1


def test_alphanumeric_barcode_search_and_pos_price(product_context):
    _, client, category, _, supplier, _ = product_context
    create(client, category, supplier, barcode="ABC-001", sale_price="18500", initial_stock=3)
    response = client.get("/api/v1/products", params={"q":"ABC-001"})
    assert response.status_code == 200
    assert response.json()[0]["barcode"] == "ABC-001"
    assert Decimal(response.json()[0]["sale_price"]) == Decimal("18500")


def test_exact_barcode_lookup_is_case_insensitive_and_includes_inactive(product_context):
    _, client, category, _, supplier, _ = product_context
    response = create(client, category, supplier, barcode="Netum-001", active=False)
    assert response.status_code == 201
    found = client.get("/api/v1/products/by-barcode", params={"barcode":"netum-001"})
    assert found.status_code == 200
    assert found.json()["barcode"] == "Netum-001" and found.json()["active"] is False


def test_exact_barcode_lookup_returns_readable_not_found(product_context):
    _, client, *_ = product_context
    response = client.get("/api/v1/products/by-barcode", params={"barcode":"NO-EXISTE"})
    assert response.status_code == 404
    assert response.json()["detail"] == "No existe un producto con este código"


def test_inventory_filters_category_supplier_active_and_minimum_stock(product_context):
    _, client, category, _, supplier, _ = product_context
    created = create(client, category, supplier, initial_stock=3, minimum_stock=5).json()
    page = client.get("/api/v1/inventory", params={
        "category_id":str(category.id), "supplier_id":str(supplier.id),
        "active":True, "low_stock":True}).json()
    assert [row["id"] for row in page["items"]] == [created["id"]]
    assert page["total"] == 1


def test_blank_barcode_is_stored_as_null_not_rejected(product_context):
    """Approved 2026-09-28: products without EAN exist; "" / "   " mean "no code" (NULL), never a fake code."""
    db, client, category, _, supplier, _ = product_context
    first = create(client, category, supplier, barcode="   ", name="Huevo suelto")
    second = create(client, category, supplier, barcode="", name="Arepa suelta")
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["barcode"] is None and second.json()["barcode"] is None   # many NULLs, no unique clash
