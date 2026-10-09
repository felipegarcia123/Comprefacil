"""HTTP 400 on POST /invoices is NOT proof that nothing was created (ERG4622, 2026-10-02:
Alegra created the draft and answered 400 when the DIAN step failed). The 400 body is kept,
the sale's marker is searched, a remote draft is linked as STAMP_FAILED and is finished by
stamping THAT invoice — never by a second POST /invoices. Fake Alegra only."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.domain.enums import ElectronicInvoiceStatus, PaymentMethod, SaleStatus
from app.models import ElectronicInvoice, PaymentChannel, Product, Sale, SaleItem, SalePayment, User
from app.services import electronic_invoicing as invoicing
from app.services.alegra_client import AlegraClient, AlegraNetworkError, AlegraValidationError

BODY_400 = {"error": {"code": 3061, "message": "La unidad de medida del ítem es obligatoria para la DIAN"}}


FINAL_CONTACT = {"id": "1", "name": "CONSUMIDOR FINAL", "status": "active",
                 "identificationObject": {"type": "CC", "number": "222222222222"}}


class FakeClient:
    def __init__(self, alegra):
        self.alegra = alegra

    def get(self, path, params=None):
        if path.startswith("/contacts/"):                          # Consumidor final verification
            return FINAL_CONTACT if path.endswith("/1") else None
        if path.startswith("/items/"):
            return self.alegra.items.get(path.rsplit("/", 1)[1])
        return []


class FakeAlegra:
    def __init__(self, unit="unit"):
        self.invoices: list[dict] = []
        self.calls: list[tuple] = []
        self.mode = "draft_then_400"            # draft_then_400 | reject | ok
        self.body = BODY_400
        self.search_error = None
        self.items = {"500": {"id": "500", "inventory": {"unit": unit} if unit else None}}
        self.client = FakeClient(self)

    def count(self, path):
        return len([c for c in self.calls if c == ("POST", path)])

    def create_invoice(self, payload):
        self.calls.append(("POST", "/invoices"))
        invoice = {"id": str(4624 + len(self.invoices)), "status": "draft", "date": payload["date"],
                   "client": payload["client"], "observations": payload["observations"],
                   "items": [{"id": line["id"], "name": "LENTEJA", "unit": None} for line in payload["items"]],
                   "numberTemplate": {"prefix": "ERG", "number": str(4622 + len(self.invoices)),
                                      "fullNumber": f"ERG{4622 + len(self.invoices)}"}}
        if self.mode == "reject":
            raise AlegraValidationError("rechazada", details=self.body)
        self.invoices.append(invoice)
        if self.mode == "draft_then_400":
            raise AlegraValidationError("La unidad de medida del ítem es obligatoria para la DIAN (código 3061)",
                                        details=self.body)
        invoice["status"] = "open"
        return invoice

    def list_invoices_page(self, *, date, client_id, start=0, limit=30):
        if self.search_error:
            raise self.search_error
        rows = [i for i in self.invoices if i["date"] == date and i["client"]["id"] == client_id]
        return rows[start:start + limit], len(rows)

    def get_invoice(self, provider_invoice_id, fields=None):
        return next(i for i in self.invoices if i["id"] == provider_invoice_id)

    def stamp_invoices(self, ids):
        self.calls.append(("POST", "/invoices/stamp"))
        for invoice in self.invoices:
            if invoice["id"] in [str(i) for i in ids]:
                invoice.update(status="open", stamp={"legalStatus": "STAMPED_AND_ACCEPTED", "cufe": "cufe-4624"})
        return [{"id": str(i), "success": True} for i in ids]


@pytest.fixture()
def env(monkeypatch, tmp_path):
    from app.core.config import settings
    for key, value in {"alegra_enabled": True, "alegra_email": "a@b.co", "alegra_token": "t",
                       "alegra_number_template_id": "17", "alegra_final_consumer_contact_id": "1",
                       "alegra_api_url": "http://alegra.invalid"}.items():
        monkeypatch.setattr(settings, key, value)
    engine = create_engine(f"sqlite:///{tmp_path / 'pos.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        user = User(name="Cajero")
        cash = PaymentChannel(name="Efectivo", payment_type=PaymentMethod.CASH, affects_cash=True,
                              alegra_payment_method="CASH")
        product = Product(barcode="7707335281496", name="LENTEJA MARITZA X 500 GR", sale_price=Decimal(4500),
                          unit_cost=Decimal(3763), alegra_item_id="500")
        db.add_all([user, cash, product]); db.flush()
        sale = Sale(number="V-BF5F8BA6", status=SaleStatus.COMPLETED, created_by=user.id, total=Decimal(4500),
                    completed_at=datetime(2026, 10, 2, 16, 46, tzinfo=timezone.utc))
        sale.items.append(SaleItem(product_id=product.id, product_name=product.name, quantity=Decimal(1),
                                   unit_price=Decimal(4500), unit_cost=Decimal(3763), line_total=Decimal(4500)))
        sale.payments.append(SalePayment(payment_channel_id=cash.id, amount=Decimal(4500), method=PaymentMethod.CASH,
                                         received_amount=Decimal(4500)))
        db.add(sale); db.commit()
        yield factory, sale.id


def issue(factory, sale_id, fake):
    with factory() as db:
        return invoicing.issue_invoice(db, db.get(Sale, sale_id), provider=fake, generate_stamp=True)


def record_of(factory, sale_id):
    with factory() as db:
        return db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale_id))


# ---- 400 without remote document: a real rejection, body kept -----------------------------------------------

def test_400_without_remote_document_is_a_real_rejection_and_keeps_the_body(env):
    factory, sale_id = env
    fake = FakeAlegra(); fake.mode = "reject"
    record = issue(factory, sale_id, fake)
    assert record.status == ElectronicInvoiceStatus.ERROR and record.error_code == "AlegraValidationError"
    assert record.provider_invoice_id is None and record.needs_reconciliation is False
    assert record.provider_response["_rejection_400"]["body"] == BODY_400
    assert fake.count("/invoices") == 1


# ---- 400 with a draft created: linked, STAMP_FAILED, never re-POSTed ----------------------------------------

def test_400_with_remote_draft_links_it_as_stamp_failed(env):
    factory, sale_id = env
    fake = FakeAlegra()
    record = issue(factory, sale_id, fake)
    assert record.provider_invoice_id == "4624" and record.provider_number == "ERG4622"
    assert record.display_status == "DRAFT" and record.error_code == "STAMP_FAILED"
    assert "unidad de medida" in record.error_message and record.needs_reconciliation is False
    assert record.provider_response["_rejection_400"]["body"] == BODY_400          # body survives the link
    assert fake.count("/invoices") == 1


def test_retry_or_reissue_of_a_remote_draft_never_posts_again(env):
    factory, sale_id = env
    fake = FakeAlegra()
    issue(factory, sale_id, fake)
    with pytest.raises(invoicing.RemoteDraftError):
        issue(factory, sale_id, fake)
    with factory() as db:                                       # retry = stamp THAT draft (C3), never a new invoice
        record = db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale_id))
        record = invoicing.retry_invoice(db, record, db.get(Sale, sale_id), provider=fake)
        assert record.status == ElectronicInvoiceStatus.ISSUED and record.cufe == "cufe-4624"
        assert record.provider_invoice_id == "4624" and record.display_status == "ACCEPTED"
        assert record.error_code is None
    assert fake.count("/invoices") == 1 and len(fake.invoices) == 1 and fake.count("/invoices/stamp") == 1


def test_400_unverifiable_is_reconciled_before_anything_else(env):
    factory, sale_id = env
    fake = FakeAlegra(); fake.search_error = AlegraNetworkError("sin red")
    record = issue(factory, sale_id, fake)
    assert record.status == ElectronicInvoiceStatus.ERROR and record.error_code == "REJECTED_UNVERIFIED"
    assert record.needs_reconciliation is True and record.provider_invoice_id is None
    fake.search_error = None                                    # retry: reconciliation finds the draft
    with factory() as db:
        record = db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale_id))
        record = invoicing.retry_invoice(db, record, db.get(Sale, sale_id), provider=fake, generate_stamp=True)
    assert record.provider_invoice_id == "4624" and record.error_code == "STAMP_FAILED"
    assert fake.count("/invoices") == 1


def test_invoice_id_named_in_the_400_body_is_checked_by_marker(env):
    factory, sale_id = env
    fake = FakeAlegra()
    fake.body = {"message": "Error al timbrar", "invoice": {"id": "4624"}}
    real_list = fake.list_invoices_page
    fake.list_invoices_page = lambda **kw: ([], 0)              # search by date/client misses it
    record = issue(factory, sale_id, fake)
    assert record.provider_invoice_id == "4624" and record.error_code == "STAMP_FAILED"
    fake.list_invoices_page = real_list


def test_client_keeps_nested_400_message_and_body():
    response = httpx.Response(400, json=BODY_400, request=httpx.Request("POST", "http://alegra.invalid/invoices"))
    with pytest.raises(AlegraValidationError) as caught:
        AlegraClient()._handle_response(response)
    assert str(caught.value) == "La unidad de medida del ítem es obligatoria para la DIAN (código 3061)"
    assert caught.value.details == BODY_400


# ---- recovery: stamp THAT draft --------------------------------------------------------------------------------

def test_stamp_plan_checks_unit_and_never_writes(env):
    factory, sale_id = env
    fake = FakeAlegra(unit="unit")
    issue(factory, sale_id, fake)                               # historical case: a draft exists remotely
    fake.items["500"]["inventory"] = None                       # ...and its item has no unit of measure
    with factory() as db:
        record = db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale_id))
        plan = invoicing.stamp_existing_invoice(db, record, db.get(Sale, sale_id), fake, confirm=True)
    assert plan["request"] == {"method": "POST", "path": "/invoices/stamp", "json": {"ids": ["4624"]}}
    assert any("unidad de medida" in p for p in plan["problems"]) and plan["stamped"] is False
    assert fake.count("/invoices/stamp") == 0 and fake.count("/invoices") == 1


def test_stamp_confirm_stamps_the_same_invoice_and_creates_none(env):
    factory, sale_id = env
    fake = FakeAlegra(unit="unit")
    issue(factory, sale_id, fake)
    with factory() as db:
        record = db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale_id))
        sale = db.get(Sale, sale_id)
        assert invoicing.stamp_existing_invoice(db, record, sale, fake, confirm=False)["problems"] == []
        assert fake.count("/invoices/stamp") == 0                # dry run
        plan = invoicing.stamp_existing_invoice(db, record, sale, fake, confirm=True)
        db.refresh(record)
    assert plan["warnings"] and "unit=null" in plan["warnings"][0]          # the draft line kept unit null
    assert plan["stamped"] is True and record.cufe == "cufe-4624" and record.display_status == "ACCEPTED"
    assert record.error_code is None and record.provider_invoice_id == "4624"
    assert fake.count("/invoices/stamp") == 1 and fake.count("/invoices") == 1 and len(fake.invoices) == 1


def test_controlled_issue_routes_a_remote_draft_to_the_stamp_plan(env):
    from app.services import controlled_issue
    factory, sale_id = env
    fake = FakeAlegra()
    issue(factory, sale_id, fake)
    with factory() as db:
        report = controlled_issue.run(db, db.get(Sale, sale_id), provider=fake, confirm=True,
                                      expected_total=Decimal("4500"))
        assert report["step"] == "STAMP_PENDING" and report["stamp_plan"]["invoice_id"] == "4624"
        stamp = controlled_issue.run_stamp(db, db.get(Sale, sale_id), provider=fake, confirm=True,
                                           expected_total=Decimal("4499"))
        assert "Confirmación rechazada" in stamp["problems"][0]
    assert fake.count("/invoices") == 1 and fake.count("/invoices/stamp") == 0


def test_stamp_rerun_on_already_stamped_invoice_is_ok_and_never_posts(env):
    # Re-running --stamp-existing after a successful stamp: Alegra now says status=open + CUFE.
    # That is not "not a draft" error: it reports ALREADY_STAMPED, syncs by GET and POSTs nothing.
    from app.services import controlled_issue
    factory, sale_id = env
    fake = FakeAlegra(unit="unit")
    issue(factory, sale_id, fake)
    with factory() as db:
        record = db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale_id))
        invoicing.stamp_existing_invoice(db, record, db.get(Sale, sale_id), fake, confirm=True)
        assert fake.count("/invoices/stamp") == 1
        report = controlled_issue.run_stamp(db, db.get(Sale, sale_id), provider=fake, confirm=True,
                                            expected_total=Decimal("4500"))
        db.refresh(record)
    assert report["step"] == "ALREADY_STAMPED" and report["ok"] is True and report["problems"] == []
    assert report["invoices_with_marker"] == 1 and record.cufe == "cufe-4624" and record.error_code is None
    assert fake.count("/invoices/stamp") == 1 and fake.count("/invoices") == 1


def test_open_invoice_without_cufe_is_never_stamped_nor_reissued(env):
    # open without CUFE is not a stampable draft and not proven stamped: report, POST nothing.
    from app.services import controlled_issue
    factory, sale_id = env
    fake = FakeAlegra(unit="unit")
    issue(factory, sale_id, fake)
    fake.invoices[0]["status"] = "open"                       # no stamp / cufe
    with factory() as db:
        report = controlled_issue.run_stamp(db, db.get(Sale, sale_id), provider=fake, confirm=True,
                                            expected_total=Decimal("4500"))
        assert report["ok"] is False and report["step"] == "STAMP_ATTEMPTED"
        assert any("no es un borrador sin timbrar" in p for p in report["problems"])
        record = db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale_id))
        with pytest.raises(invoicing.SaleValidationError):        # retry checks it, does not stamp, never a 2nd invoice
            invoicing.retry_invoice(db, record, db.get(Sale, sale_id), provider=fake)
    assert fake.count("/invoices/stamp") == 0 and fake.count("/invoices") == 1


# ---- C3: the web retry of a remote draft stamps THAT draft --------------------------------------------------------

def draft_record(factory, sale_id, fake):
    issue(factory, sale_id, fake)                              # 400 after creating it -> remote draft, STAMP_FAILED
    record = record_of(factory, sale_id)
    assert record.provider_status == "draft" and record.error_code == invoicing.STAMP_FAILED_CODE
    return record


def retry_of(factory, sale_id, fake):
    with factory() as db:
        record = db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale_id))
        return invoicing.retry_invoice(db, record, db.get(Sale, sale_id), provider=fake)


def test_retry_never_stamps_a_draft_without_this_sales_marker(env):
    factory, sale_id = env
    fake = FakeAlegra()
    draft_record(factory, sale_id, fake)
    fake.invoices[0]["observations"] = "POS sale_id=otra-venta"
    with pytest.raises(invoicing.SaleValidationError):
        retry_of(factory, sale_id, fake)
    record = record_of(factory, sale_id)
    assert record.status == ElectronicInvoiceStatus.ISSUED and record.cufe is None and "marca" in record.error_message
    assert fake.count("/invoices/stamp") == 0 and fake.count("/invoices") == 1


def test_retry_never_stamps_when_alegra_has_duplicates(env):
    factory, sale_id = env
    fake = FakeAlegra()
    draft_record(factory, sale_id, fake)
    with factory() as db:
        db.get(ElectronicInvoice, record_of(factory, sale_id).id).error_code = "DUPLICATE_IN_ALEGRA"; db.commit()
    with pytest.raises(invoicing.SaleValidationError):
        retry_of(factory, sale_id, fake)
    assert fake.count("/invoices/stamp") == 0 and fake.count("/invoices") == 1


def test_concurrent_retry_of_a_draft_stamps_once(env):
    factory, sale_id = env
    fake = FakeAlegra()
    record = draft_record(factory, sale_id, fake)
    with factory() as db:                                      # another retry is stamping it right now
        row = db.get(ElectronicInvoice, record.id)
        row.status, row.last_attempt_at = ElectronicInvoiceStatus.PROCESSING, datetime.now(timezone.utc)
        db.commit()
    with pytest.raises(invoicing.InvoiceInProgressError):
        retry_of(factory, sale_id, fake)
    assert fake.count("/invoices/stamp") == 0
    with factory() as db:                                      # ...that one crashed long ago: reclaimable
        db.get(ElectronicInvoice, record.id).last_attempt_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        db.commit()
    assert retry_of(factory, sale_id, fake).cufe == "cufe-4624"
    assert fake.count("/invoices/stamp") == 1 and fake.count("/invoices") == 1


def test_failed_stamp_leaves_the_draft_retryable_and_never_posts_an_invoice(env):
    from app.services.alegra_client import AlegraServerError
    factory, sale_id = env
    fake = FakeAlegra()
    draft_record(factory, sale_id, fake)
    real_stamp = fake.stamp_invoices

    def failing(ids):
        fake.calls.append(("POST", "/invoices/stamp"))
        raise AlegraServerError("Alegra caído")
    fake.stamp_invoices = failing
    with pytest.raises(invoicing.SaleValidationError):
        retry_of(factory, sale_id, fake)
    record = record_of(factory, sale_id)
    assert record.status == ElectronicInvoiceStatus.ISSUED and record.provider_status == "draft" and not record.cufe
    fake.stamp_invoices = real_stamp
    assert retry_of(factory, sale_id, fake).cufe == "cufe-4624"
    assert fake.count("/invoices/stamp") == 2 and fake.count("/invoices") == 1


def test_retry_of_a_draft_already_stamped_remotely_only_syncs(env):
    factory, sale_id = env
    fake = FakeAlegra()
    draft_record(factory, sale_id, fake)
    fake.invoices[0].update(status="open", stamp={"legalStatus": "STAMPED_AND_ACCEPTED", "cufe": "cufe-remoto"})
    record = retry_of(factory, sale_id, fake)
    assert record.cufe == "cufe-remoto" and record.display_status == "ACCEPTED" and record.error_code is None
    assert fake.count("/invoices/stamp") == 0 and fake.count("/invoices") == 1
