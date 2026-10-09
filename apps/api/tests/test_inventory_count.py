import io
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_db
from app.domain.enums import InventoryMovementType
from app.main import app
from app.models import CashSession, InventoryMovement, Product, User
from app.services.inventory import current_stock

PREVIEW = "/api/v1/inventory/count/preview"
CONFIRM = "/api/v1/inventory/count/confirm"
TEMPLATE = "/api/v1/inventory/count/template"


@pytest.fixture()
def ctx():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(name="Administrador")
        db.add(user); db.flush()
        session = CashSession(opening_amount=Decimal("0"), opened_by=user.id)
        db.add(session); db.commit()
        def override_db(): yield db
        app.dependency_overrides[get_db] = override_db
        try:
            yield db, TestClient(app), user, session
        finally:
            app.dependency_overrides.clear()


def product(db, user, barcode, name, stock=0, active=True):
    item = Product(barcode=barcode, name=name, sale_price=Decimal("1000"), unit_cost=Decimal("600"), active=active)
    db.add(item); db.flush()
    if stock:
        db.add(InventoryMovement(product_id=item.id, movement_type=InventoryMovementType.PURCHASE,
                                 quantity=Decimal(stock), unit_cost=Decimal("600"), user_id=user.id))
    db.commit()
    return item


def xlsx(rows, header=("barcode", "physical_count")):
    wb = Workbook(); ws = wb.active; ws.title = "Conteo"; ws.append(list(header))
    for row in rows:
        ws.append(list(row))
    buffer = io.BytesIO(); wb.save(buffer)
    return buffer.getvalue()


def upload(client, rows, **kwargs):
    return client.post(PREVIEW, content=xlsx(rows, **kwargs), headers={"Content-Type": "application/octet-stream"})


def confirm_body(preview):
    return {"batch_id": preview["batch_id"], "rows": [
        {"product_id": r["product_id"], "expected_current_stock": r["current_stock"], "physical_count": r["physical_count"]}
        for r in preview["rows"] if r["status"] == "CHANGE"]}


def movement_count(db):
    return db.scalar(select(func.count()).select_from(InventoryMovement))


def test_template_lists_only_active_products_with_text_barcodes(ctx):
    db, client, user, _ = ctx
    product(db, user, "070847038764", "Monster", stock=4)
    product(db, user, "7702004001234", "Café", stock=0)
    product(db, user, "COL-DEMO-1", "Demo inactivo", active=False)
    response = client.get(TEMPLATE)
    assert response.status_code == 200
    assert "spreadsheetml.sheet" in response.headers["content-type"]
    ws = load_workbook(io.BytesIO(response.content))["Conteo"]
    assert [c.value for c in ws[1]] == ["product_id", "barcode", "name", "current_stock", "physical_count",
                                        "category", "unit"]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    assert {r[1] for r in rows} == {"070847038764", "7702004001234"}       # leading zero preserved, no inactive
    monster = next(r for r in rows if r[1] == "070847038764")
    assert monster[3] == 4 and monster[4] is None
    assert str(db.scalar(select(Product.id).where(Product.barcode == "070847038764"))) == monster[0]
    assert ws["B2"].number_format == "@" and ws["B2"].data_type == "s"
    assert not any("iva" in str(c.value).lower() for c in ws[1])


def test_preview_computes_deltas_and_never_writes(ctx):
    db, client, user, _ = ctx
    product(db, user, "111", "Aceite Diana", stock=0)
    product(db, user, "222", "Arroz", stock=10)
    product(db, user, "333", "Sal", stock=5)
    product(db, user, "444", "Sin contar", stock=7)
    before = movement_count(db)
    data = upload(client, [("111", 12), ("222", 8), ("333", 5), ("444", None)]).json()
    by = {r["barcode"]: r for r in data["rows"]}
    assert (by["111"]["status"], Decimal(by["111"]["delta"])) == ("CHANGE", 12)
    assert (by["222"]["status"], Decimal(by["222"]["delta"])) == ("CHANGE", -2)
    assert by["333"]["status"] == "UNCHANGED" and by["444"]["status"] == "SKIPPED"
    assert Decimal(by["222"]["current_stock"]) == 10 and by["111"]["name"] == "Aceite Diana"
    s = data["summary"]
    assert (s["with_difference"], s["unchanged"], s["skipped_blank"], s["errors"]) == (2, 1, 1, 0)
    assert (Decimal(s["positive_units"]), Decimal(s["negative_units"]), Decimal(s["net_units"])) == (12, -2, 10)
    assert data["can_confirm"] is True and data["session_open"] is True
    assert movement_count(db) == before


