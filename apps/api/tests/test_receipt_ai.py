import base64
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_db
from app.main import app
from app.models import OperationalCategory, Product, Purchase, ReceiptEquivalence, Supplier, User
from app.core.config import settings
from app.services.receipt_ai import find_candidates
from app.services.receipt_equivalences import confirm_equivalence, replace_equivalence, set_equivalence_active
from app.schemas.receipt_ai import ReceiptEquivalenceActive, ReceiptEquivalenceCreate


@pytest.fixture()
def context():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        db.add_all([OperationalCategory(code="CIGARROS", name="Cigarros")])
        db.commit()
        def override():
            yield db
        app.dependency_overrides[get_db] = override
        try:
            yield db, TestClient(app)
        finally:
            app.dependency_overrides.clear()


def test_receipt_ai_text_fixture_is_reviewable_and_read_only(context):
    db, client = context
    category = db.scalar(__import__("sqlalchemy").select(OperationalCategory).where(OperationalCategory.code == "CIGARROS"))
    db.add(Product(barcode="7700000000001", name="Coca Cola Original 1.5 L", sale_price=5000, unit_cost=3000,
                   operational_category_id=category.id))
    db.commit()
    content = "code,name,packages,units_per_package,unit_cost\nSUP-1,Coca Cola 1.5 L,4,12,3000\n".encode()
    response = client.post("/api/v1/receipt-ai/analyze", json={
        "filename": "fixture.csv", "content_type": "text/csv",
        "content_base64": base64.b64encode(content).decode(), "vendor_hint": "Coca-Cola",
    })
    assert response.status_code == 200
    line = response.json()["lines"][0]
    assert line["total_units"] == "48" and line["candidates"][0]["name"] == "Coca Cola Original 1.5 L"
    assert db.query(Product).count() == 1


def test_receipt_ai_image_is_explicitly_pending_without_vision_provider(context):
    _, client = context
    response = client.post("/api/v1/receipt-ai/analyze", json={
        "filename": "colanta.jpg", "content_type": "image/jpeg",
        "content_base64": base64.b64encode(b"not-a-real-image").decode(),
    })
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "REVIEW_REQUIRED" and data["lines"] == []
    assert data["adapter"] == "vision-provider-not-configured"


