from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.domain.enums import ElectronicInvoiceStatus, PaymentMethod, SaleStatus
from app.models import (ElectronicInvoice, OperationalCategory, PaymentChannel, Product, Sale, SaleItem,
                        SalePayment, User)
from app.services import electronic_invoicing as invoicing
from app.services.alegra_client import AlegraServerError, AlegraValidationError


class _UnitClient:
    """GET /items/{id} of the unit check before invoicing: the item has unit "unit"."""
    def get(self, path, params=None):
        return {"id": path.rsplit("/", 1)[1], "inventory": {"unit": "unit"}}


class FakeProvider:
    """Stands in for AlegraProvider — the service layer only depends on this
    method contract, never on httpx, so tests never touch the network."""
    def __init__(self, create_response=None, create_error=None, get_response=None):
        self.create_response = create_response
        self.create_error = create_error
        self.get_response = get_response
        self.create_calls = []
        self.get_calls = []
        self.client = _UnitClient()

    def create_invoice(self, payload):
        self.create_calls.append(payload)
        if self.create_error:
            raise self.create_error
        return self.create_response

    def get_invoice(self, provider_invoice_id, fields=None):
        self.get_calls.append((provider_invoice_id, fields))
        return self.get_response

    def list_invoices_page(self, *, date, client_id, start=0, limit=30):
        return [], 0  # reconciliation: nothing in Alegra for this sale


@pytest.fixture()
def invoicing_ctx(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "alegra_enabled", True)
    monkeypatch.setattr(settings, "alegra_email", "shop@example.com")
    monkeypatch.setattr(settings, "alegra_token", "token-123")
    monkeypatch.setattr(settings, "alegra_number_template_id", "5")
    monkeypatch.setattr(settings, "alegra_api_url", "http://alegra.invalid")  # never the real account, whatever .env says

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(name="Cajero")
        category = OperationalCategory(code="SUPER", name="Super")
        mapped = Product(barcode="M1", name="Producto mapeado", sale_price=Decimal("10000"),
                         unit_cost=Decimal("6000"), operational_category_id=category.id,
                         alegra_item_id="77", alegra_tax_id="9")
        no_tax = Product(barcode="M2", name="Sin impuesto mapeado", sale_price=Decimal("5000"),
                         unit_cost=Decimal("3000"), operational_category_id=category.id,
                         alegra_item_id="88", alegra_tax_id=None)
        unmapped = Product(barcode="M3", name="Sin mapear", sale_price=Decimal("2000"),
                           unit_cost=Decimal("1000"), operational_category_id=category.id)
        cash = PaymentChannel(name="Efectivo", payment_type=PaymentMethod.CASH, affects_cash=True,
                              alegra_payment_method="CASH")
        db.add_all([user, category, mapped, no_tax, unmapped, cash]); db.commit()
        yield db, user, mapped, no_tax, unmapped
        monkeypatch.setattr(settings, "alegra_enabled", False)


def _sale(db, user, status=SaleStatus.COMPLETED, items=()):
    row = Sale(number=f"V-{uuid4().hex[:10]}", status=status, created_by=user.id,
              total=sum((i.line_total for i in items), Decimal("0")),
              completed_at=datetime.now(timezone.utc) if status == SaleStatus.COMPLETED else None)
    db.add(row); db.flush()
    for item in items:
        item.sale_id = row.id
        db.add(item)
    if row.total:  # paid in cash: the only payment method resolved without a pending decision
        cash = db.scalar(select(PaymentChannel).where(PaymentChannel.affects_cash.is_(True)))
        db.add(SalePayment(sale_id=row.id, payment_channel_id=cash.id, method=PaymentMethod.CASH,
                           amount=row.total, received_amount=row.total, change_amount=0))
    db.commit(); db.refresh(row)
    return row


def _item(product=None, quantity="1", price="10000", cost="6000"):
    return SaleItem(product_id=product.id if product else None,
                    category_code="SUPER", product_name=product.name if product else "Manual",
                    quantity=Decimal(quantity), unit_price=Decimal(price),
                    unit_cost=Decimal(cost), line_total=Decimal(quantity) * Decimal(price))


