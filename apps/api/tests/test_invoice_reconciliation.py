"""Anti-duplicate reconciliation, pre-flight and item-mapping preview.

Alegra has no idempotency key: after an ambiguous failure (timeout/5xx) the next
attempt must search Alegra for the sale's marker before any new POST. A fake
Alegra keeps its own invoice list so "created but answer lost" is reproducible."""
import threading
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.domain.enums import ElectronicInvoiceStatus, PaymentMethod, SaleSource, SaleStatus
from app.models import ElectronicInvoice, PaymentChannel, Product, Sale, SaleItem, SalePayment, User
from app.services import electronic_invoicing as invoicing
from app.services.alegra_client import AlegraNetworkError, AlegraServerError, AlegraValidationError
from app.services.alegra_item_mapping import apply_auto_matches, build_mapping_preview, is_valid_gtin


FINAL_CONTACT = {"id": "1", "name": "CONSUMIDOR FINAL", "status": "active",
                 "identificationObject": {"type": "CC", "number": "222222222222"}}


class _NoItemsClient:
    """GET /items?reference= of the on-demand catalog step: the item doesn't exist in Alegra.
    GET /items/{id} (unit check before invoicing): every linked item has unit "unit"."""
    def get(self, path, params=None):
        if path.startswith("/contacts/"):                          # Consumidor final verification
            return FINAL_CONTACT if path.endswith("/1") else None
        if path.startswith("/items/"):
            return {"id": path.rsplit("/", 1)[1], "inventory": {"unit": "unit"}}
        return []


class FakeAlegra:
    def __init__(self):
        self.remote: list[dict] = []   # what Alegra actually holds
        self.create_calls = 0
        self.mode = "ok"               # ok | timeout_before | timeout_after | reject
        self.list_error = None
        self.gate: threading.Event | None = None
        self.entered = threading.Event()
        self.client = _NoItemsClient()

    def create_invoice(self, payload):
        self.create_calls += 1
        self.entered.set()
        if self.gate:
            self.gate.wait(5)
        if self.mode == "timeout_before":
            raise AlegraNetworkError("Tiempo de espera agotado consultando Alegra (/invoices)")
        if self.mode == "reject":
            raise AlegraValidationError("El ítem no existe")
        invoice = {"id": str(7000 + len(self.remote)), "status": "open", "date": payload["date"],
                   "stamp": {"legalStatus": "STAMPED_AND_ACCEPTED", "cufe": "cufe-" + str(len(self.remote))},
                   "pdf": "https://alegra.test/f.pdf", "xml": "https://alegra.test/f.xml",
                   "client": payload["client"], "observations": payload["observations"],
                   "numberTemplate": {"prefix": "ERG", "number": str(5000 + len(self.remote)),
                                      "fullNumber": f"ERG{5000 + len(self.remote)}"}}
        self.remote.append(invoice)
        if self.mode == "timeout_after":
            raise AlegraNetworkError("Tiempo de espera agotado consultando Alegra (/invoices)")
        return invoice

    def list_invoices_page(self, *, date, client_id, start=0, limit=30):
        if self.list_error:
            raise self.list_error
        rows = [i for i in self.remote if i["date"] == date and i["client"]["id"] == client_id]
        return rows[start:start + limit], len(rows)

    def get_invoice(self, provider_invoice_id, fields=None):
        return next(i for i in self.remote if i["id"] == provider_invoice_id)

    def preview_invoice(self, payload):
        self.previews = getattr(self, "previews", 0) + 1
        return {"pdfUrl": "https://alegra.test/draft.pdf"}


