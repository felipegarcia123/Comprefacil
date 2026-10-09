"""Physical audit (interventoría física): scan/search -> found (1 by default) -> optional real
count -> finish -> report. Never writes the inventory ledger."""
import io
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_db
from app.domain.enums import InventoryMovementType, SaleStatus
from app.main import app
from app.models import InventoryMovement, Product, Sale, SaleItem, User

API = "/api/v1/physical-audits"


@pytest.fixture()
def ctx():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(name="Cajero")
        p = {
            "leche": Product(barcode="7702177022504", name="LECHE", sale_price=Decimal(6800), unit_cost=Decimal(5000),
                             alegra_item_id="287"),
            "huevo": Product(barcode=None, name="HUEVO AA X UND", sale_price=Decimal(700), unit_cost=Decimal(600),
                             alegra_item_id="98"),
            "arroz": Product(barcode="7701234567890", name="ARROZ", sale_price=Decimal(3000), unit_cost=Decimal(2500)),
            "viejo": Product(barcode="7700000000017", name="VIEJO SIN STOCK", sale_price=Decimal(1), unit_cost=Decimal(1)),
            "vendido": Product(barcode="7700000000024", name="VENDIDO HACE POCO", sale_price=Decimal(1), unit_cost=Decimal(1)),
            "con_stock": Product(barcode="7700000000031", name="CON STOCK NO VISTO", sale_price=Decimal(1),
                                 unit_cost=Decimal(1)),
            "inactivo": Product(barcode="7700000000048", name="INACTIVO", sale_price=Decimal(1), unit_cost=Decimal(1),
                                active=False),
        }
        db.add(user); db.add_all(p.values()); db.flush()
        for key, qty in (("leche", 5), ("huevo", 30), ("con_stock", 4)):
            db.add(InventoryMovement(product_id=p[key].id, movement_type=InventoryMovementType.PURCHASE,
                                     quantity=Decimal(qty), unit_cost=Decimal(1), user_id=user.id))
        sale = Sale(number="V-TEST", created_by=user.id, status=SaleStatus.COMPLETED, total=Decimal(1), completed_at=datetime.now(timezone.utc) - timedelta(days=3))
        sale.items.append(SaleItem(product_id=p["vendido"].id, product_name="VENDIDO HACE POCO", quantity=Decimal(1),
                                   unit_price=Decimal(1), unit_cost=Decimal(1), line_total=Decimal(1)))
        db.add(sale); db.commit()
        def override(): yield db
        app.dependency_overrides[get_db] = override
        try:
            yield db, TestClient(app), p
        finally:
            app.dependency_overrides.clear()


def _ledger(db):
    return db.scalar(select(func.count()).select_from(InventoryMovement))


def test_walk_found_counted_and_report(ctx):
    db, client, p = ctx
    movements = _ledger(db)
    audit = client.post(API).json()
    assert audit["status"] == "OPEN" and audit["active_products"] == 6
    assert client.post(API).json()["id"] == audit["id"]                          # one walk at a time: resume
    aid = audit["id"]

    body = client.post(f"{API}/{aid}/scan", json={"barcode": "7702177022504"}).json()   # found = 1
    leche = next(i for i in body["items"] if i["name"] == "LECHE")
    assert leche["quantity"] == 1 and leche["counted"] is False
    body = client.post(f"{API}/{aid}/scan", json={"product_id": str(p["huevo"].id)}).json()  # by name, no barcode
    huevo = next(i for i in body["items"] if i["name"] == "HUEVO AA X UND")
    body = client.put(f"{API}/{aid}/items/{huevo['id']}", json={"quantity": 28}).json()     # optional real count
    huevo = next(i for i in body["items"] if i["name"] == "HUEVO AA X UND")
    assert huevo["counted"] is True and huevo["difference"] == -2
    client.post(f"{API}/{aid}/scan", json={"barcode": "7701234567890"})                 # ARROZ, sin Alegra
    body = client.post(f"{API}/{aid}/scan", json={"barcode": "7701234567890"}).json()  # scanned twice = counting
    arroz = next(i for i in body["items"] if i["name"] == "ARROZ")
    assert arroz["quantity"] == 2 and arroz["counted"] is True and arroz["scans"] == 2

    assert client.post(f"{API}/{aid}/finish").json()["status"] == "FINISHED"
    assert client.post(f"{API}/{aid}/scan", json={"barcode": "7702177022504"}).status_code == 409

    r = client.get(f"{API}/{aid}/report").json()
    s = r["summary"]
    assert (s["active_products"], s["present"], s["not_found"], s["counted"], s["without_alegra"]) == (6, 3, 3, 2, 1)
    names = lambda section: sorted(x["name"] for x in r["sections"][section])
    assert names("not_found") == ["CON STOCK NO VISTO", "VENDIDO HACE POCO", "VIEJO SIN STOCK"]
    assert names("without_alegra") == ["ARROZ"]
    # first walk: no deactivation list at all (ledger stock is not reliable before a first count)
    assert "deactivate_candidates" not in r["sections"] and "deactivate_candidates" not in s
    assert s["to_create"] == 0
    assert s["counted_with_difference"] == 2
    assert _ledger(db) == movements                                              # never writes inventory
    assert db.get(Product, p["viejo"].id).active is True                        # never deactivates


