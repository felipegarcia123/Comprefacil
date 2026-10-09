"""Operational control panel of electronic invoices + cutover readiness.
Fake Alegra only; the real account is unreachable from here (invalid URL)."""
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_db
from app.domain.enums import ElectronicInvoiceStatus, PaymentMethod, SaleSource, SaleStatus
from app.main import app
from app.models import (CashSession, Customer, ElectronicInvoice, PaymentChannel, Product, Sale, SaleItem,
                        SalePayment, User)
from app.services import electronic_invoicing as invoicing
from app.services import invoicing_ops as ops

API = "/api/v1/electronic-invoices"


def signed(expires_in: int) -> str:
    return f"https://cdn.alegra.test/f.pdf?Expires={int(time.time()) + expires_in}&Signature=x"


class FakeAlegra:
    def __init__(self):
        self.get_calls = 0

    def get_invoice(self, provider_invoice_id, fields=None):
        self.get_calls += 1
        return {"id": provider_invoice_id, "status": "open", "pdf": signed(3600), "xml": signed(3600),
                "stamp": {"legalStatus": "STAMPED_AND_ACCEPTED", "cufe": "cufe-1"}}


@pytest.fixture()
def ctx(monkeypatch):
    from app.core.config import settings
    for key, value in {"alegra_enabled": True, "alegra_email": "a@b.co", "alegra_token": "t",
                       "alegra_api_url": "http://alegra.invalid", "alegra_number_template_id": "17",
                       "alegra_final_consumer_contact_id": "", "alegra_auto_issue": False,
                       "alegra_auto_issue_stamp": False, "alegra_manual_issue_enabled": False,
                       "alegra_item_manual_bulk": "372", "alegra_item_manual_sack": "2988"}.items():
        monkeypatch.setattr(settings, key, value)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    fake = FakeAlegra()
    monkeypatch.setattr(invoicing, "AlegraProvider", lambda: fake)
    with Session(engine, expire_on_commit=False) as db:
        user = User(name="Cajero")
        cash = PaymentChannel(name="Efectivo", payment_type=PaymentMethod.CASH, affects_cash=True,
                              alegra_payment_method="CASH")
        nequi = PaymentChannel(name="Nequi", payment_type=PaymentMethod.TRANSFER, alegra_payment_method="CREDIT_TRANSFER")
        card = PaymentChannel(name="Datáfono", payment_type=PaymentMethod.CARD)
        mapped = Product(barcode="7702177022504", name="LECHE", sale_price=Decimal("6800"), unit_cost=Decimal("5000"),
                         alegra_item_id="287")
        unmapped = Product(barcode="7702191523995", name="PAN", sale_price=Decimal("4000"), unit_cost=Decimal("2000"))
        invalid = Product(barcode="COL-1", name="DEMO", sale_price=Decimal("1"), unit_cost=Decimal("1"))
        final = Customer(alegra_contact_id="1", identification_type="CC", identification_number="222222222222",
                         name="CONSUMIDOR FINAL", is_final_consumer=True)
        db.add_all([user, cash, nequi, card, mapped, unmapped, invalid, final]); db.commit()
        env = {"db": db, "user": user, "cash": cash, "nequi": nequi, "card": card, "mapped": mapped,
               "unmapped": unmapped, "fake": fake, "settings": settings}

        def override(): yield db
        app.dependency_overrides[get_db] = override
        try:
            yield env, TestClient(app)
        finally:
            app.dependency_overrides.clear()


def sale(env, lines=(("mapped", 1),), channel="cash", source=SaleSource.POS_PRODUCT, manual=False,
         minutes_ago=5, card_type=None):
    db = env["db"]
    s = Sale(number=f"V-{uuid4().hex[:8].upper()}", status=SaleStatus.COMPLETED, created_by=env["user"].id,
             source=source, completed_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago))
    total = Decimal("0")
    for key, qty in lines:
        p = env[key]
        s.items.append(SaleItem(product_id=p.id, product_name=p.name, quantity=Decimal(qty), unit_price=p.sale_price,
                                unit_cost=p.unit_cost, line_total=p.sale_price * qty))
        total += p.sale_price * qty
    if manual:
        s.items.append(SaleItem(product_id=None, product_name="Bulto 40 kg", quantity=Decimal(1),
                                unit_price=Decimal("90000"), unit_cost=Decimal(0), line_total=Decimal("90000")))
        total += Decimal("90000")
    s.total = total
    s.payments.append(SalePayment(payment_channel_id=env[channel].id, amount=total, card_type=card_type))
    db.add(s); db.commit(); db.refresh(s)
    return s