@pytest.fixture()
def env(monkeypatch, tmp_path):
    from app.core.config import settings
    for key, value in {"alegra_enabled": True, "alegra_email": "a@b.co", "alegra_token": "t",
                       "alegra_number_template_id": "17", "alegra_final_consumer_contact_id": "1",
                       "alegra_api_url": "http://alegra.invalid", "alegra_auto_issue": False,
                       "alegra_manual_issue_enabled": False, "alegra_item_manual_bulk": "372"}.items():
        monkeypatch.setattr(settings, key, value)
    # File-backed SQLite so two sessions/threads really are independent connections.
    engine = create_engine(f"sqlite:///{tmp_path / 'pos.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        user = User(name="Cajero")
        cash = PaymentChannel(name="Efectivo", payment_type=PaymentMethod.CASH, affects_cash=True,
                              alegra_payment_method="CASH")
        nequi = PaymentChannel(name="Nequi", payment_type=PaymentMethod.TRANSFER, affects_cash=False)  # unmapped
        mapped = Product(barcode="7702084057200", name="Leche", sale_price=Decimal("5000"),
                         unit_cost=Decimal("3000"), alegra_item_id="2832")
        unmapped = Product(barcode="7702191523995", name="Pan", sale_price=Decimal("4000"), unit_cost=Decimal("2000"))
        db.add_all([user, cash, nequi, mapped, unmapped]); db.commit()
        ids = {"user": user.id, "cash": cash.id, "nequi": nequi.id, "mapped": mapped.id, "unmapped": unmapped.id}
    yield factory, ids


def make_sale(factory, ids, lines=(("mapped", "2"),), channel="cash", manual=False,
              completed_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)):
    with factory() as db:
        sale = Sale(number=f"V-{uuid4().hex[:8]}", status=SaleStatus.COMPLETED, created_by=ids["user"],
                    completed_at=completed_at)
        total = Decimal("0")
        for key, qty in lines:
            product = db.get(Product, ids[key])
            line = product.sale_price * Decimal(qty)
            sale.items.append(SaleItem(product_id=product.id, product_name=product.name, quantity=Decimal(qty),
                                       unit_price=product.sale_price, unit_cost=product.unit_cost, line_total=line))
            total += line
        if manual:
            sale.items.append(SaleItem(product_id=None, product_name="Comida a granel", quantity=Decimal("1"),
                                       unit_price=Decimal("20000"), unit_cost=Decimal("0"), line_total=Decimal("20000")))
            total += Decimal("20000")
        sale.total = total
        sale.payments.append(SalePayment(payment_channel_id=ids[channel], amount=total, method=PaymentMethod.CASH,
                                         received_amount=total if channel == "cash" else None))
        db.add(sale); db.commit()
        return sale.id


def issue(factory, sale_id, fake):
    with factory() as db:
        return invoicing.issue_invoice(db, db.get(Sale, sale_id), provider=fake)


def retry(factory, sale_id, fake):
    with factory() as db:
        record = db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale_id))
        return invoicing.retry_invoice(db, record, db.get(Sale, sale_id), provider=fake)


def remote_for(fake, sale_id):
    return [i for i in fake.remote if f"POS sale_id={sale_id}" in i["observations"]]


# ---- the five required scenarios ------------------------------------------------------------------------

def test_timeout_before_creation_reconciles_empty_then_posts_once(env):
    factory, ids = env
    fake = FakeAlegra(); fake.mode = "timeout_before"
    sale_id = make_sale(factory, ids)
    first = issue(factory, sale_id, fake)
    assert first.status == ElectronicInvoiceStatus.ERROR and first.needs_reconciliation is True
    fake.mode = "ok"
    second = retry(factory, sale_id, fake)
    assert second.status == ElectronicInvoiceStatus.ISSUED and second.needs_reconciliation is False
    assert fake.create_calls == 2 and len(remote_for(fake, sale_id)) == 1


