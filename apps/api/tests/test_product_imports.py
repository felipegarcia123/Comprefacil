from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_db
from app.domain.enums import InventoryMovementType
from app.main import app
from app.models import CashSession, InventoryMovement, OperationalCategory, Product, Supplier, User


@pytest.fixture()
def context():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(name="Administrador"); db.add_all([user, OperationalCategory(code="CIGARROS", name="Cigarros"), Supplier(name="Proveedor X", active=True)]); db.flush(); db.add(CashSession(opening_amount=Decimal("0"), opened_by=user.id))
        db.commit()
        def override(): yield db
        app.dependency_overrides[get_db] = override
        try: yield db, TestClient(app)
        finally: app.dependency_overrides.clear()


def payload(content, mapping=None, delimiter="auto"):
    return {"filename":"productos.csv", "content":content, "delimiter":delimiter,
            "mapping":mapping or {"barcode":"barcode", "name":"name", "sale_price":"sale_price",
                                  "purchase_cost":"purchase_cost", "initial_stock":"initial_stock",
                                  "minimum_stock":"minimum_stock", "category":"category", "supplier":"supplier"}}


def preview(client, content, **kwargs):
    return client.post("/api/v1/products/import/preview", json=payload(content, **kwargs))


def confirm(client, content, **kwargs):
    return client.post("/api/v1/products/import/confirm", json=payload(content, **kwargs))


HEADER = "barcode,name,sale_price,purchase_cost,initial_stock,minimum_stock,category,supplier\n"


def test_valid_preview_does_not_persist_and_confirm_creates_ledger(context):
    db, client = context
    content = HEADER + "001234,Producto CSV,12500,8000,5,2,Cigarros,Proveedor X\n"
    result = preview(client, content)
    assert result.status_code == 200
    data = result.json(); assert data["ready_count"] == 1 and data["rows"][0]["parsed_data"]["barcode"] == "001234"
    assert db.scalar(select(func.count()).select_from(Product)) == 0
    imported = confirm(client, content)
    assert imported.status_code == 200 and imported.json()["imported_count"] == 1
    product = db.scalar(select(Product).where(Product.barcode == "001234"))
    movement = db.scalar(select(InventoryMovement).where(InventoryMovement.product_id == product.id))
    assert movement.movement_type == InventoryMovementType.INITIAL_STOCK and movement.quantity == Decimal("5")
    assert client.get("/api/v1/products?q=001234").json()[0]["sale_price"] == "12500.00"


def test_semicolon_bom_money_category_and_supplier_case_insensitive(context):
    _, client = context
    content = "\ufeffCódigo;Nombre;Venta;Costo;Stock;Mínimo;Categoría;Proveedor\nABC1;Uno;18.500;3.500,00;1.001;0; CIGARROS ; proveedor x \n"
    mapping = {"Código":"barcode","Nombre":"name","Venta":"sale_price","Costo":"purchase_cost",
               "Stock":"initial_stock","Mínimo":"minimum_stock","Categoría":"category","Proveedor":"supplier"}
    data = preview(client, content, mapping=mapping).json()
    row = data["rows"][0]
    assert data["delimiter"] == ";" and row["status"] == "READY"
    assert row["parsed_data"]["purchase_cost"] == "3500.00" and row["parsed_data"]["initial_stock"] == 1001
    assert row["parsed_data"]["category"] == "Cigarros" and row["parsed_data"]["supplier"] == "Proveedor X"


@pytest.mark.parametrize("row,error", [
    (",Sin código,1000,0,0,0,,", "Código obligatorio"),
    ("X1,,1000,0,0,0,,", "Nombre obligatorio"),
    ("X1,Uno,abc,0,0,0,,", "Precio de venta inválido"),
    ("X1,Uno,0,0,0,0,,", "Precio de venta debe ser mayor"),
    ("X1,Uno,1000,-1,0,0,,", "Costo de compra no puede"),
    ("X1,Uno,1000,0,-1,0,,", "Stock inicial no puede"),
    ("X1,Uno,1000,0,1.5,0,,", "Stock inicial debe ser entero"),
    ("X1,Uno,1000,0,0,-1,,", "Stock mínimo no puede"),
    ("7.702004001234E12,Uno,1000,0,0,0,,", "notación científica"),
])
def test_invalid_rows(context, row, error):
    _, client = context
    data = preview(client, HEADER + row + "\n").json()
    assert data["error_count"] == 1 and any(error in item for item in data["rows"][0]["errors"])


def test_existing_and_internal_duplicates(context):
    db, client = context
    db.add(Product(barcode="EXISTE", name="Viejo", sale_price=1, unit_cost=0)); db.commit()
    content = HEADER + "EXISTE,Uno,1000,0,0,0,,\nREP,Dos,1000,0,0,0,,\nrep,Tres,1000,0,0,0,,\n"
    data = preview(client, content).json()
    assert data["error_count"] == 3
    assert "ya existe" in data["rows"][0]["errors"][0]
    assert all("duplicado dentro" in row["errors"][0] for row in data["rows"][1:])


