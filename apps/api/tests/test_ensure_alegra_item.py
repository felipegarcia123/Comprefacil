"""On-demand Alegra items at invoicing time ("Punto Red" experience, approved 2026-10-02):
ensure_alegra_item -> LINK or CREATE -> invoice. Fake Alegra only; every remote call logged."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.domain.enums import ElectronicInvoiceStatus, PaymentMethod, SaleStatus
from app.models import (CatalogSyncOperation, ElectronicInvoice, InventoryMovement, PaymentChannel, Product, Sale,
                        SaleItem, SalePayment, User)
from app.services import alegra_catalog_sync as sync
from app.services import electronic_invoicing as invoicing
from app.services.alegra_client import AlegraNetworkError, AlegraValidationError


FINAL_CONTACT = {"id": "1", "name": "CONSUMIDOR FINAL", "status": "active",
                 "identificationObject": {"type": "CC", "number": "222222222222"}}


class FakeClient:
    def __init__(self, alegra):
        self.alegra = alegra

    def get(self, path, params=None):
        self.alegra.calls.append(("GET", path, dict(params or {})))
        if path.startswith("/contacts/"):                          # Consumidor final verification
            return FINAL_CONTACT if path.endswith("/1") else None
        if self.alegra.search_error:                               # item searches / unit checks only
            raise self.alegra.search_error
        if path.startswith("/items/"):
            item_id = path.rsplit("/", 1)[1]
            return next((i for i in self.alegra.items if i["id"] == item_id),
                        {"id": item_id, "inventory": {"unit": "unit"}})        # pre-existing linked item
        ref = (params or {}).get("reference", "")
        return [i for i in self.alegra.items if ref in (i.get("reference") or "")]      # SUBSTRING, like Alegra


class FakeAlegra:
    def __init__(self, items=()):
        self.items = [{"inventory": {"unit": "unit"}, **i} for i in items]
        self.invoices: list[dict] = []
        self.calls: list[tuple] = []
        self.search_error = None
        self.item_mode = "ok"            # ok | timeout_after | timeout_before | reject | no_id
        self.reject_names: set[str] = set()
        self.client = FakeClient(self)

    def posts(self, path):
        return [c for c in self.calls if c[0] == "POST" and c[1] == path]

    def create_item(self, payload):
        self.calls.append(("POST", "/items", payload))
        if self.item_mode == "reject" or payload["name"] in self.reject_names:
            raise AlegraValidationError("El nombre no es válido")
        if self.item_mode == "timeout_before":
            raise AlegraNetworkError("timeout")
        new = {"id": str(9000 + len(self.items)), "reference": payload["reference"], "name": payload["name"],
               "status": "active", "inventory": payload.get("inventory")}
        self.items.append(new)
        if self.item_mode == "timeout_after":
            raise AlegraNetworkError("timeout")
        return {} if self.item_mode == "no_id" else new

    def create_invoice(self, payload):
        self.calls.append(("POST", "/invoices", payload))
        invoice = {"id": str(7000 + len(self.invoices)), "status": "open", "date": payload["date"],
                   "client": payload["client"], "observations": payload["observations"],
                   "stamp": {"legalStatus": "STAMPED_AND_ACCEPTED"},
                   "numberTemplate": {"prefix": "ERG", "number": str(5000 + len(self.invoices))}}
        self.invoices.append({**invoice, "items": payload["items"]})
        return invoice

    def get_invoice(self, provider_invoice_id, fields=None):
        return next(i for i in self.invoices if i["id"] == provider_invoice_id)

    def list_invoices_page(self, *, date, client_id, start=0, limit=30):
        rows = [i for i in self.invoices if i["date"] == date and i["client"]["id"] == client_id]
        return rows[start:start + limit], len(rows)


@pytest.fixture()
def env(monkeypatch, tmp_path):
    from app.core.config import settings
    for key, value in {"alegra_enabled": True, "alegra_email": "a@b.co", "alegra_token": "t",
                       "alegra_number_template_id": "17", "alegra_final_consumer_contact_id": "1",
                       "alegra_api_url": "http://alegra.invalid", "alegra_auto_issue": False,
                       "alegra_manual_issue_enabled": False, "alegra_catalog_create_enabled": True,
                       "alegra_item_manual_bulk": "372"}.items():
        monkeypatch.setattr(settings, key, value)
    engine = create_engine(f"sqlite:///{tmp_path / 'pos.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        user = User(name="Cajero")
        cash = PaymentChannel(name="Efectivo", payment_type=PaymentMethod.CASH, affects_cash=True,
                              alegra_payment_method="CASH")
        p = lambda barcode, name, price, mapped=None: Product(barcode=barcode, name=name, sale_price=Decimal(price),
                                                              unit_cost=Decimal(1), alegra_item_id=mapped)
        products = {"linked": p("7702084057200", "LECHE", "5000", "2832"),
                    "in_alegra": p("7702191523995", "PAN", "4000"),
                    "new": p("4005900036759", "CREMA NUEVA", "5200"),
                    "new2": p("7501031311309", "ARENA NUEVA", "9000"),
                    "nobarcode": p(None, "HUEVO CRIOLLO", "800")}
        db.add_all([user, cash, *products.values()]); db.commit()
        ids = {"user": user.id, "cash": cash.id, **{k: v.id for k, v in products.items()}}
    yield factory, ids, settings


def make_sale(factory, ids, *keys):
    with factory() as db:
        sale = Sale(number=f"V-{uuid4().hex[:8]}", status=SaleStatus.COMPLETED, created_by=ids["user"],
                    completed_at=datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc))
        total = Decimal(0)
        for key in keys:
            product = db.get(Product, ids[key])
            sale.items.append(SaleItem(product_id=product.id, product_name=product.name, quantity=Decimal(1),
                                       unit_price=product.sale_price, unit_cost=product.unit_cost,
                                       line_total=product.sale_price))
            total += product.sale_price
        sale.total = total
        sale.payments.append(SalePayment(payment_channel_id=ids["cash"], amount=total, method=PaymentMethod.CASH,
                                         received_amount=total))
        db.add(sale); db.commit()
        return sale.id


def issue(factory, sale_id, fake):
    with factory() as db:
        return invoicing.issue_invoice(db, db.get(Sale, sale_id), provider=fake)


def retry(factory, sale_id, fake):
    with factory() as db:
        record = db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale_id))
        return invoicing.retry_invoice(db, record, db.get(Sale, sale_id), provider=fake)


def item_id_of(factory, ids, key):
    with factory() as db:
        return db.get(Product, ids[key]).alegra_item_id


# ---- 1. already linked: zero catalog I/O ------------------------------------------------------------------------

def test_linked_product_needs_no_catalog_call(env):
    factory, ids, _ = env
    fake = FakeAlegra()
    record = issue(factory, make_sale(factory, ids, "linked"), fake)
    assert record.status == ElectronicInvoiceStatus.ISSUED
    # Resolving it: zero catalog I/O (no reference search, no POST /items). The only /items call
    # is the unit-of-measure check of the invoice itself (DIAN 3061), one GET of that item.
    assert [c for c in fake.calls if c[1].startswith("/items")] == [("GET", "/items/2832", {})]
    assert fake.invoices[0]["items"][0]["id"] == "2832"


# ---- 2. exists in Alegra: GET -> LINK -> invoice -----------------------------------------------------------------

def test_unmapped_but_in_alegra_is_linked_then_invoiced(env):
    factory, ids, _ = env
    fake = FakeAlegra([{"id": "10", "reference": "7702191523995", "name": "PAN", "status": "active"}])
    record = issue(factory, make_sale(factory, ids, "in_alegra"), fake)
    assert record.status == ElectronicInvoiceStatus.ISSUED
    assert item_id_of(factory, ids, "in_alegra") == "10"
    assert fake.posts("/items") == [] and fake.invoices[0]["items"][0]["id"] == "10"
    with factory() as db:
        assert db.scalar(select(CatalogSyncOperation.action)) == "LINK"


# ---- 3. absent: GET -> POST item -> id saved -> invoice ----------------------------------------------------------

def test_missing_product_is_created_then_invoiced(env):
    factory, ids, _ = env
    fake = FakeAlegra()
    record = issue(factory, make_sale(factory, ids, "new"), fake)
    assert record.status == ElectronicInvoiceStatus.ISSUED
    created = fake.posts("/items")
    assert len(created) == 1 and created[0][2] == {
        "name": "CREMA NUEVA", "reference": "4005900036759", "type": "product",
        "price": [{"idPriceList": "1", "price": 5200.0}], "tax": [], "category": {"id": "5148"},
        "inventory": {"unit": "unit"}}
    new_id = item_id_of(factory, ids, "new")
    # created with its unit -> no extra unit-check GET of the item it just created
    assert ("GET", f"/items/{new_id}", {}) not in fake.calls
    assert new_id and fake.invoices[0]["items"][0]["id"] == new_id
    # searched before posting, and the invoice only after the item
    order = [c[1] if c[0] == "POST" else "GET" for c in fake.calls]
    assert order.index("GET") < order.index("/items") < order.index("/invoices")


# ---- 4. no barcode: stable internal reference --------------------------------------------------------------------

def test_product_without_barcode_uses_stable_internal_reference(env):
    factory, ids, _ = env
    reference = f"POS-{ids['nobarcode'].hex[:16].upper()}"
    with factory() as db:
        product = db.get(Product, ids["nobarcode"])
        assert sync.item_reference(product) == sync.item_reference(product) == reference   # deterministic
    fake = FakeAlegra([{"id": "13", "reference": "90100111710111", "name": "HUEVO CRIOLLO", "status": "active"}])
    record = issue(factory, make_sale(factory, ids, "nobarcode"), fake)
    assert record.status == ElectronicInvoiceStatus.ISSUED
    assert fake.posts("/items")[0][2]["reference"] == reference          # same name in Alegra: never matched
    assert item_id_of(factory, ids, "nobarcode") not in (None, "13")


def test_product_without_barcode_links_to_its_own_reference(env):
    factory, ids, _ = env
    reference = f"POS-{ids['nobarcode'].hex[:16].upper()}"
    fake = FakeAlegra([{"id": "77", "reference": reference, "name": "HUEVO CRIOLLO", "status": "active"}])
    assert issue(factory, make_sale(factory, ids, "nobarcode"), fake).status == ElectronicInvoiceStatus.ISSUED
    assert item_id_of(factory, ids, "nobarcode") == "77" and fake.posts("/items") == []


# ---- 5. same product twice -> at most one creation ---------------------------------------------------------------

def test_same_product_twice_creates_at_most_once(env):
    factory, ids, _ = env
    fake = FakeAlegra()
    with factory() as db:                    # one sale can't hold two rows of a product; the step dedupes anyway
        product = db.get(Product, ids["new"])
        stub = SimpleNamespace(items=[SimpleNamespace(product_id=product.id, product=product)] * 2)
        assert sync.ensure_sale_items(db, stub, fake)["status"] == "READY"
    assert issue(factory, make_sale(factory, ids, "new"), fake).status == ElectronicInvoiceStatus.ISSUED
    assert issue(factory, make_sale(factory, ids, "new"), fake).status == ElectronicInvoiceStatus.ISSUED
    assert len(fake.posts("/items")) == 1 and len(fake.invoices) == 2


# ---- 6. two missing products: both ready before the invoice -------------------------------------------------------

def test_two_missing_products_are_both_prepared_before_the_invoice(env):
    factory, ids, _ = env
    fake = FakeAlegra()
    record = issue(factory, make_sale(factory, ids, "new", "new2"), fake)
    assert record.status == ElectronicInvoiceStatus.ISSUED
    posts = [c[1] for c in fake.calls if c[0] == "POST"]
    assert posts == ["/items", "/items", "/invoices"]
    assert {line["id"] for line in fake.invoices[0]["items"]} == {item_id_of(factory, ids, "new"),
                                                                   item_id_of(factory, ids, "new2")}


# ---- 7. POST /items timed out but the item exists -> reconcile, LINK, no duplicate ------------------------------

def test_item_timeout_after_creation_is_reconciled_not_duplicated(env):
    factory, ids, _ = env
    fake = FakeAlegra(); fake.item_mode = "timeout_after"
    record = issue(factory, make_sale(factory, ids, "new"), fake)
    assert record.status == ElectronicInvoiceStatus.ISSUED
    assert len(fake.posts("/items")) == 1 and len([i for i in fake.items if i["reference"] == "4005900036759"]) == 1
    assert fake.invoices[0]["items"][0]["id"] == item_id_of(factory, ids, "new")


def test_item_outcome_unknown_waits_and_never_reposts_blindly(env):
    factory, ids, _ = env
    fake = FakeAlegra(); fake.item_mode = "timeout_before"           # not created, and we can't know
    sale_id = make_sale(factory, ids, "new")
    record = issue(factory, sale_id, fake)
    assert record.status == ElectronicInvoiceStatus.ERROR and record.error_code == "CATALOG_PENDING"
    assert fake.invoices == [] and len(fake.posts("/items")) == 1
    fake.item_mode = "ok"
    assert retry(factory, sale_id, fake).status == ElectronicInvoiceStatus.ERROR   # within cooldown: no 2nd POST
    assert len(fake.posts("/items")) == 1
    with factory() as db:                                                         # cooldown elapsed
        op = db.scalar(select(CatalogSyncOperation).where(CatalogSyncOperation.status == "UNCERTAIN"))
        op.created_at = datetime.now(timezone.utc) - timedelta(minutes=11); db.commit()
    assert retry(factory, sale_id, fake).status == ElectronicInvoiceStatus.ISSUED  # searched, absent, POST once
    assert len(fake.posts("/items")) == 2 and len([i for i in fake.items if i["reference"] == "4005900036759"]) == 1


# ---- 8. definitive failure: sale untouched, invoice not sent, recoverable ----------------------------------------

def test_item_rejected_keeps_sale_and_leaves_invoice_retryable(env):
    factory, ids, _ = env
    fake = FakeAlegra(); fake.item_mode = "reject"
    sale_id = make_sale(factory, ids, "new")
    with factory() as db:
        before = (db.scalar(select(func.count()).select_from(InventoryMovement)),
                  db.scalar(select(func.count()).select_from(SalePayment)))
    record = issue(factory, sale_id, fake)
    assert record.status == ElectronicInvoiceStatus.ERROR and record.error_code == "CATALOG_PENDING"
    assert "CREMA NUEVA" in record.error_message and fake.invoices == []
    with factory() as db:
        sale = db.get(Sale, sale_id)
        assert sale.status == SaleStatus.COMPLETED and len(sale.payments) == 1
        assert (db.scalar(select(func.count()).select_from(InventoryMovement)),
                db.scalar(select(func.count()).select_from(SalePayment))) == before
    fake.item_mode = "ok"
    assert retry(factory, sale_id, fake).status == ElectronicInvoiceStatus.ISSUED


# ---- 9. retry only touches products still unmapped ---------------------------------------------------------------

def test_retry_does_not_recreate_items_already_linked(env):
    factory, ids, _ = env
    fake = FakeAlegra(); fake.reject_names = {"ARENA NUEVA"}
    sale_id = make_sale(factory, ids, "new", "new2")
    assert issue(factory, sale_id, fake).status == ElectronicInvoiceStatus.ERROR
    first_id = item_id_of(factory, ids, "new")
    assert first_id and item_id_of(factory, ids, "new2") is None and fake.invoices == []
    fake.reject_names = set()
    assert retry(factory, sale_id, fake).status == ElectronicInvoiceStatus.ISSUED
    names = [c[2]["name"] for c in fake.posts("/items")]
    assert names.count("CREMA NUEVA") == 1 and names.count("ARENA NUEVA") == 2
    assert item_id_of(factory, ids, "new") == first_id


# ---- 10. creation flag off: zero remote writes; the flow is simulated up to the POST -----------------------------

def test_flag_off_means_zero_remote_writes(env, monkeypatch):
    factory, ids, settings = env
    monkeypatch.setattr(settings, "alegra_catalog_create_enabled", False)
    fake = FakeAlegra()
    sale_id = make_sale(factory, ids, "linked", "new")
    with factory() as db:
        preview = invoicing.preview_invoice(db, db.get(Sale, sale_id), fake)
    step = next(c for c in preview["catalog"] if c["product_name"] == "CREMA NUEVA")
    assert step["status"] == "READY_TO_CREATE" and step["payload"]["reference"] == "4005900036759"
    assert not preview["ready"] and any("deshabilitada" in r for r in preview["reasons"])
    lines = {line.get("pending_reference"): line["id"] for line in preview["payload"]["items"]}
    assert lines == {None: "2832", "4005900036759": None}
    with pytest.raises(invoicing.SaleValidationError):
        issue(factory, sale_id, fake)
    assert [c for c in fake.calls if c[0] == "POST"] == []
    with factory() as db:
        record = db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale_id))
        assert record.status == ElectronicInvoiceStatus.BLOCKED and record.error_code == "CATALOG"
        assert db.get(Product, ids["new"]).alegra_item_id is None
        assert db.scalar(select(func.count()).select_from(CatalogSyncOperation)) == 0
    monkeypatch.setattr(settings, "alegra_catalog_create_enabled", True)       # turned on later: same record works
    assert retry(factory, sale_id, fake).status == ElectronicInvoiceStatus.ISSUED


def test_preview_with_creation_on_is_ready_and_still_writes_nothing(env):
    factory, ids, _ = env
    fake = FakeAlegra([{"id": "10", "reference": "7702191523995", "name": "PAN", "status": "active"}])
    sale_id = make_sale(factory, ids, "in_alegra", "new")
    with factory() as db:
        preview = invoicing.preview_invoice(db, db.get(Sale, sale_id), fake)
        assert preview["ready"] and preview["reasons"] == []
        assert {c["status"] for c in preview["catalog"]} == {"EXACT_MATCH", "READY_TO_CREATE"}
        assert db.get(Product, ids["in_alegra"]).alegra_item_id is None          # dry run: not even the LINK
    assert [c for c in fake.calls if c[0] == "POST"] == []


def test_search_failure_is_pending_and_sends_nothing(env):
    factory, ids, _ = env
    fake = FakeAlegra(); fake.search_error = AlegraNetworkError("sin red")
    record = issue(factory, make_sale(factory, ids, "new"), fake)
    assert record.status == ElectronicInvoiceStatus.ERROR and record.error_code == "CATALOG_PENDING"
    assert [c for c in fake.calls if c[0] == "POST"] == []


def test_item_held_by_another_product_blocks_instead_of_duplicating(env):
    factory, ids, _ = env
    fake = FakeAlegra([{"id": "2832", "reference": "4005900036759", "name": "OTRA", "status": "active"}])
    with pytest.raises(invoicing.SaleValidationError):
        issue(factory, make_sale(factory, ids, "new"), fake)                  # 2832 already belongs to LECHE
    assert [c for c in fake.calls if c[0] == "POST"] == []


def test_controlled_issue_shows_the_catalog_plan_and_creates_only_on_confirm(env):
    from app.services import controlled_issue
    factory, ids, _ = env
    fake = FakeAlegra()
    fake.preview_invoice = lambda payload: (_ for _ in ()).throw(AssertionError("preview needs real item ids"))
    sale_id = make_sale(factory, ids, "linked", "new")
    with factory() as db:
        report = controlled_issue.run(db, db.get(Sale, sale_id), provider=fake)
    assert report["ok"] and report["step"] == "PREVIEW"
    assert [(c["product_name"], c["status"]) for c in report["catalog"]] == [("CREMA NUEVA", "READY_TO_CREATE")]
    assert "4005900036759" in report["preview_skipped"] and [c for c in fake.calls if c[0] == "POST"] == []
    with factory() as db:
        report = controlled_issue.run(db, db.get(Sale, sale_id), provider=fake, confirm=True,
                                      expected_total=Decimal("10200"))
    assert report["step"] == "ISSUED" and report["record"]["status"] == "ISSUED"
    assert [c[1] for c in fake.calls if c[0] == "POST"] == ["/items", "/invoices"]


# ---- unit of measure (DIAN 3061, verified with ERG4622 / item 2989) ------------------------------------------------

FORBIDDEN_INVENTORY_KEYS = {"unitCost", "initialQuantity", "availableQuantity", "warehouses", "quantity", "cost"}


def _all_keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _all_keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _all_keys(v)}
    return set()


def test_planned_and_posted_item_carry_unit_and_never_quantities_or_costs(env):
    factory, ids, settings = env
    with factory() as db:                                   # flag off (as in production): only the plan
        settings.alegra_catalog_create_enabled = False
        plan = sync.ensure_alegra_item(db, ids["new"], FakeAlegra())
        settings.alegra_catalog_create_enabled = True
    assert plan["status"] == "READY_TO_CREATE" and plan["payload"]["inventory"] == {"unit": "unit"}
    fake = FakeAlegra()
    issue(factory, make_sale(factory, ids, "new", "nobarcode"), fake)
    posted = [c[2] for c in fake.posts("/items")]
    assert len(posted) == 2
    for payload in [plan["payload"], *posted]:
        assert payload["inventory"] == {"unit": "unit"}
        assert not (_all_keys(payload) & FORBIDDEN_INVENTORY_KEYS)
        assert payload["tax"] == [] and payload["category"] == {"id": "5148"}
        assert payload["price"][0]["idPriceList"] == "1"


def test_product_sold_by_kg_is_never_created_with_a_guessed_unit(env):
    factory, ids, _ = env
    with factory() as db:
        db.get(Product, ids["new"]).unit = "kg"; db.commit()
        result = sync.ensure_alegra_item(db, ids["new"], fake := FakeAlegra())
    assert result["status"] == "REVIEW_REQUIRED" and "kg" in result["reason"]
    assert fake.posts("/items") == []


def test_linked_item_without_unit_blocks_the_invoice_and_keeps_the_sale(env):
    factory, ids, _ = env
    fake = FakeAlegra([{"id": "2832", "reference": "7702084057200", "name": "LECHE", "status": "active",
                        "inventory": None}])
    sale_id = make_sale(factory, ids, "linked")
    with pytest.raises(invoicing.SaleValidationError):
        issue(factory, sale_id, fake)
    with factory() as db:
        record = db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale_id))
        assert record.status == ElectronicInvoiceStatus.BLOCKED and record.error_code == "ITEM_UNIT"
        assert "2832" in record.error_message and db.get(Sale, sale_id).status == SaleStatus.COMPLETED
    assert fake.posts("/invoices") == [] and fake.posts("/items") == []


def test_unit_check_unreachable_is_retryable_and_never_posts(env):
    factory, ids, _ = env
    fake = FakeAlegra()
    fake.search_error = AlegraNetworkError("timeout")
    sale_id = make_sale(factory, ids, "linked")
    record = issue(factory, sale_id, fake)
    assert record.status == ElectronicInvoiceStatus.ERROR and record.error_code == "UNIT_CHECK_PENDING"
    assert fake.posts("/invoices") == []
    fake.search_error = None                                 # Alegra back: the retry goes through, once
    assert retry(factory, sale_id, fake).status == ElectronicInvoiceStatus.ISSUED
    assert len(fake.posts("/invoices")) == 1


def test_bulto_generic_item_without_unit_blocks_the_invoice(env):
    # ALEGRA_ITEM_MANUAL_SACK=2988 exists in Alegra with inventory.unit = null (GET 2026-10-02).
    from app.domain.enums import SaleSource
    factory, ids, settings = env
    settings.alegra_item_manual_sack = "2988"
    fake = FakeAlegra([{"id": "2988", "reference": None, "name": "BULTO", "status": "active", "inventory": None}])
    with factory() as db:
        sale = Sale(number="V-BULTO001", status=SaleStatus.COMPLETED, created_by=ids["user"],
                    source=SaleSource.MANUAL_SACK, total=Decimal(90000),
                    completed_at=datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc))
        sale.items.append(SaleItem(product_id=None, product_name="Bulto concentrado", quantity=Decimal(1),
                                   unit_price=Decimal(90000), unit_cost=Decimal(0), line_total=Decimal(90000)))
        sale.payments.append(SalePayment(payment_channel_id=ids["cash"], amount=Decimal(90000),
                                         method=PaymentMethod.CASH, received_amount=Decimal(90000)))
        db.add(sale); db.commit()
        sale_id = sale.id
    with factory() as db:
        plan = invoicing.invoice_plan(db, db.get(Sale, sale_id), fake)
    assert plan["ready"] is False and any("2988" in r and "unidad de medida" in r for r in plan["reasons"])
    with pytest.raises(invoicing.SaleValidationError):
        issue(factory, sale_id, fake)
    with factory() as db:
        record = db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale_id))
        assert record.error_code == "ITEM_UNIT" and db.get(Sale, sale_id).status == SaleStatus.COMPLETED
    assert fake.posts("/invoices") == []