def test_timeout_after_creation_links_existing_invoice_without_reposting(env):
    factory, ids = env
    fake = FakeAlegra(); fake.mode = "timeout_after"
    sale_id = make_sale(factory, ids)
    first = issue(factory, sale_id, fake)
    assert first.status == ElectronicInvoiceStatus.ERROR and first.needs_reconciliation is True
    fake.mode = "ok"
    second = retry(factory, sale_id, fake)
    assert second.status == ElectronicInvoiceStatus.ISSUED
    assert second.provider_invoice_id == remote_for(fake, sale_id)[0]["id"]
    assert second.provider_number.startswith("ERG")
    assert fake.create_calls == 1 and len(remote_for(fake, sale_id)) == 1  # no second POST, no duplicate


def test_retry_with_existing_remote_invoice_from_crashed_attempt(env):
    """A worker died mid-POST: row stuck PROCESSING (stale) but Alegra has the invoice."""
    factory, ids = env
    fake = FakeAlegra(); fake.mode = "timeout_after"
    sale_id = make_sale(factory, ids)
    issue(factory, sale_id, fake)
    with factory() as db:
        record = db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale_id))
        record.status = ElectronicInvoiceStatus.PROCESSING
        record.last_attempt_at = datetime(2026, 1, 1, tzinfo=timezone.utc)  # long ago = stale
        db.commit()
    fake.mode = "ok"
    record = retry(factory, sale_id, fake)
    assert record.status == ElectronicInvoiceStatus.ISSUED and fake.create_calls == 1


def test_retry_without_remote_invoice_posts_again(env):
    """A definitive rejection (400) proves nothing was created: retry POSTs without searching."""
    factory, ids = env
    fake = FakeAlegra(); fake.mode = "reject"
    sale_id = make_sale(factory, ids)
    first = issue(factory, sale_id, fake)
    assert first.status == ElectronicInvoiceStatus.ERROR and first.needs_reconciliation is False
    fake.mode = "ok"
    fake.list_error = AssertionError("no debe consultar Alegra: el 400 prueba que no se creó")
    second = retry(factory, sale_id, fake)
    assert second.status == ElectronicInvoiceStatus.ISSUED and len(remote_for(fake, sale_id)) == 1


def test_two_concurrent_retries_post_at_most_once(env):
    factory, ids = env
    fake = FakeAlegra(); fake.mode = "timeout_before"
    sale_id = make_sale(factory, ids)
    issue(factory, sale_id, fake)                     # ERROR, needs reconciliation
    fake.mode = "ok"; fake.gate = threading.Event(); fake.entered.clear()
    results = {}

    def first():
        try:
            results["a"] = retry(factory, sale_id, fake).status
        except Exception as exc:  # noqa: BLE001
            results["a"] = exc

    worker = threading.Thread(target=first)
    worker.start()
    assert fake.entered.wait(5)                       # A owns the claim and is inside the POST
    with pytest.raises(invoicing.InvoiceInProgressError):
        retry(factory, sale_id, fake)                 # B loses the atomic claim
    fake.gate.set(); worker.join(5)
    assert results["a"] == ElectronicInvoiceStatus.ISSUED
    assert fake.create_calls == 2                     # 1 failed first attempt + 1 retry; B never POSTed
    assert len(remote_for(fake, sale_id)) == 1
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ElectronicInvoice)) == 1


# ---- reconciliation edge cases ---------------------------------------------------------------------------

def test_unverifiable_search_never_reposts(env):
    factory, ids = env
    fake = FakeAlegra(); fake.mode = "timeout_after"
    sale_id = make_sale(factory, ids)
    issue(factory, sale_id, fake)
    fake.mode = "ok"; fake.list_error = AlegraServerError("Alegra caído")
    record = retry(factory, sale_id, fake)
    assert record.status == ElectronicInvoiceStatus.ERROR and record.error_code == "RECONCILE_FAILED"
    assert record.needs_reconciliation is True and fake.create_calls == 1


