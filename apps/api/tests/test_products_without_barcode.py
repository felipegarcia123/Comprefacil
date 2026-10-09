"""Products without barcode (approved 2026-09-28): nullable barcode, Venta by product_id
(XOR with barcode, same business logic), physical count by product_id, invoicing."""
import io
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_db
from app.domain.enums import InventoryMovementType, PaymentMethod, SaleStatus
from app.main import app
from app.models import CashSession, InventoryMovement, PaymentChannel, Product, Sale, SalePayment, User
from app.services import electronic_invoicing as invoicing
from app.services.inventory import current_stock
from app.services.pos import add_item, create_sale

API = "/api/v1"


@pytest.fixture()
def ctx(monkeypatch):
    from app.core.config import settings
    for key, value in {"alegra_api_url": "http://alegra.invalid", "alegra_enabled": True, "alegra_email": "a@b.co",
                       "alegra_token": "t", "alegra_number_template_id": "17",
                       "alegra_final_consumer_contact_id": "1"}.items():
        monkeypatch.setattr(settings, key, value)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(name="Cajero")
        cash = PaymentChannel(name="Efectivo", payment_type=PaymentMethod.CASH, affects_cash=True, alegra_payment_method="CASH")
        huevo = Product(barcode=None, name="HUEVO AA X UND", sale_price=Decimal("550"), unit_cost=Decimal("400"),
                        alegra_item_id="98")
        arepa = Product(barcode="", name="AREPA SIN SAL", sale_price=Decimal("1000"), unit_cost=Decimal("700"))
        leche = Product(barcode="7702177022504", name="LECHE", sale_price=Decimal("6800"), unit_cost=Decimal("5000"),
                        alegra_item_id="287")
        inactive = Product(barcode=None, name="VIEJO", sale_price=Decimal("1"), unit_cost=Decimal("1"), active=False)
        db.add_all([user, cash, huevo, arepa, leche, inactive]); db.flush()
        for p, qty in ((huevo, 30), (arepa, 2), (leche, 5)):
            db.add(InventoryMovement(product_id=p.id, movement_type=InventoryMovementType.PURCHASE, quantity=Decimal(qty),
                                     unit_cost=p.unit_cost, user_id=user.id))
        db.add(CashSession(opening_amount=0, opened_by=user.id, opened_at=datetime.now(timezone.utc) - timedelta(minutes=5)))
        db.commit()
        def override(): yield db
        app.dependency_overrides[get_db] = override
        try:
            yield db, TestClient(app), {"huevo": huevo, "arepa": arepa, "leche": leche, "inactive": inactive, "cash": cash}
        finally:
            app.dependency_overrides.clear()


# ---- nullable barcode -----------------------------------------------------------------------------------------

def test_empty_and_blank_barcode_become_null_and_many_nulls_coexist(ctx):
    db, _, p = ctx
    assert p["huevo"].barcode is None and p["arepa"].barcode is None          # "" -> NULL
    db.add(Product(barcode="   ", name="OTRO", sale_price=Decimal(1), unit_cost=Decimal(1))); db.commit()
    assert db.scalar(select(func.count()).select_from(Product).where(Product.barcode.is_(None))) == 4