def test_build_payload_shape(invoicing_ctx):
    db, user, mapped, *_ = invoicing_ctx
    sale = _sale(db, user, items=[_item(mapped, "2", "10000", "6000")])
    db.refresh(sale)
    payload = invoicing.build_alegra_invoice_payload(sale, "42", "5")
    assert payload["client"] == {"id": "42"}
    assert payload["numberTemplate"] == {"id": "5"}
    assert payload["paymentForm"] == "CASH" and payload["paymentMethod"] == "CASH"
    assert payload["items"] == [{"id": "77", "price": 10000.0, "quantity": 2.0, "tax": [{"id": "9"}]}]
    assert "stamp" not in payload  # generate_stamp defaults to False
    assert str(sale.id) in payload["observations"]


def test_generate_stamp_true_adds_stamp_flag(invoicing_ctx):
    db, user, mapped, *_ = invoicing_ctx
    sale = _sale(db, user, items=[_item(mapped)])
    db.refresh(sale)
    payload = invoicing.build_alegra_invoice_payload(sale, "42", "5", generate_stamp=True)
    assert payload["stamp"] == {"generateStamp": True}


def test_sale_with_manual_item_fails_validation(invoicing_ctx):
    db, user, *_ = invoicing_ctx
    sale = _sale(db, user, items=[_item(None)])
    db.refresh(sale)
    reasons = invoicing.validate_sale_for_invoicing(sale)
    assert any("venta manual" in r for r in reasons)


def test_sale_with_unmapped_product_fails_validation(invoicing_ctx):
    db, user, mapped, no_tax, unmapped = invoicing_ctx
    sale = _sale(db, user, items=[_item(unmapped, price="2000", cost="1000")])
    db.refresh(sale)
    reasons = invoicing.validate_sale_for_invoicing(sale)
    assert any("alegra_item_id" in r for r in reasons)


def test_sale_with_no_tax_product_passes_validation_with_empty_tax_array(invoicing_ctx):
    """alegra_tax_id is optional: real Alegra invoices in this account carry
    tax: [] on every line (regime SIMPLIFIED_REGIME / No responsable de IVA).
    A product without alegra_tax_id is billed with no tax, not blocked."""
    db, user, mapped, no_tax, unmapped = invoicing_ctx
    sale = _sale(db, user, items=[_item(no_tax, price="5000", cost="3000")])
    db.refresh(sale)
    reasons = invoicing.validate_sale_for_invoicing(sale)
    assert reasons == []
    payload = invoicing.build_alegra_invoice_payload(sale, "42", "5")
    assert payload["items"] == [{"id": "88", "price": 5000.0, "quantity": 1.0, "tax": []}]


def test_electronic_invoice_is_unique_per_sale(invoicing_ctx):
    db, user, mapped, *_ = invoicing_ctx
    sale = _sale(db, user, items=[_item(mapped)])
    first = invoicing.create_electronic_invoice_record(db, sale)
    second = invoicing.create_electronic_invoice_record(db, sale)
    assert first.id == second.id
    count = db.scalars(select(ElectronicInvoice.id).where(ElectronicInvoice.sale_id == sale.id)).all()
    assert len(count) == 1


def test_alegra_disabled_blocks_issue(invoicing_ctx, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "alegra_enabled", False)
    db, user, mapped, *_ = invoicing_ctx
    sale = _sale(db, user, items=[_item(mapped)])
    with pytest.raises(invoicing.InvoicingConfigError):
        invoicing.issue_invoice(db, sale, "42", provider=FakeProvider())