def test_marker_does_not_match_other_sales_or_manual_invoices(env):
    factory, ids = env
    fake = FakeAlegra(); fake.mode = "timeout_before"
    sale_id = make_sale(factory, ids)
    issue(factory, sale_id, fake)
    with factory() as db:
        record = db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale_id))
        payload = record.provider_payload
    fake.remote += [  # same date and client: an invoice typed by hand in Alegra, and another POS sale
        {"id": "1", "date": payload["date"], "client": payload["client"], "observations": "", "status": "open"},
        {"id": "2", "date": payload["date"], "client": payload["client"], "status": "open",
         "observations": f"POS sale_id={uuid4()} number=V-X"}]
    fake.mode = "ok"
    record = retry(factory, sale_id, fake)
    assert record.status == ElectronicInvoiceStatus.ISSUED and record.provider_invoice_id not in ("1", "2")


def test_invoice_date_is_colombia_local_date(env):
    factory, ids = env
    sale_id = make_sale(factory, ids, completed_at=datetime(2026, 9, 28, 2, 30, tzinfo=timezone.utc))
    with factory() as db:
        assert invoicing.invoice_date(db.get(Sale, sale_id)) == "2026-09-27"  # 21:30 in Bogotá


# ---- pre-flight -------------------------------------------------------------------------------------------

def test_preflight_ready_for_mapped_cash_sale(env):
    factory, ids = env
    sale_id = make_sale(factory, ids)
    with factory() as db:
        result = invoicing.preflight_sale(db, db.get(Sale, sale_id), provider=FakeAlegra())
    assert result["status"] == "READY" and result["lines"] == [] and result["requirements"] == []


def test_preflight_blocked_lists_every_line_and_requirement_without_touching_sale(env):
    factory, ids = env
    sale_id = make_sale(factory, ids, lines=(("mapped", "1"), ("unmapped", "1")), channel="nequi", manual=True)
    with factory() as db:
        sale = db.get(Sale, sale_id)
        before = (sale.status, sale.total, len(sale.items), len(sale.payments))
        result = invoicing.preflight_sale(db, sale, provider=FakeAlegra())
        assert result["status"] == "BLOCKED"
        assert sorted((l["code"], l["product_name"]) for l in result["lines"]) == [
            ("MANUAL_LINE", "Comida a granel"), ("UNMAPPED_PRODUCT", "Pan")]
        assert [r["code"] for r in result["requirements"]] == ["PAYMENT_METHOD"]
        assert "Nequi" in result["requirements"][0]["message"] and "no tiene medio de pago" in result["requirements"][0]["message"]
        db.refresh(sale)
        assert (sale.status, sale.total, len(sale.items), len(sale.payments)) == before


def test_blocked_attempt_records_blocked_and_sends_nothing(env):
    factory, ids = env
    fake = FakeAlegra()
    sale_id = make_sale(factory, ids, lines=(("unmapped", "1"),))
    with pytest.raises(invoicing.SaleValidationError):
        issue(factory, sale_id, fake)
    with factory() as db:
        record = db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale_id))
        # not in Alegra and automatic creation off -> BLOCKED at the catalog step, nothing sent
        assert record.status == ElectronicInvoiceStatus.BLOCKED and record.provider_payload is None
        assert record.error_code == "CATALOG" and "deshabilitada" in record.error_message
        assert db.get(Sale, sale_id).status == SaleStatus.COMPLETED
    assert fake.create_calls == 0
    with factory() as db:  # fix the mapping -> the same record can be retried
        db.get(Product, ids["unmapped"]).alegra_item_id = "2929"; db.commit()
    assert retry(factory, sale_id, fake).status == ElectronicInvoiceStatus.ISSUED


# ---- item mapping preview ------------------------------------------------------------------------------

def test_gtin_check_digit():
    assert is_valid_gtin("7702084057200") and is_valid_gtin("012345678905")
    assert not is_valid_gtin("7702084057201") and not is_valid_gtin("1234") and not is_valid_gtin("COL-ABR-004")