@pytest.mark.parametrize("rows,message", [
    ([("999", 3)], "desconocido"),
    ([("111", -1)], "negativo"),
    ([("111", 1.5)], "decimales"),
    ([("111", "abc")], "entero"),
    ([("", 3)], "Falta product_id o barcode"),
    ([("111", 1), ("111", 2)], "duplicado"),
    ([("1.11E+2", 3)], "científica"),
])
def test_invalid_rows_are_errors_and_block_confirmation(ctx, rows, message):
    db, client, user, _ = ctx
    product(db, user, "111", "Aceite"); product(db, user, "222", "Arroz")
    data = upload(client, rows + [("222", 4)]).json()
    assert data["summary"]["errors"] >= 1 and data["can_confirm"] is False
    assert any(message in e for r in data["rows"] for e in r["errors"])


def test_inactive_product_is_an_error(ctx):
    db, client, user, _ = ctx
    product(db, user, "COL-1", "Demo", active=False)
    data = upload(client, [("COL-1", 1)]).json()
    assert data["rows"][0]["status"] == "ERROR" and "inactivo" in data["rows"][0]["errors"][0]


def test_numeric_cells_lose_leading_zero_and_are_not_guessed(ctx):
    db, client, user, _ = ctx
    product(db, user, "070847038764", "Monster", stock=1)
    data = upload(client, [(70847038764, 3)]).json()      # 11 digits: leading zero lost by Excel
    assert data["rows"][0]["status"] == "ERROR"


def test_upc_ean_variants_resolve_to_the_same_product_and_count_twice_is_duplicate(ctx):
    db, client, user, _ = ctx
    product(db, user, "070847038764", "Monster", stock=1)
    ok = upload(client, [("0070847038764", 3)]).json()
    assert ok["rows"][0]["status"] == "CHANGE" and ok["rows"][0]["barcode"] == "070847038764"
    dup = upload(client, [("070847038764", 3), ("0070847038764", 3)]).json()
    assert dup["summary"]["errors"] == 2


def test_bad_files_are_rejected(ctx):
    _, client, *_ = ctx
    assert client.post(PREVIEW, content=b"not a zip").status_code == 422
    assert client.post(PREVIEW, content=b"").status_code == 422
    missing = client.post(PREVIEW, content=xlsx([("1", 1)], header=("barcode", "qty")))
    assert missing.status_code == 422 and "physical_count" in missing.json()["detail"]


def test_confirm_creates_adjustment_movements_in_the_ledger(ctx):
    db, client, user, _ = ctx
    a = product(db, user, "111", "Aceite Diana", stock=0)
    b = product(db, user, "222", "Arroz", stock=10)
    c = product(db, user, "333", "Sal", stock=5)
    preview = upload(client, [("111", 12), ("222", 8), ("333", 5)]).json()
    before = movement_count(db)
    result = client.post(CONFIRM, json=confirm_body(preview))
    assert result.status_code == 201, result.text
    out = result.json()
    assert out["movements_created"] == 2 and Decimal(out["net_units"]) == 10
    assert movement_count(db) == before + 2                                # delta 0 => no movement
    assert current_stock(db, a.id) == 12 and current_stock(db, b.id) == 8 and current_stock(db, c.id) == 5
    moves = db.scalars(select(InventoryMovement).where(InventoryMovement.reference_id == UUID(preview["batch_id"]))).all()
    assert {m.quantity for m in moves} == {Decimal("12"), Decimal("-2")}
    assert all(m.movement_type == InventoryMovementType.ADJUSTMENT for m in moves)
    assert all(m.reason == f"Conteo físico inicial · lote {preview['batch_id'].replace('-', '')[:8].upper()}" for m in moves)


