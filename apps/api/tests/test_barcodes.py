from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_db
from app.main import app
from app.models import CashSession, Product, User
from app.services.barcodes import alternate_barcode, find_product_by_barcode
from app.services.pos import add_item, create_sale

UPC = "070847038764"          # UPC-A, 12 digits
EAN = "0070847038764"         # same GTIN as EAN-13


@pytest.fixture()
def ctx():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(name="Administrador")
        db.add(user); db.flush()
        db.add(CashSession(opening_amount=Decimal("0"), opened_by=user.id)); db.commit()
        def override_db(): yield db
        app.dependency_overrides[get_db] = override_db
        try:
            yield db, TestClient(app)
        finally:
            app.dependency_overrides.clear()


def add(db, barcode, name="Producto"):
    product = Product(barcode=barcode, name=name, sale_price=Decimal("1000"), unit_cost=Decimal("500"))
    db.add(product); db.commit()
    return product


def test_alternate_only_toggles_a_single_leading_zero():
    assert alternate_barcode(EAN) == UPC
    assert alternate_barcode(UPC) == EAN
    assert alternate_barcode("7702004001234") is None          # 13 digits not starting with 0
    assert alternate_barcode("00070847038764") is None         # 14 digits
    assert alternate_barcode("70847038764") is None            # 11 digits: never guess zeros
    assert alternate_barcode("COL-ABR-001") is None


def test_ean13_scan_finds_stored_upca_without_touching_barcode(ctx):
    db, client = ctx
    product = add(db, UPC)
    assert find_product_by_barcode(db, EAN).id == product.id
    assert client.get("/api/v1/products/by-barcode", params={"barcode": EAN}).json()["barcode"] == UPC
    sale = add_item(db, create_sale(db).id, EAN, Decimal("1"))
    assert sale.items[0].product_id == product.id
    db.refresh(product)
    assert product.barcode == UPC


def test_upca_scan_finds_stored_ean13(ctx):
    db, client = ctx
    product = add(db, EAN)
    assert find_product_by_barcode(db, UPC).id == product.id
    assert client.get("/api/v1/products/by-barcode", params={"barcode": UPC}).json()["barcode"] == EAN
    assert add_item(db, create_sale(db).id, UPC, Decimal("1")).items[0].product_id == product.id


def test_exact_match_wins_and_no_duplicates_are_created(ctx):
    db, _ = ctx
    product = add(db, UPC)
    assert find_product_by_barcode(db, UPC).id == product.id
    assert db.query(Product).count() == 1


def test_both_spellings_belonging_to_different_products_is_a_conflict(ctx):
    db, client = ctx
    add(db, UPC, "A"); add(db, EAN, "B")
    with pytest.raises(HTTPException) as error:
        find_product_by_barcode(db, UPC)
    assert error.value.status_code == 409
    assert client.get("/api/v1/products/by-barcode", params={"barcode": EAN}).status_code == 409
    with pytest.raises(HTTPException) as scan:
        add_item(db, create_sale(db).id, UPC, Decimal("1"))
    assert scan.value.status_code == 409


def test_unknown_and_arbitrary_zero_variants_are_not_found(ctx):
    db, client = ctx
    add(db, UPC)
    assert find_product_by_barcode(db, "0000847038764") is None
    assert find_product_by_barcode(db, "70847038764") is None      # 11 digits: leading zero not guessed
    assert client.get("/api/v1/products/by-barcode", params={"barcode": "9999999999999"}).status_code == 404


def test_pos_search_accepts_both_spellings(ctx):
    db, client = ctx
    add(db, UPC, "Monster")
    assert [p["barcode"] for p in client.get("/api/v1/products", params={"q": EAN}).json()] == [UPC]
    assert [p["barcode"] for p in client.get("/api/v1/products", params={"q": UPC}).json()] == [UPC]