def test_mapping_preview_statuses_and_no_writes(env):
    factory, ids = env
    with factory() as db:
        db.add_all([
            Product(barcode="012345678905", name="Twin UPC", sale_price=1, unit_cost=1),        # UPC-A -> EAN-13 twin
            Product(barcode="7501031311309", name="Dup ref", sale_price=1, unit_cost=1),        # two Alegra items
            Product(barcode="7707211440016", name="Claimed A", sale_price=1, unit_cost=1),      # item claimed twice...
            Product(barcode="07707211440016", name="Claimed B", sale_price=1, unit_cost=1),     # ...via GTIN-14 variant
            Product(barcode="4005900036759", name="Crema Nivea Soft 200 ml", sale_price=1, unit_cost=1),
            Product(barcode="COL-ABR-004", name="Interno", sale_price=1, unit_cost=1),
            Product(barcode="7702354956837", name="Inactivo", sale_price=1, unit_cost=1)])
        db.commit()
        items = [
            {"id": "2832", "reference": "7702084057200", "name": "LECHE", "status": "active"},
            {"id": "10", "reference": "0012345678905", "name": "TWIN", "status": "active"},
            {"id": "11", "reference": "7501031311309", "name": "DUP 1", "status": "active"},
            {"id": "12", "reference": "7501031311309", "name": "DUP 2", "status": "active"},
            {"id": "13", "reference": "7707211440016", "name": "CLAIMED", "status": "active"},
            {"id": "14", "reference": "999", "name": "CREMA NIVEA SOFT X 200 ML", "status": "active"},
            {"id": "15", "reference": "7702354956837", "name": "OLD", "status": "inactive"}]
        before = {p.barcode: p.alegra_item_id for p in db.scalars(select(Product))}
        preview = build_mapping_preview(db, items)
        after = {p.barcode: p.alegra_item_id for p in db.scalars(select(Product))}
    assert before == after  # read-only
    status = {r["barcode"]: (r["match_status"], r["alegra_item_id"], r["confidence"]) for r in preview["rows"]}
    assert status["7702084057200"] == ("AUTO_MATCH", "2832", "HIGH")
    assert status["012345678905"] == ("AUTO_MATCH", "10", "MEDIUM")
    assert status["7501031311309"][0] == "AMBIGUOUS"
    assert status["7707211440016"][0] == "AUTO_MATCH"          # the GTIN-14 spelling is not a UPC/EAN twin
    assert status["07707211440016"][0] == "NO_MATCH"
    assert status["COL-ABR-004"][0] == "INVALID_BARCODE"
    assert status["7702354956837"][0] == "AMBIGUOUS"          # only match is inactive in Alegra
    nivea = next(r for r in preview["rows"] if r["barcode"] == "4005900036759")
    assert nivea["match_status"] == "NO_MATCH" and nivea["alegra_item_id"] is None
    assert nivea["suggestion_item_id"] == "14" and nivea["confidence"] == "LOW"   # name = suggestion only
    assert preview["summary"]["active"]["AUTO_MATCH"] == 3


def test_apply_auto_matches_is_idempotent_and_never_overwrites(env):
    factory, ids = env
    with factory() as db:
        db.add_all([Product(barcode="7702354956837", name="Libre", sale_price=1, unit_cost=1),
                    Product(barcode="7501031311309", name="Con otro id", sale_price=1, unit_cost=1,
                            alegra_item_id="999"),
                    Product(barcode="4005900036759", name="Twin", sale_price=1, unit_cost=1)])
        db.commit()
        by_code = {p.barcode: str(p.id) for p in db.scalars(select(Product))}

    def row(code, item, status="AUTO_MATCH", confidence="HIGH", barcode=None):
        return {"product_id": by_code[code], "barcode": barcode or code, "product_name": code,
                "alegra_item_id": item, "match_status": status, "confidence": confidence}
    rows = [row("7702354956837", "15"), row("7702084057200", "2832"),              # free + already same (fixture)
            row("7501031311309", "11"),                                             # has a different id
            row("7702191523995", "15"),                                             # item already taken above
            row("4005900036759", "14", confidence="MEDIUM"),                        # not HIGH -> ignored
            row("4005900036759", "14", status="NO_MATCH")]
    with factory() as db:
        first = {r["barcode"]: r["status"] for r in apply_auto_matches(db, rows)}
    assert first == {"7702354956837": "APPLIED", "7702084057200": "SKIP_ALREADY_MAPPED",
                     "7501031311309": "CONFLICT", "7702191523995": "CONFLICT"}
    with factory() as db:
        second = {r["barcode"]: r["status"] for r in apply_auto_matches(db, rows)}
        mapped = {p.barcode: p.alegra_item_id for p in db.scalars(select(Product))}
    assert second["7702354956837"] == "SKIP_ALREADY_MAPPED"                        # idempotent
    assert mapped["7501031311309"] == "999" and mapped["4005900036759"] is None      # never overwritten / ignored



