"""Catalog coverage of real sales, Pareto, priorities, Alegra item-creation preview
and the factual cutover report. Local data + fake Alegra items only."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_db
from app.domain.enums import PaymentMethod, SaleSource, SaleStatus
from app.main import app
from app.models import CashSession, PaymentChannel, Product, Sale, SaleItem, SalePayment, User
from app.services import catalog_coverage as cc

IMPORT_DAY = datetime(2026, 9, 25, 15, tzinfo=timezone.utc)


@pytest.fixture()
def env(monkeypatch):
    from app.core.config import settings
    for key, value in {"alegra_api_url": "http://alegra.invalid", "alegra_item_manual_bulk": "372",
                       "alegra_item_manual_sack": "", "alegra_auto_issue": False}.items():
        monkeypatch.setattr(settings, key, value)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(name="Cajero")
        cash = PaymentChannel(name="Efectivo", payment_type=PaymentMethod.CASH, affects_cash=True, alegra_payment_method="CASH")
        nequi = PaymentChannel(name="Nequi", payment_type=PaymentMethod.TRANSFER, alegra_payment_method="CREDIT_TRANSFER")
        card = PaymentChannel(name="Datáfono", payment_type=PaymentMethod.CARD)
        def product(code, name, price, mapped=None, active=True, created=IMPORT_DAY):
            return Product(barcode=code, name=name, sale_price=Decimal(price), unit_cost=Decimal(1), active=active,
                           alegra_item_id=mapped, created_at=created)
        p = {
            "leche": product("7702177022504", "LECHE", "6800", mapped="287"),
            "pan": product("7702191523995", "PAN", "4000"),
            "huevo": product("7702354956837", "HUEVO", "16000"),
            "arena": product("7501031311309", "ARENA", "32000"),
            "never": product("4005900036759", "  NUNCA   VENDIDO ", "1000"),
            "zero": product("7707211440016", "SIN PRECIO", "0"),
            "badcode": product("COL-9", "INTERNO", "500"),
            "demo": product("COL-1", "DEMO", "100", mapped="1106", active=False, created=IMPORT_DAY - timedelta(days=20)),
        }
        db.add_all([user, cash, nequi, card, *p.values()]); db.commit()
        yield {"db": db, "user": user, "cash": cash, "nequi": nequi, "card": card, "p": p, "settings": settings}


def sale(e, lines, when, channel="cash", source=SaleSource.POS_PRODUCT, manual=None, card_type=None, extra_payment=None):
    s = Sale(number=f"V-{uuid4().hex[:8]}", status=SaleStatus.COMPLETED, created_by=e["user"].id, source=source,
             completed_at=when)
    total = Decimal(0)
    for key, qty in lines:
        prod = e["p"][key]
        s.items.append(SaleItem(product_id=prod.id, product_name=prod.name, quantity=Decimal(qty),
                                unit_price=prod.sale_price, unit_cost=Decimal(1), line_total=prod.sale_price * qty))
        total += prod.sale_price * qty
    if manual:
        s.items.append(SaleItem(product_id=None, product_name=manual, quantity=Decimal(1), unit_price=Decimal(20000),
                                unit_cost=Decimal(0), line_total=Decimal(20000)))
        total += Decimal(20000)
    s.total = total
    s.payments.append(SalePayment(payment_channel_id=e[channel].id, amount=total, card_type=card_type))
    if extra_payment:
        s.payments.append(SalePayment(payment_channel_id=e[extra_payment].id, amount=Decimal(1)))
    e["db"].add(s); e["db"].commit()
    return s


@pytest.fixture()
def history(env):
    day = IMPORT_DAY + timedelta(days=1)
    sale(env, [("demo", 3)], IMPORT_DAY - timedelta(days=5))            # before the real catalog -> excluded
    sale(env, [("demo", 1)], day)                                        # inactive (demo) product -> excluded
    sale(env, [("leche", 2)], day)
    sale(env, [("leche", 1), ("pan", 1)], day + timedelta(hours=1))
    sale(env, [("pan", 2)], day + timedelta(hours=2))
    sale(env, [("pan", 1), ("huevo", 1)], day + timedelta(hours=3))
    sale(env, [("arena", 1)], day + timedelta(hours=4))
    sale(env, [], day + timedelta(hours=5), source=SaleSource.MANUAL_BULK, manual="Granel gato")
    sale(env, [], day + timedelta(hours=6), source=SaleSource.MANUAL_SACK, manual="Bulto perro")
    return env


def test_catalog_since_is_derived_from_the_data(history):
    assert cc.catalog_since(history["db"]).isoformat() == "2026-09-25"


def test_real_sales_exclude_demo_and_pre_catalog(history):
    sales, excluded = cc.real_sales(history["db"], cc.catalog_since(history["db"]))
    assert len(sales) == 7 and excluded == {"before_catalog": 1, "inactive_product": 1, "total_completed": 9}


def test_coverage_by_products_lines_and_value(history):
    cov = cc.coverage(history["db"])
    # lines: leche x2 lines mapped, pan x3, huevo, arena unmapped, granel covered (372), bulto uncovered
    assert cov["lines"] == {"total": 9, "mapped": 2, "unmapped": 5, "manual_covered": 1, "manual_uncovered": 1,
                            "pct": 33.3}
    assert cov["value"]["mapped"] == Decimal("20400") and cov["value"]["unmapped"] == Decimal("64000")
    assert cov["value"]["manual_covered"] == Decimal("20000") and cov["value"]["pct"] == 32.5   # 40.400 / 124.400
    assert cov["products"] == {"distinct_sold": 4, "distinct_sold_mapped": 1, "pct": 25.0}
    assert cov["sales"] == 7 and cov["sales_fully_invoiceable"] == 2        # leche x2 sale + granel sale
    assert cov["catalog"] == {"active": 7, "mapped": 1, "pct": 14.3}


def test_coverage_by_jornada(history):
    db = history["db"]
    db.add(CashSession(opening_amount=0, opened_by=history["user"].id, opened_at=IMPORT_DAY + timedelta(days=1),
                       closed_at=IMPORT_DAY + timedelta(days=1, hours=2, minutes=30)))
    db.commit()
    rows = cc.coverage(db)["by_session"]
    assert len(rows) == 1 and rows[0]["sales"] == 3 and rows[0]["lines"] == 4 and rows[0]["lines_pct"] == 50.0


def test_ranking_frequency_value_and_pareto(history):
    db = history["db"]
    ranking = cc.unmapped_ranking(db)
    assert [r["name"] for r in ranking] == ["PAN", "ARENA", "HUEVO"]           # lines, then revenue
    assert ranking[0]["times_sold"] == 3 and ranking[0]["units_sold"] == Decimal("4")
    assert ranking[0]["revenue"] == Decimal("16000")
    cov = cc.coverage(db)
    rows = {r["target_pct"]: r for r in cc.pareto(9, 3, ranking)}
    assert rows[80]["products_needed"] == 3 and rows[80]["reachable"]         # 3+3+1+1 = 8 >= 7.2
    assert rows[95]["reachable"] is False                                     # the bulto line is not a mapping problem


def test_priorities_derived_from_usage(history):
    ranking = cc.unmapped_ranking(history["db"])
    prio = cc.priorities(ranking, 9, 3)
    by_name = {r["name"]: prio[r["product_id"]] for r in ranking}
    assert by_name == {"PAN": "P0", "ARENA": "P0", "HUEVO": "P0"}


ALEGRA_ITEMS = [
    {"id": "287", "name": "LECHE", "reference": "7702177022504", "category": {"id": "5148", "name": "Ventas"}, "tax": [],
     "type": "product", "inventory": {"unit": "gram"}, "price": [{"idPriceList": "1"}]},
    {"id": "900", "name": "HUEVO", "reference": "9999999999994", "category": {"id": "5148", "name": "Ventas"}, "tax": [],
     "type": "product", "inventory": {"unit": "gram"}, "price": [{"idPriceList": "1"}]},
    {"id": "902", "name": "PAN EXISTENTE", "reference": "0" + "7702191523995"[:0] + "770219152399X", "category": {"id": "5148", "name": "Ventas"},
     "tax": [], "type": "product", "inventory": None, "price": [{"idPriceList": "1"}]},
    {"id": "901", "name": "ARENA X", "reference": "07501031311309", "category": {"id": "5148", "name": "Ventas"},
     "tax": [], "type": "product", "inventory": None, "price": [{"idPriceList": "1"}]},
]


def test_creation_preview_statuses_priorities_and_no_writes(history):
    db = history["db"]
    before = {p.barcode: p.alegra_item_id for p in history["p"].values()}
    preview = cc.creation_preview(db, ALEGRA_ITEMS, "5148")
    rows = {r["barcode"]: r for r in preview["rows"]}
    assert rows["7702191523995"]["status"] == "READY_TO_CREATE" and rows["7702191523995"]["creation_priority"] == "P0"
    assert rows["7501031311309"]["status"] == "REVIEW_REQUIRED" and "ceros" in rows["7501031311309"]["reason"]
    assert rows["7702354956837"]["status"] == "REVIEW_REQUIRED" and "mismo nombre" not in rows["7702354956837"]["reason"]
    assert rows["7707211440016"]["status"] == "REVIEW_REQUIRED" and "cero" in rows["7707211440016"]["reason"]
    assert rows["COL-9"]["status"] == "INVALID_BARCODE"
    never = rows["4005900036759"]
    assert never["status"] == "READY_TO_CREATE" and never["proposed_name"] == "NUNCA VENDIDO"
    assert never["creation_priority"] == "P3" and never["proposed_tax"] == "[]" and never["proposed_category"] == "5148"
    assert "7702177022504" not in rows                                          # already mapped: not in the preview
    for prod in history["p"].values():
        db.refresh(prod)
    assert {p.barcode: p.alegra_item_id for p in history["p"].values()} == before


def test_item_payload_mirrors_the_verified_bulto_creation():
    payload = cc.item_payload({"proposed_name": "PAN", "proposed_reference": "7702191523995",
                               "proposed_price": Decimal("4000"), "proposed_category": "5148"})
    assert payload == {"name": "PAN", "reference": "7702191523995", "type": "product",
                       "price": [{"idPriceList": "1", "price": 4000.0}], "tax": [], "category": {"id": "5148"},
                       "inventory": {"unit": "unit"}}


def test_name_normalization_is_conservative():
    assert cc.normalize_name("  LECHE\tX  1  L ") == ("LECHE X 1 L", False)
    assert cc.normalize_name("CAF�")[1] is True
    assert cc.normalize_name("Café Águila")[0] == "Café Águila"


def test_audit_alegra_items_distribution():
    audit = cc.audit_alegra_items(ALEGRA_ITEMS)
    assert audit["category"][0] == {"id": "5148", "name": "Ventas", "count": 4}
    assert audit["tax_empty"] == 4 and audit["inventory_unit"] == {"gram": 2, None: 2}


def test_cutover_report_is_factual(history, monkeypatch):
    db = history["db"]
    sale(history, [("leche", 1)], IMPORT_DAY + timedelta(days=2), extra_payment="nequi")     # mixed methods
    sale(history, [("leche", 1)], IMPORT_DAY + timedelta(days=2, hours=1), channel="card")   # card without subtype
    def override(): yield db
    app.dependency_overrides[get_db] = override
    try:
        body = TestClient(app).get("/api/v1/electronic-invoices/cutover-report").json()
    finally:
        app.dependency_overrides.clear()
    assert set(body) >= {"technical", "catalog", "payments", "manual", "operational"}
    assert body["manual"]["granel"]["ready"] is True and body["manual"]["bulto"]["ready"] is False
    assert body["operational"]["cash"] == "CLOSED" and body["operational"]["auto_issue"] is False
    assert body["payments"]["real_sales_blocked_by_payment"] == {"PAYMENT_METHOD_MIXED": 1, "PAYMENT_METHOD": 1}
    mixed = next(c for c in body["payments"]["cases"] if "distintos" in c["case"])
    assert mixed["ready"] is False and "un solo paymentMethod" in mixed["note"]
    assert "score" not in str(body).lower()


def test_existing_exact_or_twin_reference_is_already_exists(history):
    items = ALEGRA_ITEMS + [{"id": "950", "name": "PAN EN ALEGRA", "reference": "7702191523995", "category": {"id": "5148"}}]
    rows = {r["barcode"]: r for r in cc.creation_preview(history["db"], items, "5148")["rows"]}
    assert rows["7702191523995"]["status"] == "ALREADY_EXISTS" and "950" in rows["7702191523995"]["reason"]