def test_confirming_the_same_batch_twice_does_not_change_inventory(ctx):
    db, client, user, _ = ctx
    a = product(db, user, "111", "Aceite", stock=0)
    preview = upload(client, [("111", 12)]).json()
    body = confirm_body(preview)
    assert client.post(CONFIRM, json=body).status_code == 201
    total, count = current_stock(db, a.id), movement_count(db)
    again = client.post(CONFIRM, json=body)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "COUNT_BATCH_ALREADY_APPLIED"
    assert current_stock(db, a.id) == total == 12 and movement_count(db) == count


def test_stock_changed_since_preview_returns_409_and_writes_nothing(ctx):
    db, client, user, _ = ctx
    a = product(db, user, "111", "Aceite", stock=10)
    b = product(db, user, "222", "Arroz", stock=0)
    preview = upload(client, [("111", 8), ("222", 3)]).json()
    db.add(InventoryMovement(product_id=a.id, movement_type=InventoryMovementType.SALE,
                             quantity=Decimal("-1"), unit_cost=Decimal("600"), user_id=user.id)); db.commit()
    before = movement_count(db)
    response = client.post(CONFIRM, json=confirm_body(preview))
    assert response.status_code == 409 and response.json()["detail"]["code"] == "COUNT_STOCK_CHANGED"
    assert "nuevo preview" in response.json()["detail"]["message"]
    assert movement_count(db) == before and current_stock(db, b.id) == 0    # all-or-nothing
    fresh = upload(client, [("111", 8), ("222", 3)]).json()                  # a new preview works
    assert client.post(CONFIRM, json=confirm_body(fresh)).status_code == 201
    assert current_stock(db, a.id) == 8 and current_stock(db, b.id) == 3


def test_confirm_requires_an_open_session(ctx):
    from datetime import datetime, timezone
    db, client, user, session = ctx
    product(db, user, "111", "Aceite")
    preview = upload(client, [("111", 4)]).json()
    session.closed_at = datetime.now(timezone.utc); session.closed_by = user.id; db.commit()
    closed_preview = upload(client, [("111", 4)]).json()
    assert closed_preview["session_open"] is False and closed_preview["can_confirm"] is False
    before = movement_count(db)
    response = client.post(CONFIRM, json=confirm_body(preview))
    assert response.status_code == 409 and response.json()["detail"]["code"] == "OPERATIONAL_SESSION_CLOSED"
    assert movement_count(db) == before


def test_confirm_rejects_unknown_or_inactive_products_without_writing(ctx):
    db, client, user, _ = ctx
    a = product(db, user, "111", "Aceite")
    body = {"batch_id": str(uuid4()), "rows": [
        {"product_id": str(a.id), "expected_current_stock": "0", "physical_count": 3},
        {"product_id": str(uuid4()), "expected_current_stock": "0", "physical_count": 3}]}
    before = movement_count(db)
    assert client.post(CONFIRM, json=body).status_code == 409
    a.active = False; db.commit()
    body["rows"] = body["rows"][:1]
    assert client.post(CONFIRM, json=body).status_code == 409
    assert movement_count(db) == before


def test_products_missing_from_the_file_are_untouched(ctx):
    db, client, user, _ = ctx
    product(db, user, "111", "Aceite", stock=0)
    other = product(db, user, "222", "Arroz", stock=9)
    preview = upload(client, [("111", 2)]).json()
    assert preview["summary"]["active_products_not_in_file"] == 1
    client.post(CONFIRM, json=confirm_body(preview))
    assert current_stock(db, other.id) == 9