# ---- granel / bulto (manual sales) ------------------------------------------------------------------------

def manual_sale(factory, ids, source, channel="cash"):
    with factory() as db:
        sale = Sale(number=f"VM-{uuid4().hex[:6]}", status=SaleStatus.COMPLETED, created_by=ids["user"],
                    source=source, total=Decimal("18000"), completed_at=datetime(2026, 9, 28, 15, tzinfo=timezone.utc))
        sale.items.append(SaleItem(product_id=None, product_name="Comida a granel gato 1,5 kg", quantity=Decimal("1"),
                                   unit_price=Decimal("18000"), unit_cost=Decimal("0"), line_total=Decimal("18000")))
        sale.payments.append(SalePayment(payment_channel_id=ids[channel], amount=Decimal("18000"),
                                         method=PaymentMethod.CASH, received_amount=Decimal("18000")))
        db.add(sale); db.commit()
        return sale.id


def test_granel_uses_item_372_with_real_line_price_and_description(env):
    factory, ids = env
    sale_id = manual_sale(factory, ids, SaleSource.MANUAL_BULK)
    fake = FakeAlegra()
    with factory() as db:
        sale = db.get(Sale, sale_id)
        assert invoicing.preflight_sale(db, sale, provider=fake)["status"] == "READY"
        payload = invoicing.build_alegra_invoice_payload(sale, "1", "17")
    assert payload["items"] == [{"id": "372", "price": 18000.0, "quantity": 1.0, "tax": [],
                                 "description": "Comida a granel gato 1,5 kg"}]


def test_bulto_stays_blocked_until_its_item_exists(env, monkeypatch):
    factory, ids = env
    from app.core.config import settings
    monkeypatch.setattr(settings, "alegra_item_manual_sack", "")   # independent of the host .env
    sale_id = manual_sale(factory, ids, SaleSource.MANUAL_SACK)
    with factory() as db:
        result = invoicing.preflight_sale(db, db.get(Sale, sale_id), provider=FakeAlegra())
    assert result["status"] == "BLOCKED" and result["lines"][0]["code"] == "MANUAL_LINE"
    assert "BULTO" in result["lines"][0]["reason"]
    from app.core.config import settings
    monkeypatch.setattr(settings, "alegra_item_manual_sack", "9999")   # once the BULTO item is created
    with factory() as db:
        assert invoicing.preflight_sale(db, db.get(Sale, sale_id), provider=FakeAlegra())["status"] == "READY"