def test_missing_relations_are_warnings_and_import_without_relations(context):
    db, client = context
    content = HEADER + "WARN1,Uno,1000,0,0,0,Nueva,Desconocido\n"
    data = preview(client, content).json()
    assert data["warning_count"] == 1 and len(data["rows"][0]["warnings"]) == 2
    assert confirm(client, content).json()["imported_count"] == 1
    product = db.scalar(select(Product).where(Product.barcode == "WARN1"))
    assert product.operational_category_id is None and product.supplier_id is None


def test_zero_stock_has_no_movement_and_second_import_is_duplicate(context):
    db, client = context
    content = HEADER + "ZERO1,Uno,1000,0,0,0,,\n"
    assert confirm(client, content).json()["imported_count"] == 1
    product = db.scalar(select(Product).where(Product.barcode == "ZERO1"))
    assert db.scalar(select(func.count()).select_from(InventoryMovement).where(InventoryMovement.product_id == product.id)) == 0
    second = confirm(client, content).json()
    assert second["imported_count"] == 0 and second["skipped_count"] == 1


def test_invalid_rows_excluded_but_valid_imported(context):
    db, client = context
    content = HEADER + "GOOD,Correcto,$ 18.500,$ 3.500,2,0,,\nBAD,Incorrecto,abc,0,0,0,,\n"
    result = confirm(client, content).json()
    assert result["imported_count"] == 1 and result["skipped_count"] == 1
    assert db.scalar(select(Product).where(Product.barcode == "GOOD")) is not None
    assert db.scalar(select(Product).where(Product.barcode == "BAD")) is None


@pytest.mark.parametrize("active,expected", [("true",True),("1",True),("activo",True),("si",True),("false",False),("0",False),("inactivo",False),("no",False)])
def test_active_values(context, active, expected):
    _, client = context
    content = f"barcode,name,sale_price,active\nACT{active},Uno,1000,{active}\n"
    mapping = {"barcode":"barcode","name":"name","sale_price":"sale_price","active":"active"}
    row = preview(client, content, mapping=mapping).json()["rows"][0]
    assert row["parsed_data"]["active"] is expected


def test_bad_mapping_headers_and_limits(context):
    _, client = context
    basic = "barcode,name,sale_price\nA,Uno,1000\n"
    assert preview(client, basic, mapping={"barcode":"barcode","name":"barcode","sale_price":"sale_price"}).status_code == 422
    assert preview(client, basic, mapping={"barcode":"barcode","name":"name"}).status_code == 422
    assert preview(client, "a,a\n1,2\n", mapping={"a":"barcode"}).status_code == 422
    oversized = "barcode,name,sale_price\n" + ("X" * (10 * 1024 * 1024))
    assert preview(client, oversized, mapping={"barcode":"barcode","name":"name","sale_price":"sale_price"}).status_code == 413


def test_template(context):
    _, client = context
    response = client.get("/api/v1/products/import/template")
    assert response.status_code == 200 and response.content.startswith(b"\xef\xbb\xbfbarcode,name")


def test_duplicate_created_between_preview_and_confirm_is_skipped(context):
    db, client = context
    content = HEADER + "RACE1,Uno,1000,0,0,0,,\n"
    assert preview(client, content).json()["ready_count"] == 1
    db.add(Product(barcode="RACE1", name="Creado por otro", sale_price=1000, unit_cost=0)); db.commit()
    result = confirm(client, content).json()
    assert result["imported_count"] == 0 and result["skipped_count"] == 1


def test_invalid_active_is_row_error(context):
    _, client = context
    content = "barcode,name,sale_price,active\nACTBAD,Uno,1000,quizás\n"
    mapping = {"barcode":"barcode","name":"name","sale_price":"sale_price","active":"active"}
    row = preview(client, content, mapping=mapping).json()["rows"][0]
    assert row["status"] == "ERROR" and "activo inválido" in row["errors"][0]


def test_text_is_trimmed_without_changing_capitalization(context):
    _, client = context
    content = "barcode,name,sale_price,unit,description\n TRIM1 ,  Mi Producto  ,1000, paquete , detalle útil \n"
    mapping = {"barcode":"barcode","name":"name","sale_price":"sale_price","unit":"unit","description":"description"}
    data = preview(client, content, mapping=mapping).json()["rows"][0]["parsed_data"]
    assert data["barcode"] == "TRIM1" and data["name"] == "Mi Producto"
    assert data["unit"] == "paquete" and data["description"] == "detalle útil"


def test_quoted_colombian_money_with_comma_delimiter(context):
    _, client = context
    content = 'barcode,name,sale_price,purchase_cost\nMONEY1,Uno,"18.500,00","3.500,00"\n'
    mapping = {"barcode":"barcode","name":"name","sale_price":"sale_price","purchase_cost":"purchase_cost"}
    data = preview(client, content, mapping=mapping, delimiter=",").json()["rows"][0]["parsed_data"]
    assert data["sale_price"] == "18500.00" and data["purchase_cost"] == "3500.00"


def test_confirm_uses_shared_product_builder(context, monkeypatch):
    _, client = context
    import app.services.product_imports as importer
    original = importer.add_product_to_session
    calls = []
    def tracked(db, body, operator_id):
        calls.append(body.barcode)
        return original(db, body, operator_id)
    monkeypatch.setattr(importer, "add_product_to_session", tracked)
    assert confirm(client, HEADER + "SHARED1,Uno,1000,0,1,0,,\n").json()["imported_count"] == 1
    assert calls == ["SHARED1"]