def record(env, s, status, **fields):
    r = ElectronicInvoice(sale_id=s.id, status=status, **fields)
    env["db"].add(r); env["db"].commit()
    return r


@pytest.fixture()
def populated(ctx):
    env, client = ctx
    session = CashSession(opening_amount=0, opened_by=env["user"].id,
                          opened_at=datetime.now(timezone.utc) - timedelta(hours=2))
    env["db"].add(session); env["db"].commit()
    rows = {
        "accepted": record(env, sale(env), ElectronicInvoiceStatus.ISSUED, provider_invoice_id="4615",
                           provider_number="ERG4613", provider_status="open", cufe="c",
                           legal_status="STAMPED_AND_ACCEPTED_WITH_OBSERVATIONS", pdf_url=signed(-10)),
        "draft": record(env, sale(env), ElectronicInvoiceStatus.ISSUED, provider_invoice_id="9",
                        provider_status="draft"),
        "blocked": record(env, sale(env, lines=(("unmapped", 1),)), ElectronicInvoiceStatus.BLOCKED,
                          error_code="PREFLIGHT"),
        "mixed": record(env, sale(env, channel="card"), ElectronicInvoiceStatus.BLOCKED, error_code="PREFLIGHT"),
        "error": record(env, sale(env), ElectronicInvoiceStatus.ERROR, error_code="AlegraNetworkError",
                        needs_reconciliation=True),
        "held": record(env, sale(env), ElectronicInvoiceStatus.BLOCKED, error_code=invoicing.REVIEW_HOLD_CODE),
        "pending": record(env, sale(env), ElectronicInvoiceStatus.PENDING),
        "old": record(env, sale(env, minutes_ago=60 * 24 * 3), ElectronicInvoiceStatus.ISSUED,
                      provider_invoice_id="1", provider_status="open", legal_status="STAMPED_AND_ACCEPTED", cufe="x"),
    }
    sale(env)  # a completed sale of the jornada without any invoice (manual mode)
    return env, client, rows, session


# ---- counts / list / filters ---------------------------------------------------------------------------------

def test_summary_counts_by_display_state_for_the_current_jornada(populated):
    env, client, rows, _ = populated
    body = client.get(f"{API}/summary").json()
    counts = body["counts"]
    assert counts["ACCEPTED"] == 1 and counts["DRAFT"] == 1 and counts["BLOCKED"] == 2 and counts["ERROR"] == 1
    assert counts["HELD"] == 1 and counts["PENDING"] == 1 and counts["SENT"] == 0
    assert body["sales_without_invoice"] == 1                      # the old (3 days ago) one is outside the jornada
    assert client.get(f"{API}/summary", params={"scope": "all"}).json()["counts"]["ACCEPTED"] == 2


def test_list_filters_by_state_sale_number_and_customer(populated):
    env, client, rows, _ = populated
    accepted = client.get(f"{API}/list", params={"state": "ACCEPTED"}).json()
    assert accepted["total"] == 1 and accepted["items"][0]["provider_number"] == "ERG4613"
    assert accepted["items"][0]["customer"] == "Consumidor final"
    number = env["db"].get(Sale, rows["error"].sale_id).number
    by_number = client.get(f"{API}/list", params={"sale_number": number[-5:].lower()}).json()
    assert [i["sale_number"] for i in by_number["items"]] == [number]
    assert client.get(f"{API}/list", params={"customer": "consumidor"}).json()["total"] == 7
    assert client.get(f"{API}/list", params={"state": "NOPE"}).status_code == 422


def test_draft_is_never_listed_as_issued(populated):
    env, client, rows, _ = populated
    item = client.get(f"{API}/list", params={"state": "DRAFT"}).json()["items"][0]
    assert item["display_status"] == "DRAFT"


# ---- human reasons -------------------------------------------------------------------------------------------