def test_debit_plus_credit_card_is_mixed_and_blocked(env):
    factory, ids = env
    with factory() as db:
        card = PaymentChannel(name="Datáfono", payment_type=PaymentMethod.CARD, affects_cash=False)
        card2 = PaymentChannel(name="Datáfono 2", payment_type=PaymentMethod.CARD, affects_cash=False)
        db.add_all([card, card2]); db.commit()
        sale = Sale(number="V-MIX", status=SaleStatus.COMPLETED, created_by=ids["user"], total=Decimal("10000"),
                    completed_at=datetime.now(timezone.utc))
        product = db.get(Product, ids["mapped"])
        sale.items.append(SaleItem(product_id=product.id, product_name=product.name, quantity=Decimal("2"),
                                   unit_price=Decimal("5000"), unit_cost=Decimal("0"), line_total=Decimal("10000")))
        sale.payments += [SalePayment(payment_channel_id=card.id, amount=Decimal("5000"), card_type="DEBIT"),
                          SalePayment(payment_channel_id=card2.id, amount=Decimal("5000"), card_type="CREDIT")]
        db.add(sale); db.commit()
        result = invoicing.preflight_sale(db, db.get(Sale, sale.id), provider=FakeAlegra())
    assert result["status"] == "BLOCKED" and [r["code"] for r in result["requirements"]] == ["PAYMENT_METHOD_MIXED"]
    assert "DEBIT" not in result["requirements"][0]["message"]   # no technical codes for the cashier


# ---- legacy PENDING held for review ----------------------------------------------------------------------

def test_held_record_is_never_sent_by_retry_or_auto_issue(env):
    factory, ids = env
    sale_id = make_sale(factory, ids)
    fake = FakeAlegra()
    with factory() as db:
        record = invoicing.create_electronic_invoice_record(db, db.get(Sale, sale_id))
        held = invoicing.hold_for_review(db, record, "Intento previo al cutover; revisar si ya se facturó a mano")
        assert held.status == ElectronicInvoiceStatus.BLOCKED and held.error_code == invoicing.REVIEW_HOLD_CODE
    with pytest.raises(invoicing.InvoiceHeldForReviewError):
        retry(factory, sale_id, fake)
    invoicing.auto_issue_for_sale(sale_id, session_factory=factory, provider=fake)
    assert fake.create_calls == 0
    with factory() as db:
        record = db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale_id))
        assert record.error_code == invoicing.REVIEW_HOLD_CODE and record.status == ElectronicInvoiceStatus.BLOCKED


def test_hold_refused_when_record_already_attempted(env):
    factory, ids = env
    fake = FakeAlegra(); fake.mode = "timeout_after"
    sale_id = make_sale(factory, ids)
    issue(factory, sale_id, fake)
    with factory() as db:
        record = db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale_id))
        with pytest.raises(invoicing.SaleValidationError):
            invoicing.hold_for_review(db, record, "x")


# ---- controlled first emission ---------------------------------------------------------------------------

def test_controlled_issue_preview_only_never_posts(env):
    from app.services import controlled_issue
    factory, ids = env
    fake = FakeAlegra()
    sale_id = make_sale(factory, ids)
    with factory() as db:
        report = controlled_issue.run(db, db.get(Sale, sale_id), provider=fake)
    assert report["ok"] and report["step"] == "PREVIEW" and report["preview_pdf_url"]
    assert report["payload_total"] == report["pos_total"] == "10000.00"
    assert fake.create_calls == 0


def test_controlled_issue_requires_matching_total_then_verifies_single_marker(env):
    from app.services import controlled_issue
    factory, ids = env
    fake = FakeAlegra()
    sale_id = make_sale(factory, ids)
    with factory() as db:
        refused = controlled_issue.run(db, db.get(Sale, sale_id), provider=fake, confirm=True,
                                       expected_total=Decimal("9999"))
    assert not refused["ok"] and refused["step"] == "CONFIRM" and fake.create_calls == 0
    with factory() as db:
        report = controlled_issue.run(db, db.get(Sale, sale_id), provider=fake, confirm=True,
                                      expected_total=Decimal("10000"))
        record = db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale_id))
    assert report["ok"] and report["step"] == "ISSUED" and fake.create_calls == 1
    verification = report["verification"]
    assert verification["observations_has_marker"] and verification["invoices_with_marker"] == 1
    assert verification["cufe"] and verification["legal_status"] and verification["pdf"] and verification["xml"]
    assert record.cufe == verification["cufe"]
    assert record.pdf_url and record.xml_url   # persisted, not only checked
    with factory() as db:   # a second controlled run must refuse: already invoiced
        again = controlled_issue.run(db, db.get(Sale, sale_id), provider=fake, confirm=True,
                                     expected_total=Decimal("10000"))
    assert not again["ok"] and fake.create_calls == 1