def test_real_barcode_stays_unique(ctx):
    db, _, _ = ctx
    db.add(Product(barcode="7702177022504", name="DUPLICADA", sale_price=Decimal(1), unit_cost=Decimal(1)))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_alegra_item_id_is_unique_when_present(ctx):
    db, _, _ = ctx
    db.add(Product(barcode=None, name="OTRO HUEVO", sale_price=Decimal(1), unit_cost=Decimal(1), alegra_item_id="98"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# ---- Venta ------------------------------------------------------------------------------------------------------

def test_sell_by_product_id_without_barcode(ctx):
    db, client, p = ctx
    sale = create_sale(db)
    body = client.post(f"{API}/sales/{sale.id}/items", json={"product_id": str(p["huevo"].id), "quantity": "3"}).json()
    line = body["items"][0]
    assert line["product_name"] == "HUEVO AA X UND" and line["quantity"] == "3.000" and line["unit_price"] == "550.00"
    again = client.post(f"{API}/sales/{sale.id}/items", json={"product_id": str(p["huevo"].id)}).json()
    assert len(again["items"]) == 1 and again["items"][0]["quantity"] == "4.000"      # same line logic


def test_scanner_by_barcode_unchanged_and_both_paths_share_the_line(ctx):
    db, client, p = ctx
    sale = create_sale(db)
    client.post(f"{API}/sales/{sale.id}/items", json={"barcode": "7702177022504"})
    body = client.post(f"{API}/sales/{sale.id}/items", json={"product_id": str(p["leche"].id)}).json()
    assert len(body["items"]) == 1 and body["items"][0]["quantity"] == "2.000"


@pytest.mark.parametrize("payload", [{}, {"barcode": "   "}, {"barcode": "7702177022504", "product_id": None, "x": 1}])
def test_xor_barcode_product_id(ctx, payload):
    db, client, p = ctx
    sale = create_sale(db)
    if "x" in payload:
        payload = {"barcode": "7702177022504", "product_id": str(p["leche"].id)}
    assert client.post(f"{API}/sales/{sale.id}/items", json=payload).status_code == 422


def test_inactive_and_unknown_product_id_rejected(ctx):
    db, client, p = ctx
    sale = create_sale(db)
    assert client.post(f"{API}/sales/{sale.id}/items", json={"product_id": str(p["inactive"].id)}).status_code == 409
    assert client.post(f"{API}/sales/{sale.id}/items",
                       json={"product_id": "00000000-0000-0000-0000-000000000000"}).status_code == 404


def test_checkout_stock_rules_apply_to_products_without_barcode(ctx):
    db, client, p = ctx
    sale = add_item(db, create_sale(db).id, quantity=Decimal("3"), product_id=p["arepa"].id)   # stock 2
    response = client.post(f"{API}/sales/{sale.id}/checkout", json={"channel_id": str(p["cash"].id), "received_amount": "3000"})
    assert response.status_code == 409 and "Stock insuficiente" in response.json()["detail"]
    ok = add_item(db, create_sale(db).id, quantity=Decimal("2"), product_id=p["huevo"].id)
    done = client.post(f"{API}/sales/{ok.id}/checkout", json={"channel_id": str(p["cash"].id), "received_amount": "1100"})
    assert done.status_code == 200 and current_stock(db, p["huevo"].id) == Decimal("28")


def test_product_search_finds_products_without_barcode_by_name(ctx):
    db, client, p = ctx
    found = client.get(f"{API}/products", params={"q": "huevo"}).json()
    assert [(r["name"], r["barcode"]) for r in found] == [("HUEVO AA X UND", None)]


# ---- Conteo físico ------------------------------------------------------------------------------------------------

def xlsx(header, rows):
    wb = Workbook(); ws = wb.active; ws.title = "Conteo"; ws.append(header)
    for r in rows:
        ws.append(r)
    buffer = io.BytesIO(); wb.save(buffer)
    return buffer.getvalue()


def preview(client, content):
    return client.post(f"{API}/inventory/count/preview", content=content,
                       headers={"Content-Type": "application/octet-stream"}).json()


def test_template_has_product_id_and_empty_barcode_never_none(ctx):
    db, client, p = ctx
    ws = load_workbook(io.BytesIO(client.get(f"{API}/inventory/count/template").content))["Conteo"]
    rows = {r[2]: r for r in ws.iter_rows(min_row=2, values_only=True)}
    assert rows["HUEVO AA X UND"][0] == str(p["huevo"].id)
    assert rows["HUEVO AA X UND"][1] in (None, "") and rows["HUEVO AA X UND"][1] != "None"   # empty cell, never the text "None"


def test_count_by_product_id_for_product_without_barcode(ctx):
    db, client, p = ctx
    body = preview(client, xlsx(["product_id", "barcode", "name", "physical_count"],
                                [[str(p["huevo"].id), None, "HUEVO", 25]]))
    row = body["rows"][0]
    assert row["status"] == "CHANGE" and row["delta"] == "-5.000" and row["source_product_id"] == str(p["huevo"].id)
    confirm = client.post(f"{API}/inventory/count/confirm", json={"batch_id": body["batch_id"], "rows": [
        {"product_id": row["product_id"], "expected_current_stock": row["current_stock"], "physical_count": 25}]})
    assert confirm.status_code == 201 and current_stock(db, p["huevo"].id) == Decimal("25")
    repeat = client.post(f"{API}/inventory/count/confirm", json={"batch_id": body["batch_id"], "rows": [
        {"product_id": row["product_id"], "expected_current_stock": "25", "physical_count": 25}]})
    assert repeat.status_code == 409                                                  # batch idempotency


def test_old_template_by_barcode_still_works(ctx):
    db, client, p = ctx
    row = preview(client, xlsx(["barcode", "physical_count"], [["7702177022504", 7]]))["rows"][0]
    assert row["status"] == "CHANGE" and row["product_id"] == str(p["leche"].id)


def test_product_id_and_barcode_must_agree(ctx):
    db, client, p = ctx
    row = preview(client, xlsx(["product_id", "barcode", "physical_count"],
                               [[str(p["huevo"].id), "7702177022504", 3]]))["rows"][0]
    assert row["status"] == "ERROR" and "no corresponden" in row["errors"][0]


def test_invalid_product_id_is_error_and_stock_changed_is_409(ctx):
    db, client, p = ctx
    assert preview(client, xlsx(["product_id", "physical_count"], [["no-es-uuid", 3]]))["rows"][0]["status"] == "ERROR"
    body = preview(client, xlsx(["product_id", "physical_count"], [[str(p["huevo"].id), 20]]))
    row = body["rows"][0]
    add = create_sale(db); add_item(db, add.id, quantity=Decimal(1), product_id=p["huevo"].id)
    client.post(f"{API}/sales/{add.id}/checkout", json={"channel_id": str(p["cash"].id), "received_amount": "550"})
    stale = client.post(f"{API}/inventory/count/confirm", json={"batch_id": body["batch_id"], "rows": [
        {"product_id": row["product_id"], "expected_current_stock": row["current_stock"], "physical_count": 20}]})
    assert stale.status_code == 409


# ---- Facturación ------------------------------------------------------------------------------------------------

def completed(db, p, key):
    user = db.scalar(select(User))
    sale = Sale(number=f"V-{key}", status=SaleStatus.COMPLETED, created_by=user.id, total=p[key].sale_price,
                completed_at=datetime.now(timezone.utc))
    from app.models import SaleItem
    sale.items.append(SaleItem(product_id=p[key].id, product_name=p[key].name, quantity=Decimal(1),
                               unit_price=p[key].sale_price, unit_cost=p[key].unit_cost, line_total=p[key].sale_price))
    sale.payments.append(SalePayment(payment_channel_id=p["cash"].id, amount=p[key].sale_price))
    db.add(sale); db.commit()
    return sale


def test_no_barcode_with_alegra_item_is_ready_and_payload_uses_item(ctx):
    db, _, p = ctx
    sale = completed(db, p, "huevo")
    assert invoicing.preflight_sale(db, sale, allow_remote=False)["status"] == "READY"
    assert invoicing.build_alegra_invoice_payload(sale, "1", "17")["items"][0]["id"] == "98"


def test_no_barcode_without_alegra_item_is_blocked_unmapped(ctx):
    db, _, p = ctx
    result = invoicing.preflight_sale(db, completed(db, p, "arepa"), allow_remote=False)
    assert result["status"] == "BLOCKED" and result["lines"][0]["code"] == "UNMAPPED_PRODUCT"