def test_unknown_code_is_kept_as_product_to_create(ctx):
    db, client, p = ctx
    products = db.scalar(select(func.count()).select_from(Product))
    aid = client.post(API).json()["id"]
    body = client.post(f"{API}/{aid}/scan", json={"barcode": "7709999999999"}).json()
    assert body["item_id"] is None and body["unknown_code"] == {"barcode": "7709999999999", "scan_count": 1}
    assert body["found"] == 0 and body["unknown"] == 1
    body = client.post(f"{API}/{aid}/scan", json={"barcode": " 7709999999999 "}).json()   # again: scan_count + 1
    assert body["unknown_code"]["scan_count"] == 2 and body["unknown"] == 1
    client.post(f"{API}/{aid}/scan", json={"barcode": "123"})
    assert db.scalar(select(func.count()).select_from(Product)) == products        # never creates a Product
    r = client.get(f"{API}/{aid}/report").json()
    assert r["summary"]["to_create"] == 2
    top = r["sections"]["to_create"][0]
    assert top["barcode"] == "123" and top["status"] == "UNKNOWN_PRODUCT" and top["scan_count"] == 1
    assert {c["barcode"]: c["scan_count"] for c in r["sections"]["to_create"]} == {"123": 1, "7709999999999": 2}
    text = client.get(f"{API}/{aid}/report.csv", params={"section": "to_create"}).text
    assert text.lstrip("﻿").startswith("barcode,status,scan_count,first_scanned_at,last_scanned_at")
    assert "7709999999999,UNKNOWN_PRODUCT,2," in text
    # by product_id an unknown product is still an error (nothing to record)
    assert client.post(f"{API}/{aid}/scan", json={"product_id": "00000000-0000-0000-0000-000000000000"}).status_code == 404


def test_remove_and_reopen(ctx):
    db, client, p = ctx
    aid = client.post(API).json()["id"]
    assert client.post(f"{API}/{aid}/scan", json={}).status_code == 422
    body = client.post(f"{API}/{aid}/scan", json={"barcode": "7702177022504"}).json()
    assert [i["name"] for i in body["items"]] == ["LECHE"]
    assert client.delete(f"{API}/{aid}/items/{body['items'][0]['id']}").json()["found"] == 0
    client.post(f"{API}/{aid}/finish")
    assert client.post(f"{API}/{aid}/reopen").json()["status"] == "OPEN"
    assert client.get(f"{API}/current").json()["id"] == aid


def test_csv_and_count_template_only_counted(ctx):
    db, client, p = ctx
    aid = client.post(API).json()["id"]
    client.post(f"{API}/{aid}/scan", json={"barcode": "7702177022504"})                 # found only
    item = client.post(f"{API}/{aid}/scan", json={"product_id": str(p["huevo"].id)}).json()["item_id"]
    client.put(f"{API}/{aid}/items/{item}", json={"quantity": 28})
    csv_text = client.get(f"{API}/{aid}/report.csv", params={"section": "not_found"}).text
    assert "ARROZ" in csv_text and "LECHE" not in csv_text
    assert client.get(f"{API}/{aid}/report.csv", params={"section": "nope"}).status_code == 422
    ws = load_workbook(io.BytesIO(client.get(f"{API}/{aid}/count-template").content))["Conteo"]
    rows = [[c.value for c in r] for r in ws.iter_rows(min_row=2)]
    assert len(rows) == 1 and rows[0][0] == str(p["huevo"].id) and rows[0][2] == "HUEVO AA X UND" and rows[0][4] == 28