def test_controlled_issue_stops_if_alegra_already_has_the_marker(env):
    from app.services import controlled_issue
    factory, ids = env
    fake = FakeAlegra()
    sale_id = make_sale(factory, ids)
    with factory() as db:
        sale = db.get(Sale, sale_id)
        payload = invoicing.build_alegra_invoice_payload(sale, "1", "17")
    fake.remote.append({"id": "1", "date": payload["date"], "client": payload["client"], "status": "open",
                        "observations": payload["observations"]})
    with factory() as db:
        report = controlled_issue.run(db, db.get(Sale, sale_id), provider=fake)
    assert not report["ok"] and any("ya tiene" in p for p in report["problems"]) and fake.create_calls == 0


def test_item_scan_refuses_an_inconsistent_catalog():
    """A shifted page (one item twice, another missing) must not yield a preview."""
    from app.services.alegra_client import AlegraError
    from app.services.alegra_item_mapping import fetch_all_items

    class Shifting:
        def list_items_page(self, start=0, limit=30):
            ids = list(range(start, start + 30)) if start == 0 else [29] + list(range(start + 1, start + 30))
            return [{"id": str(i), "reference": str(i)} for i in ids][: max(0, 45 - start)], 45

    with pytest.raises(AlegraError):
        fetch_all_items(Shifting(), pause=0)


# ---- display semantics + accidental draft ---------------------------------------------------------------

def test_display_status_never_calls_a_draft_issued(env):
    factory, ids = env
    sale_id = make_sale(factory, ids)
    with factory() as db:
        record = invoicing.create_electronic_invoice_record(db, db.get(Sale, sale_id))
        record.status = ElectronicInvoiceStatus.ISSUED; record.provider_invoice_id = "4615"
        record.provider_status = "draft"; db.commit()
        assert record.display_status == "DRAFT"
        record.provider_status = "open"; record.cufe = "abc"
        assert record.display_status == "SENT"
        record.legal_status = "STAMPED_AND_ACCEPTED_WITH_OBSERVATIONS"
        assert record.display_status == "ACCEPTED"
        record.legal_status = "STAMPED_AND_REJECTED"
        assert record.display_status == "DIAN_REJECTED"


def test_deleted_accidental_draft_is_parked_forever(env):
    factory, ids = env
    sale_id = make_sale(factory, ids)
    fake = FakeAlegra()
    with factory() as db:
        record = invoicing.create_electronic_invoice_record(db, db.get(Sale, sale_id))
        record.status = ElectronicInvoiceStatus.ISSUED; record.provider_invoice_id = "4615"
        record.provider_number = "ERG4613"; record.provider_status = "draft"; db.commit()
        parked = invoicing.mark_remote_draft_deleted(db, record, {"id": "4615", "number": "ERG4613",
                                                                   "deleted_at": "2026-09-28"})
        assert parked.status == ElectronicInvoiceStatus.BLOCKED and parked.display_status == "HELD"
        assert parked.provider_invoice_id is None and parked.provider_response["deleted_remote_draft"]["id"] == "4615"
    with pytest.raises(invoicing.InvoiceHeldForReviewError):
        retry(factory, sale_id, fake)
    invoicing.auto_issue_for_sale(sale_id, session_factory=factory, provider=fake)
    from app.services import controlled_issue
    with factory() as db:
        report = controlled_issue.run(db, db.get(Sale, sale_id), provider=fake, confirm=True,
                                      expected_total=Decimal("10000"))
    assert not report["ok"] and fake.create_calls == 0