def test_blocked_reasons_are_human(populated):
    env, client, rows, _ = populated
    unmapped = client.get(f"{API}/{rows['blocked'].id}").json()
    # creation off (default): the product is a blocker, explained as "se crearán al activar la creación"
    assert unmapped["reasons"][0]["message"].startswith("Productos sin ítem en Alegra: se crearán automáticamente")
    assert unmapped["reasons"][0]["detail"] == "PAN"
    card = client.get(f"{API}/{rows['mixed'].id}").json()["reasons"][0]
    assert card["code"] == "PAYMENT_METHOD" and "débito o crédito" in card["message"]
    held = client.get(f"{API}/{rows['held'].id}").json()
    assert held["reasons"][0]["message"] == "Venta anterior apartada para revisión manual."
    err = client.get(f"{API}/{rows['error'].id}").json()["reasons"][0]
    assert "se verificará si la factura ya existe" in err["message"]
    for body in (unmapped, held):
        assert all(r["message"] and not r["message"].isupper() for r in body["reasons"])


def test_mixed_payment_and_unconfigured_bulto_messages(ctx, monkeypatch):
    env, client = ctx
    s = sale(env, lines=(("mapped", 2),))
    s.payments.append(SalePayment(payment_channel_id=env["nequi"].id, amount=Decimal("1"))); env["db"].commit()
    assert ops.preflight_reasons(env["db"], s)[0]["message"] == "La venta usó varios medios de pago y necesita revisión."
    monkeypatch.setattr(env["settings"], "alegra_item_manual_sack", "")
    bulto = sale(env, lines=(), source=SaleSource.MANUAL_SACK, manual=True)
    assert ops.preflight_reasons(env["db"], bulto)[0]["message"] == "La venta de bulto no tiene ítem de facturación configurado."


def test_detail_has_sale_payments_and_fiscal_fields(populated):
    env, client, rows, _ = populated
    body = client.get(f"{API}/{rows['accepted'].id}").json()
    assert body["display_status"] == "ACCEPTED" and body["provider_number"] == "ERG4613" and body["cufe"] == "c"
    assert body["payments"][0]["channel_name"] == "Efectivo" and body["items"][0]["product_name"] == "LECHE"
    assert body["has_pdf"] and body["retryable"] is False


# ---- PDF / XML refresh ---------------------------------------------------------------------------------------

def test_expired_link_is_refreshed_by_get_and_valid_link_is_reused(populated):
    env, client, rows, _ = populated
    fake = env["fake"]
    first = client.get(f"{API}/{rows['accepted'].id}/document", params={"kind": "pdf"}).json()
    assert first["refreshed"] is True and fake.get_calls == 1           # stored link had expired
    second = client.get(f"{API}/{rows['accepted'].id}/document", params={"kind": "pdf"}).json()
    assert second["refreshed"] is False and fake.get_calls == 1 and second["url"] == first["url"]
    xml = client.get(f"{API}/{rows['accepted'].id}/document", params={"kind": "xml"}).json()
    assert xml["url"] and fake.get_calls == 1                              # refreshed together with the pdf
    env["db"].refresh(rows["accepted"])
    assert rows["accepted"].provider_invoice_id == "4615" and rows["accepted"].status == ElectronicInvoiceStatus.ISSUED


def test_document_of_a_record_without_remote_invoice_is_404(populated):
    env, client, rows, _ = populated
    assert client.get(f"{API}/{rows['blocked'].id}/document", params={"kind": "pdf"}).status_code == 404
    assert env["fake"].get_calls == 0


# ---- retry visibility ----------------------------------------------------------------------------------------

def test_retry_hidden_and_refused_while_auto_issue_is_off(populated, monkeypatch):
    env, client, rows, _ = populated
    assert client.get(f"{API}/{rows['error'].id}").json()["retryable"] is False
    assert client.post(f"{API}/{rows['error'].id}/retry").status_code == 403
    monkeypatch.setattr(env["settings"], "alegra_auto_issue", True)
    assert client.get(f"{API}/{rows['error'].id}").json()["retryable"] is True
    assert client.get(f"{API}/{rows['held'].id}").json()["retryable"] is False       # held: never
    assert client.get(f"{API}/{rows['accepted'].id}").json()["retryable"] is False   # issued: never


def test_mode_label_never_exposes_env_names(ctx, monkeypatch):
    env, client = ctx
    assert client.get(f"{API}/mode").json() == {"automatic": False, "label": "Facturación automática desactivada"}
    monkeypatch.setattr(env["settings"], "alegra_auto_issue", True)
    body = client.get(f"{API}/mode").json()
    assert body["label"] == "Facturación automática activa" and "ALEGRA" not in str(body)


# ---- readiness -----------------------------------------------------------------------------------------------