def test_issue_invoice_success_mocked(invoicing_ctx):
    db, user, mapped, *_ = invoicing_ctx
    sale = _sale(db, user, items=[_item(mapped, "1", "10000", "6000")])
    provider = FakeProvider(create_response={"id": "999", "status": "open",
                                             "numberTemplate": {"prefix": "FE", "number": "1"}})
    record = invoicing.issue_invoice(db, sale, "42", provider=provider)
    assert record.status == ElectronicInvoiceStatus.ISSUED
    assert record.provider_invoice_id == "999"
    assert record.provider_number == "FE1"
    assert record.issued_at is not None
    assert len(provider.create_calls) == 1


def test_issue_invoice_external_error_leaves_sale_intact(invoicing_ctx):
    db, user, mapped, *_ = invoicing_ctx
    sale = _sale(db, user, items=[_item(mapped)])
    sale_total_before, sale_status_before = sale.total, sale.status
    provider = FakeProvider(create_error=AlegraServerError("Alegra caído"))

    record = invoicing.issue_invoice(db, sale, "42", provider=provider)

    assert record.status == ElectronicInvoiceStatus.ERROR
    assert record.error_code == "AlegraServerError"
    db.refresh(sale)
    assert sale.total == sale_total_before
    assert sale.status == sale_status_before


def test_already_issued_invoice_does_not_repost(invoicing_ctx):
    db, user, mapped, *_ = invoicing_ctx
    sale = _sale(db, user, items=[_item(mapped)])
    provider = FakeProvider(create_response={"id": "999", "status": "open"})
    invoicing.issue_invoice(db, sale, "42", provider=provider)
    assert len(provider.create_calls) == 1

    with pytest.raises(invoicing.AlreadyIssuedError):
        invoicing.issue_invoice(db, sale, "42", provider=provider)
    assert len(provider.create_calls) == 1  # no second POST


def test_retry_does_not_duplicate_local_record_and_reissues(invoicing_ctx):
    db, user, mapped, *_ = invoicing_ctx
    sale = _sale(db, user, items=[_item(mapped)])
    failing = FakeProvider(create_error=AlegraServerError("caído"))
    record = invoicing.issue_invoice(db, sale, "42", provider=failing)
    assert record.status == ElectronicInvoiceStatus.ERROR
    invoice_id = record.id

    succeeding = FakeProvider(create_response={"id": "1000", "status": "open"})
    retried = invoicing.retry_invoice(db, record, sale, provider=succeeding)

    assert retried.id == invoice_id  # same local row, not a new one
    assert retried.status == ElectronicInvoiceStatus.ISSUED
    count = db.scalars(select(ElectronicInvoice.id).where(ElectronicInvoice.sale_id == sale.id)).all()
    assert len(count) == 1


def test_retry_refuses_when_already_has_provider_id(invoicing_ctx):
    db, user, mapped, *_ = invoicing_ctx
    sale = _sale(db, user, items=[_item(mapped)])
    provider = FakeProvider(create_response={"id": "999", "status": "open"})
    record = invoicing.issue_invoice(db, sale, "42", provider=provider)
    with pytest.raises(invoicing.AlreadyIssuedError):
        invoicing.retry_invoice(db, record, sale, provider=provider)
    assert len(provider.create_calls) == 1


def test_sync_updates_provider_and_legal_status(invoicing_ctx):
    db, user, mapped, *_ = invoicing_ctx
    sale = _sale(db, user, items=[_item(mapped)])
    creator = FakeProvider(create_response={"id": "999", "status": "open"})
    record = invoicing.issue_invoice(db, sale, "42", provider=creator)

    syncer = FakeProvider(get_response={"id": "999", "status": "closed",
                                        "events": [{"type": "ACCEPTED_DIAN", "status": "ACCEPTED"}],
                                        "pdf": "https://alegra.test/inv.pdf", "xml": "https://alegra.test/inv.xml"})
    synced = invoicing.sync_invoice_status(db, record, provider=syncer)
    assert synced.provider_status == "closed"
    assert synced.legal_status == "ACCEPTED"
    assert synced.pdf_url == "https://alegra.test/inv.pdf"
    assert synced.last_synced_at is not None
    assert syncer.get_calls == [("999", "pdf,xml,events")]