def test_receipt_ai_openai_adapter_is_opt_in_and_structured(context, monkeypatch):
    db, client = context
    category = db.scalar(__import__("sqlalchemy").select(OperationalCategory).where(OperationalCategory.code == "CIGARROS"))
    db.add(Product(barcode="7700000000002", name="Leche Colanta 1 L", sale_price=4000, unit_cost=2500,
                   operational_category_id=category.id))
    db.commit()
    monkeypatch.setattr(settings, "receipt_ai_vision_enabled", True)
    monkeypatch.setattr(settings, "receipt_ai_openai_api_key", "test-key")

    class FakeResponse:
        def raise_for_status(self): pass
        def json(self): return {"output_text": '{"lines":[{"original_text":"LECHE COLANTA 1L","supplier_code":null,"interpreted_name":"Leche Colanta 1 L","brand":"Colanta","variant":null,"size":"1 L","package_quantity":4,"units_per_package":1,"total_units":4,"unit_cost":2500,"discount":null,"confidence":0.96,"ambiguities":[]}]}' }

    monkeypatch.setattr("app.services.receipt_ai.httpx.post", lambda *args, **kwargs: FakeResponse())
    response = client.post("/api/v1/receipt-ai/analyze", json={
        "filename": "colanta.jpg", "content_type": "image/jpeg",
        "content_base64": base64.b64encode(b"image-bytes").decode(), "vendor_hint": "Colanta",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["adapter"] == "openai-responses" and data["lines"][0]["total_units"] == "4"
    assert data["lines"][0]["candidates"][0]["name"] == "Leche Colanta 1 L"


def test_candidate_matching_penalizes_brand_and_volume_conflicts(context):
    db, _ = context
    category = db.scalar(__import__("sqlalchemy").select(OperationalCategory).where(OperationalCategory.code == "CIGARROS"))
    db.add_all([
        Product(name="AXE AQUA CITRUS", sale_price=1000, unit_cost=500, operational_category_id=category.id),
        Product(name="GASEOSA COCA-COLA ZERO X 1500 ML", sale_price=1000, unit_cost=500, operational_category_id=category.id),
        Product(name="COCA COLA ZERO X 250 ML", sale_price=1000, unit_cost=500, operational_category_id=category.id),
        Product(name="GASEOSA COCA COLA RETORNABLE X 2000 ML", sale_price=1000, unit_cost=500, operational_category_id=category.id),
        Product(name="COMBO 3 LITROS POSTOBON", sale_price=1000, unit_cost=500, operational_category_id=category.id),
    ])
    db.commit()

    fresh = find_candidates(db, "Fresh Citrus", size="400 ml", vendor_hint="Coca-Cola")
    zero = find_candidates(db, "Coca Cola Zero", brand="Coca-Cola", size="1.5 L", vendor_hint="Coca-Cola")
    cola_2l = find_candidates(db, "Coca Cola", brand="Coca-Cola", variant="RP", size="2 L", vendor_hint="Coca-Cola")
    combo = find_candidates(db, "Combo Ahorro CCSO", brand="Coca-Cola", size="3 L", vendor_hint="Coca-Cola")

    assert fresh == []
    assert zero[0].name == "GASEOSA COCA-COLA ZERO X 1500 ML"
    assert cola_2l[0].name == "GASEOSA COCA COLA RETORNABLE X 2000 ML"
    assert combo == []


def test_equivalence_requires_manual_confirmation_and_is_reusable(context):
    db, _ = context
    category = db.scalar(__import__("sqlalchemy").select(OperationalCategory).where(OperationalCategory.code == "CIGARROS"))
    user = User(name="Receipt reviewer")
    product = Product(name="COCA-COLA ORIGINAL 1.5 L", sale_price=1000, unit_cost=500,
                      operational_category_id=category.id)
    db.add_all([user, product]); db.commit(); db.refresh(product)
    body = ReceiptEquivalenceCreate(provider="Coca-Cola", material_code="135664", product_id=str(product.id),
                                    source_description="COCA COLA 1.5LT PET(12)", source_brand="Coca-Cola", source_size="1.5 L")
    saved = confirm_equivalence(db, body)
    assert saved.active and saved.product_name == product.name
    remembered = find_candidates(db, "Coca Cola", vendor_hint="Coca-Cola", material_code="135664")
    assert len(remembered) == 1 and remembered[0].name == product.name
    row = db.query(ReceiptEquivalence).one()
    set_equivalence_active(db, row.id, ReceiptEquivalenceActive(active=False))
    assert db.query(ReceiptEquivalence).one().active is False


def test_equivalence_writes_reject_non_coca_cola_provider(context):
    db, _ = context
    category = db.scalar(__import__("sqlalchemy").select(OperationalCategory).where(OperationalCategory.code == "CIGARROS"))
    user = User(name="Receipt reviewer")
    product = Product(name="POSTOBON 1.5 L", sale_price=1000, unit_cost=500,
                      operational_category_id=category.id)
    db.add_all([user, product]); db.commit(); db.refresh(product)
    body = ReceiptEquivalenceCreate(provider="Postobón", material_code="POST-1", product_id=str(product.id))
    with pytest.raises(HTTPException) as exc:
        confirm_equivalence(db, body)
    assert exc.value.status_code == 422
    assert "Coca-Cola" in str(exc.value.detail)
    assert db.query(ReceiptEquivalence).count() == 0


def test_legacy_non_coca_equivalence_cannot_be_reactivated_or_replaced(context):
    db, _ = context
    category = db.scalar(__import__("sqlalchemy").select(OperationalCategory).where(OperationalCategory.code == "CIGARROS"))
    user = User(name="Receipt reviewer")
    product = Product(name="LEGACY PRODUCT", sale_price=1000, unit_cost=500,
                      operational_category_id=category.id)
    db.add_all([user, product]); db.commit(); db.refresh(product)
    row = ReceiptEquivalence(provider="TestProvider", material_code="LEGACY-1", product_id=product.id,
                             product_name_snapshot=product.name, confirmation_method="legacy",
                             confirmed_by=user.id, active=False)
    db.add(row); db.commit(); db.refresh(row)
    with pytest.raises(HTTPException) as active_exc:
        set_equivalence_active(db, row.id, ReceiptEquivalenceActive(active=True))
    assert active_exc.value.status_code == 422
    replacement = ReceiptEquivalenceCreate(provider="Coca-Cola", material_code="135664", product_id=str(product.id))
    with pytest.raises(HTTPException) as replace_exc:
        replace_equivalence(db, row.id, replacement)
    assert replace_exc.value.status_code == 422


def test_receipt_purchase_requires_enabled_environment_and_human_confirmation(context, monkeypatch):
    db, client = context
    category = db.scalar(__import__("sqlalchemy").select(OperationalCategory).where(OperationalCategory.code == "CIGARROS"))
    supplier = Supplier(name="Coca-Cola", active=True)
    db.add(User(name="Receipt operator")); db.flush()
    product = Product(name="COCA-COLA 1.5 L", sale_price=1000, unit_cost=500,
                      operational_category_id=category.id)
    db.add_all([supplier, product]); db.commit(); db.refresh(supplier); db.refresh(product)
    body = {"confirmed": True, "supplier_id": str(supplier.id), "invoice_number": "CC-100",
            "items": [{"product_id": str(product.id), "quantity": 12, "unit_cost": 800}]}
    blocked = client.post("/api/v1/receipt-ai/purchase", json=body)
    assert blocked.status_code == 403
    monkeypatch.setattr(settings, "receipt_ai_write_enabled", True)
    unconfirmed = client.post("/api/v1/receipt-ai/purchase", json={**body, "confirmed": False})
    assert unconfirmed.status_code == 409
    created = client.post("/api/v1/receipt-ai/purchase", json=body)
    assert created.status_code == 201 and created.json()["status"] == "DRAFT", created.text
    assert db.query(Purchase).count() == 1
    duplicate = client.post("/api/v1/receipt-ai/purchase", json=body)
    assert duplicate.status_code == 409