def test_readiness_ready_with_closed_cash_and_clean_pipeline(ctx):
    env, client = ctx
    body = client.get(f"{API}/cutover-readiness").json()
    assert body["status"] == "READY_FOR_CUTOVER", body["reasons"]
    assert body["auto_issue"] is False
    assert next(c for c in body["checks"] if c["code"] == "CATALOG_COVERAGE")["blocking"] is False


def test_readiness_not_ready_with_open_cash_session(ctx):
    env, client = ctx
    env["db"].add(CashSession(opening_amount=0, opened_by=env["user"].id)); env["db"].commit()
    body = client.get(f"{API}/cutover-readiness").json()
    assert body["status"] == "NOT_READY"
    assert "El cambio debe realizarse con la caja cerrada." in body["reasons"]


def test_readiness_not_ready_reasons(ctx, monkeypatch):
    env, client = ctx
    monkeypatch.setattr(env["settings"], "alegra_item_manual_sack", "")
    env["nequi"].alegra_payment_method = None
    s = sale(env)
    record(env, s, ElectronicInvoiceStatus.PROCESSING)
    record(env, sale(env), ElectronicInvoiceStatus.ERROR, needs_reconciliation=True)
    body = client.get(f"{API}/cutover-readiness").json()
    codes = {c["code"] for c in body["checks"] if not c["ok"]}
    assert body["status"] == "NOT_READY"
    assert {"BULTO_ITEM", "PAYMENT_MAPPINGS", "NO_PROCESSING", "NO_PENDING_RECONCILIATION"} <= codes
    assert any("Nequi" in r for r in body["reasons"])


def test_readiness_never_changes_flags(ctx):
    env, client = ctx
    client.get(f"{API}/cutover-readiness")
    assert env["settings"].alegra_auto_issue is False and env["settings"].alegra_auto_issue_stamp is False


# ---- blocked sales / catalog -----------------------------------------------------------------------------------

def test_blocked_sales_grouped_for_the_jornada(ctx):
    env, client = ctx
    env["db"].add(CashSession(opening_amount=0, opened_by=env["user"].id,
                              opened_at=datetime.now(timezone.utc) - timedelta(hours=1)))
    env["db"].commit()
    sale(env); sale(env, lines=(("unmapped", 1),)); sale(env, channel="card")
    sale(env, lines=(), source=SaleSource.MANUAL_SACK, manual=True)
    held = sale(env); record(env, held, ElectronicInvoiceStatus.BLOCKED, error_code=invoicing.REVIEW_HOLD_CODE)
    body = client.get(f"{API}/blocked-sales").json()
    assert body["groups"] == {"READY": 2, "UNMAPPED_PRODUCT": 1, "MANUAL": 0, "PAYMENT_METHOD": 1, "OTHER": 0}
    assert body["blocked"] == 2 and body["held"] == 1                   # bulto is READY (item 2988 configured)


def test_catalog_coverage_and_unmapped_export(ctx):
    env, client = ctx
    body = client.get(f"{API}/catalog-coverage").json()
    assert (body["active"], body["mapped"], body["unmapped"], body["unmapped_invalid_barcode"]) == (3, 1, 2, 1)
    listing = client.get(f"{API}/unmapped-products", params={"q": "pan"}).json()
    assert listing["total"] == 1 and listing["items"][0]["valid_barcode"] is True
    csv_body = client.get(f"{API}/unmapped-products.csv").text
    assert "7702191523995" in csv_body and "COL-1" in csv_body and "7702177022504" not in csv_body


# ---- legacy invoices stay out of the automatic pipeline --------------------------------------------------------

def test_legacy_invoices_never_enter_the_auto_pipeline(ctx, monkeypatch):
    env, client = ctx
    monkeypatch.setattr(env["settings"], "alegra_auto_issue", True)
    for code in invoicing.HOLD_CODES:
        s = sale(env)
        record(env, s, ElectronicInvoiceStatus.BLOCKED, error_code=code)

        class Boom:
            def create_invoice(self, payload): raise AssertionError("no debe emitir")
        factory = lambda: Session(env["db"].get_bind(), expire_on_commit=False)
        invoicing.auto_issue_for_sale(s.id, session_factory=factory, provider=Boom())
        with pytest.raises(invoicing.InvoiceHeldForReviewError):
            invoicing.retry_invoice(env["db"], env["db"].scalar(select(ElectronicInvoice).where(
                ElectronicInvoice.sale_id == s.id)), s, provider=Boom())
