"""Customers (Alegra-linked), Consumidor Final default, automatic electronic
invoicing after checkout, and the payment flows the Venta panel relies on.
Alegra is always a fake here — no test touches the network."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base, get_db
from app.domain.enums import ElectronicInvoiceStatus, InventoryMovementType, PaymentMethod, SaleStatus
from app.main import app
from app.models import (CashMovement, CashSession, Customer, ElectronicInvoice, InventoryMovement, Payment,
                        PaymentChannel, Product, Sale, User)
from app.services import customers as customers_service
from app.services import electronic_invoicing as invoicing
from app.services.alegra_client import AlegraNetworkError, AlegraServerError, AlegraValidationError
from app.services.inventory import current_stock
from app.services.pos import add_item, create_sale

API = "/api/v1"
# The cashier ticked "Factura electrónica" (migration 0024): only then is a sale invoiced.
FC = {"electronic_invoice": {"recipient": "FINAL_CONSUMER"}}


def to_customer(customer_id):
    return {"electronic_invoice": {"recipient": "CUSTOMER", "customer_id": customer_id}}


def contact(id_, number, name="Juan Pérez", kind="CC", email="juan@example.com", status="active", **extra):
    first, _, last = name.partition(" ")
    return {"id": id_, "name": name, "identificationObject": {"type": kind, "number": number, "dv": None},
            "nameObject": {"firstName": first, "secondName": None, "lastName": last or None, "secondLastName": None},
            "kindOfPerson": "PERSON_ENTITY", "email": email, "phonePrimary": "3001234567", "mobile": None,
            "address": {"address": "Calle 1", "city": None}, "status": status, "type": ["client"], **extra}


FINAL = contact("1", "222222222222", name="CONSUMIDOR FINAL", email=None)


class _NoItemsClient:
    """GET /items?reference= of the on-demand catalog step: the item doesn't exist in Alegra.
    GET /items/{id} (unit check before invoicing): every linked item has unit "unit".
    GET /contacts/{id} (Consumidor final verification): the fake's own contacts."""
    def __init__(self, alegra=None):
        self.alegra = alegra

    def get(self, path, params=None):
        if path.startswith("/contacts/") and self.alegra is not None:
            contact_id = path.rsplit("/", 1)[1]
            return next((c for c in self.alegra.contacts if c["id"] == contact_id), None)
        if path.startswith("/items/"):
            return {"id": path.rsplit("/", 1)[1], "inventory": {"unit": "unit"}}
        return []


class FakeAlegra:
    """Contract-compatible stand-in for AlegraProvider (contacts + invoices)."""
    def __init__(self, contacts=None, page_size=30):
        self.contacts = list(contacts or [])
        self.page_size = page_size
        self.created_contacts, self.updated_contacts, self.invoices = [], [], []
        self.create_contact_error = self.create_invoice_error = None
        self.page_calls = 0
        self.next_id = 500
        self.client = _NoItemsClient(self)

    def list_contacts_page(self, start=0, limit=30, contact_type="client"):
        self.page_calls += 1
        size = min(limit, self.page_size)
        return self.contacts[start:start + size], len(self.contacts)

    def find_contacts_by_identification(self, number):
        return [c for c in self.contacts if c["identificationObject"]["number"] == number]

    def create_contact(self, payload):
        if self.create_contact_error:
            raise self.create_contact_error
        self.created_contacts.append(payload)
        self.next_id += 1
        return {"id": str(self.next_id), **payload}

    def update_contact(self, contact_id, payload):
        self.updated_contacts.append((contact_id, payload))
        return {"id": contact_id, **payload}

    def create_invoice(self, payload):
        if self.create_invoice_error:
            raise self.create_invoice_error
        self.invoices.append(payload)
        return {"id": str(9000 + len(self.invoices)), "status": "open",
                "numberTemplate": {"prefix": "FE", "number": str(len(self.invoices))}}

    def list_invoices_page(self, *, date, client_id, start=0, limit=30):
        rows = [{"id": str(9001 + i), "status": "open", "observations": inv.get("observations"),
                 "numberTemplate": {"prefix": "FE", "number": str(i + 1)}}
                for i, inv in enumerate(self.invoices) if inv["date"] == date and inv["client"]["id"] == client_id]
        return rows[start:start + limit], len(rows)

    def get_invoice(self, provider_invoice_id, fields=None):
        return {"id": provider_invoice_id, "status": "open"}


@pytest.fixture()
def ctx(monkeypatch):
    from app.core.config import settings
    for key, value in {"alegra_enabled": True, "alegra_email": "shop@example.com", "alegra_token": "t",
                       "alegra_number_template_id": "17", "alegra_auto_issue": False,
                       "alegra_auto_issue_stamp": False, "alegra_final_consumer_contact_id": "",
                       "alegra_api_url": "http://alegra.invalid", "alegra_manual_issue_enabled": False}.items():
        monkeypatch.setattr(settings, key, value)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    fake = FakeAlegra([FINAL, contact("4", "1370000001")])
    # Every code path that would build a real AlegraProvider gets the fake instead.
    monkeypatch.setattr(customers_service, "AlegraProvider", lambda: fake)
    monkeypatch.setattr(invoicing, "AlegraProvider", lambda: fake)
    import app.core.db as core_db
    monkeypatch.setattr(core_db, "SessionLocal", factory)
    with factory() as db:
        user = User(name="Cajero")
        channels = {c.name: c for c in [
            PaymentChannel(name="Efectivo", payment_type=PaymentMethod.CASH, affects_cash=True, display_order=10,
                           alegra_payment_method="CASH"),
            PaymentChannel(name="Nequi", payment_type=PaymentMethod.TRANSFER, affects_cash=False, display_order=20,
                           alegra_payment_method="CREDIT_TRANSFER"),
            PaymentChannel(name="Datáfono", payment_type=PaymentMethod.CARD, affects_cash=False, display_order=50)]}
        product = Product(barcode="7701", name="Arroz", sale_price=Decimal("10000"), unit_cost=Decimal("6000"),
                          alegra_item_id="77")
        db.add_all([user, product, *channels.values()]); db.flush()
        db.add(InventoryMovement(product_id=product.id, movement_type=InventoryMovementType.PURCHASE,
                                 quantity=Decimal("50"), unit_cost=Decimal("6000"), user_id=user.id))
        db.add(CashSession(opening_amount=Decimal("0"), opened_by=user.id,
                           opened_at=datetime.now(timezone.utc) - timedelta(seconds=5)))
        db.commit()

        def override_db(): yield db
        app.dependency_overrides[get_db] = override_db
        try:
            yield db, TestClient(app), fake, product, channels, settings
        finally:
            app.dependency_overrides.clear()


def open_sale(db, product, qty="1"):
    return add_item(db, create_sale(db).id, product.barcode, Decimal(qty))


NEW_PERSON = {"identification_type": "CC", "identification_number": "1.020.304.050",
              "first_name": "Ana", "last_name": "Gómez", "email": "ana@example.com", "phone": "3110000000"}


# ---- normalization ------------------------------------------------------------------------------------

def test_nit_check_digit_and_normalization():
    assert customers_service.nit_check_digit("900123456") == "8"
    assert customers_service.split_nit("900.123.456-8") == ("900123456", "8")
    assert customers_service.normalize_number(" 1.020.304.050 ") == "1020304050"
    data = customers_service.prepare_new_customer("nit", "900.123.456-8", None, "Comercial ABC SAS",
                                                  None, None, None, None, None)
    assert data["kind_of_person"] == "LEGAL_ENTITY" and data["dv"] == "8"
    with pytest.raises(customers_service.CustomerError):
        customers_service.prepare_new_customer("NIT", "900123456-3", None, "X", None, None, None, None, None)


# ---- create ------------------------------------------------------------------------------------------------

def test_create_customer_posts_to_alegra_then_stores_linked(ctx):
    db, client, fake, *_ = ctx
    response = client.post(f"{API}/customers", json=NEW_PERSON)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "CREATED"
    assert body["customer"]["identification_number"] == "1020304050"
    assert body["customer"]["alegra_contact_id"] == "501"
    assert fake.created_contacts == [{
        "identificationObject": {"type": "CC", "number": "1020304050"}, "kindOfPerson": "PERSON_ENTITY",
        "type": ["client"], "nameObject": {"firstName": "Ana", "lastName": "Gómez"},
        "email": "ana@example.com", "phonePrimary": "3110000000"}]


def test_create_does_not_duplicate_document(ctx):
    db, client, fake, *_ = ctx
    first = client.post(f"{API}/customers", json=NEW_PERSON).json()
    second = client.post(f"{API}/customers", json={**NEW_PERSON, "identification_number": "1020304050"}).json()
    assert second["outcome"] == "EXISTING_LOCAL" and second["customer"]["id"] == first["customer"]["id"]
    assert len(fake.created_contacts) == 1
    assert db.scalar(select(func.count()).select_from(Customer)) == 1


def test_create_links_existing_alegra_contact_instead_of_posting(ctx):
    db, client, fake, *_ = ctx
    response = client.post(f"{API}/customers", json={**NEW_PERSON, "identification_number": "1370000001"})
    assert response.json()["outcome"] == "LINKED_REMOTE"
    assert response.json()["customer"]["alegra_contact_id"] == "4"
    assert fake.created_contacts == []


def test_alegra_rejection_stores_nothing(ctx):
    db, client, fake, *_ = ctx
    fake.create_contact_error = AlegraValidationError("El email no es válido")
    response = client.post(f"{API}/customers", json=NEW_PERSON)
    assert response.status_code == 422 and "El email no es válido" in response.json()["detail"]
    assert db.scalar(select(func.count()).select_from(Customer)) == 0


def test_alegra_unreachable_stores_nothing(ctx):
    db, client, fake, *_ = ctx
    fake.create_contact_error = AlegraNetworkError("timeout")
    assert client.post(f"{API}/customers", json=NEW_PERSON).status_code == 502
    assert db.scalar(select(func.count()).select_from(Customer)) == 0


def test_final_consumer_document_cannot_be_created_as_customer(ctx):
    db, client, *_ = ctx
    response = client.post(f"{API}/customers", json={**NEW_PERSON, "identification_number": "222222222222"})
    assert response.status_code == 422


def test_alegra_contact_id_is_unique(ctx):
    db, *_ = ctx
    db.add(Customer(alegra_contact_id="9", identification_type="CC", identification_number="1", name="A"))
    db.commit()
    db.add(Customer(alegra_contact_id="9", identification_type="CC", identification_number="2", name="B"))
    with pytest.raises(Exception):
        db.commit()
    db.rollback()


# ---- search -----------------------------------------------------------------------------------------------

def test_search_by_document_name_phone_email_and_hides_final_consumer(ctx):
    db, client, fake, *_ = ctx
    client.post(f"{API}/customers/sync")
    client.post(f"{API}/customers", json=NEW_PERSON)
    names = lambda q: [c["name"] for c in client.get(f"{API}/customers", params={"q": q}).json()]
    assert names("1.020.304") == ["Ana Gómez"]
    assert names("gómez") == ["Ana Gómez"]
    assert names("311000") == ["Ana Gómez"]
    assert names("ana@example") == ["Ana Gómez"]
    assert "CONSUMIDOR FINAL" not in names("")
    assert names("222222222222") == []


# ---- sync ---------------------------------------------------------------------------------------------------

def test_sync_is_paginated_and_idempotent(ctx):
    db, client, fake, *_ = ctx
    fake.contacts += [contact(str(10 + i), f"80000{i:04d}", name=f"Cliente {i}") for i in range(65)]
    first = client.post(f"{API}/customers/sync").json()
    assert first["created"] == 67 and first["errors"] == 0 and first["total_remote"] == 67
    assert fake.page_calls == 3  # 30 + 30 + 7
    second = client.post(f"{API}/customers/sync").json()
    assert (second["created"], second["updated"], second["unchanged"]) == (0, 0, 67)
    assert db.scalar(select(func.count()).select_from(Customer)) == 67
    final = db.scalar(select(Customer).where(Customer.is_final_consumer.is_(True)))
    assert final.alegra_contact_id == "1"


def test_sync_updates_changed_fields_and_never_deletes(ctx):
    db, client, fake, *_ = ctx
    client.post(f"{API}/customers/sync")
    fake.contacts[1] = contact("4", "1370000001", email="nuevo@example.com")
    fake.contacts.pop(0)  # CONSUMIDOR FINAL disappears from Alegra
    result = client.post(f"{API}/customers/sync").json()
    assert result["updated"] == 1 and result["created"] == 0
    assert db.scalar(select(Customer).where(Customer.alegra_contact_id == "4")).email == "nuevo@example.com"
    assert db.scalar(select(func.count()).select_from(Customer)) == 2


def test_sync_links_local_customer_by_document_and_reports_bad_contacts(ctx):
    db, client, fake, *_ = ctx
    fake.contacts.append({"id": "77", "name": "Sin documento", "identificationObject": {}, "status": "active"})
    fake.contacts.append(contact("78", "1370000001", name="Duplicado en Alegra"))
    result = client.post(f"{API}/customers/sync").json()
    assert result["created"] == 2 and result["errors"] == 2
    assert len(result["error_messages"]) == 2


def test_sync_status_reports_last_sync_without_secrets(ctx):
    db, client, *_ = ctx
    assert client.get(f"{API}/customers/sync-status").json()["last_synced_at"] is None
    client.post(f"{API}/customers/sync")
    status = client.get(f"{API}/customers/sync-status").json()
    assert status["last_synced_at"] and status["final_consumer_resolved"] is True and status["total_customers"] == 1
    assert "token" not in str(status).lower()


# ---- Sale <-> Customer and invoicing ------------------------------------------------------------------------

def test_final_consumer_resolved_automatically_and_cached(ctx):
    db, client, fake, product, channels, _ = ctx
    sale = open_sale(db, product)
    client.post(f"{API}/sales/{sale.id}/checkout", json={**FC, "channel_id": str(channels["Efectivo"].id), "received_amount": "10000"})
    record = invoicing.issue_invoice(db, db.get(Sale, sale.id), invoicing.resolve_contact_for_sale(db, db.get(Sale, sale.id))[0], provider=fake)
    assert record.status == ElectronicInvoiceStatus.ISSUED
    assert fake.invoices[0]["client"] == {"id": "1"}
    assert db.scalar(select(Customer).where(Customer.is_final_consumer.is_(True))) is not None  # cached locally


def test_assign_customer_to_open_sale_and_checkout_bills_that_contact(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    monkeypatch.setattr(settings, "alegra_auto_issue", True)
    customer = client.post(f"{API}/customers", json=NEW_PERSON).json()["customer"]
    sale = open_sale(db, product)
    assigned = client.put(f"{API}/sales/{sale.id}/customer", json={"customer_id": customer["id"]})
    assert assigned.status_code == 200 and assigned.json()["customer"]["name"] == "Ana Gómez"
    response = client.post(f"{API}/sales/{sale.id}/checkout", json={**to_customer(customer["id"]), "channel_id": str(channels["Efectivo"].id), "received_amount": "10000"})
    assert response.status_code == 200 and response.json()["payments"][0]["channel_name"] == "Efectivo"
    record = db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale.id))
    db.refresh(record)
    assert record.status == ElectronicInvoiceStatus.ISSUED
    assert record.alegra_contact_id == customer["alegra_contact_id"]
    assert str(record.customer_id) == customer["id"]
    assert record.customer_snapshot["identification_number"] == "1020304050"
    assert fake.invoices[0]["client"] == {"id": customer["alegra_contact_id"]}
    assert "stamp" not in fake.invoices[0]  # stamp stays opt-in (ALEGRA_AUTO_ISSUE_STAMP)


def test_snapshot_survives_customer_edits(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    monkeypatch.setattr(settings, "alegra_auto_issue", True)
    customer = client.post(f"{API}/customers", json=NEW_PERSON).json()["customer"]
    sale = open_sale(db, product)
    client.put(f"{API}/sales/{sale.id}/customer", json={"customer_id": customer["id"]})
    client.post(f"{API}/sales/{sale.id}/checkout", json={**to_customer(customer["id"]), "channel_id": str(channels["Efectivo"].id), "received_amount": "10000"})
    client.put(f"{API}/customers/{customer['id']}", json={"email": "otro@example.com"})
    record = db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale.id))
    db.refresh(record)
    assert record.customer_snapshot["email"] == "ana@example.com"


def test_checkout_without_customer_bills_final_consumer(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    monkeypatch.setattr(settings, "alegra_auto_issue", True)
    sale = open_sale(db, product)
    client.post(f"{API}/sales/{sale.id}/checkout", json={**FC, "channel_id": str(channels["Efectivo"].id), "received_amount": "10000"})
    record = db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale.id))
    db.refresh(record)
    assert record.status == ElectronicInvoiceStatus.ISSUED and record.customer_id is None
    assert record.customer_snapshot["final_consumer"] is True
    assert fake.invoices[0]["client"] == {"id": "1"}


def test_inactive_or_unknown_customer_cannot_be_assigned(ctx):
    db, client, fake, product, *_ = ctx
    customer = client.post(f"{API}/customers", json=NEW_PERSON).json()["customer"]
    client.patch(f"{API}/customers/{customer['id']}/active", json={"active": False})
    sale = open_sale(db, product)
    assert client.put(f"{API}/sales/{sale.id}/customer", json={"customer_id": customer["id"]}).status_code == 409
    client.post(f"{API}/customers/sync")
    final = db.scalar(select(Customer).where(Customer.is_final_consumer.is_(True)))
    assert client.put(f"{API}/sales/{sale.id}/customer", json={"customer_id": str(final.id)}).status_code == 404


def test_alegra_failure_does_not_revert_sale_and_can_be_retried(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    monkeypatch.setattr(settings, "alegra_auto_issue", True)
    fake.create_invoice_error = AlegraServerError("Alegra caído")
    sale = open_sale(db, product, "2")
    response = client.post(f"{API}/sales/{sale.id}/checkout",
                           json={**FC, "channel_id": str(channels["Efectivo"].id), "received_amount": "20000"})
    assert response.status_code == 200 and response.json()["status"] == "COMPLETED"
    db.expire_all()
    assert db.get(Sale, sale.id).status == SaleStatus.COMPLETED
    assert current_stock(db, product.id) == Decimal("48")
    assert db.scalar(select(func.sum(CashMovement.amount))) == Decimal("20000")
    assert db.scalar(select(func.count()).select_from(Payment)) == 1
    record = db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale.id))
    assert record.status == ElectronicInvoiceStatus.ERROR and record.error_code == "AlegraServerError"

    fake.create_invoice_error = None
    retried = client.post(f"{API}/electronic-invoices/{record.id}/retry")
    assert retried.status_code == 200 and retried.json()["status"] == "ISSUED"
    assert db.scalar(select(func.count()).select_from(ElectronicInvoice)) == 1
    assert len(fake.invoices) == 1


def test_unmapped_product_is_reported_as_retryable_error_not_invented(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    monkeypatch.setattr(settings, "alegra_auto_issue", True)
    unmapped = Product(barcode="7702", name="Sin mapear", sale_price=Decimal("5000"), unit_cost=Decimal("1"))
    db.add(unmapped); db.flush()
    db.add(InventoryMovement(product_id=unmapped.id, movement_type=InventoryMovementType.PURCHASE,
                             quantity=Decimal("5"), unit_cost=Decimal("1"), user_id=db.scalar(select(User.id))))
    db.commit()
    sale = open_sale(db, unmapped)
    client.post(f"{API}/sales/{sale.id}/checkout", json={**FC, "channel_id": str(channels["Nequi"].id)})
    record = db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale.id))
    db.refresh(record)
    # never invented: searched in Alegra, absent, creation off -> BLOCKED, retryable later
    assert record.status == ElectronicInvoiceStatus.BLOCKED and record.error_code == "CATALOG"
    assert "Sin mapear" in record.error_message and fake.invoices == []
    assert db.get(Sale, sale.id).status == SaleStatus.COMPLETED


def test_auto_issue_is_idempotent(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    monkeypatch.setattr(settings, "alegra_auto_issue", True)
    sale = open_sale(db, product)
    client.post(f"{API}/sales/{sale.id}/checkout", json={**FC, "channel_id": str(channels["Efectivo"].id), "received_amount": "10000"})
    import app.core.db as core_db
    invoicing.auto_issue_for_sale(sale.id, session_factory=core_db.SessionLocal, provider=fake)
    invoicing.auto_issue_for_sale(sale.id, session_factory=core_db.SessionLocal, provider=fake)
    assert len(fake.invoices) == 1
    assert db.scalar(select(func.count()).select_from(ElectronicInvoice)) == 1


def test_auto_issue_off_creates_no_invoice(ctx):
    db, client, fake, product, channels, _ = ctx
    sale = open_sale(db, product)
    client.post(f"{API}/sales/{sale.id}/checkout", json={"channel_id": str(channels["Nequi"].id)})
    assert db.scalar(select(func.count()).select_from(ElectronicInvoice)) == 0 and fake.invoices == []


def test_preview_never_posts(ctx):
    db, client, fake, product, channels, _ = ctx
    sale = open_sale(db, product)
    client.post(f"{API}/sales/{sale.id}/checkout", json={"channel_id": str(channels["Efectivo"].id), "received_amount": "10000"})
    preview = client.get(f"{API}/electronic-invoices/sale/{sale.id}/preview").json()
    assert preview["ready"] is True and preview["payload"]["client"] == {"id": "1"}
    assert preview["payload"]["numberTemplate"] == {"id": "17"}
    assert fake.invoices == [] and db.scalar(select(func.count()).select_from(ElectronicInvoice)) == 0


# ---- payments the Venta panel relies on (unchanged backend rules) -------------------------------------------

def test_cash_payment_with_change(ctx):
    db, client, fake, product, channels, _ = ctx
    sale = open_sale(db, product)
    body = client.post(f"{API}/sales/{sale.id}/checkout",
                       json={"channel_id": str(channels["Efectivo"].id), "received_amount": "20000"}).json()
    assert body["payments"][0]["change_amount"] == "10000.00"


def test_electronic_umbrella_records_the_exact_channel(ctx):
    db, client, fake, product, channels, _ = ctx
    sale = open_sale(db, product)
    body = client.post(f"{API}/sales/{sale.id}/checkout", json={"channel_id": str(channels["Nequi"].id)}).json()
    assert [(p["channel_name"], p["affects_cash"]) for p in body["payments"]] == [("Nequi", False)]
    assert db.scalar(select(func.count()).select_from(CashMovement)) == 0


def test_split_payment_still_works_with_customer(ctx):
    db, client, fake, product, channels, _ = ctx
    customer = client.post(f"{API}/customers", json=NEW_PERSON).json()["customer"]
    sale = open_sale(db, product)
    client.put(f"{API}/sales/{sale.id}/customer", json={"customer_id": customer["id"]})
    body = client.post(f"{API}/sales/{sale.id}/checkout", json={"payments": [
        {"channel_id": str(channels["Efectivo"].id), "amount": "4000", "received_amount": "4000"},
        {"channel_id": str(channels["Nequi"].id), "amount": "6000"}]}).json()
    assert body["status"] == "COMPLETED" and [p["channel_name"] for p in body["payments"]] == ["Efectivo", "Nequi"]
    assert body["customer"]["id"] == customer["id"]


def test_card_channel_without_debit_credit_is_blocked(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    monkeypatch.setattr(settings, "alegra_auto_issue", True)
    sale = open_sale(db, product)
    assert client.post(f"{API}/sales/{sale.id}/checkout", json={**FC, "channel_id": str(channels["Datáfono"].id)}).status_code == 200
    record = db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale.id))
    db.refresh(record)
    assert record.status == ElectronicInvoiceStatus.BLOCKED and "Débito o Crédito" in record.error_message
    assert fake.invoices == [] and db.get(Sale, sale.id).status == SaleStatus.COMPLETED
    preflight = client.get(f"{API}/electronic-invoices/sale/{sale.id}/preflight").json()
    assert preflight["status"] == "BLOCKED"
    assert [r["code"] for r in preflight["requirements"]] == ["PAYMENT_METHOD"]


@pytest.mark.parametrize("channel,card_type,expected", [
    ("Nequi", None, "CREDIT_TRANSFER"), ("Datáfono", "DEBIT", "DEBIT_CARD"), ("Datáfono", "CREDIT", "CREDIT_CARD"),
    ("Efectivo", None, "CASH")])
def test_payment_method_mapping_reaches_the_payload(ctx, monkeypatch, channel, card_type, expected):
    db, client, fake, product, channels, settings = ctx
    monkeypatch.setattr(settings, "alegra_auto_issue", True)
    sale = open_sale(db, product)
    body = {**FC, "channel_id": str(channels[channel].id)}
    if channel == "Efectivo": body["received_amount"] = "10000"
    if card_type: body["card_type"] = card_type
    response = client.post(f"{API}/sales/{sale.id}/checkout", json=body)
    assert response.status_code == 200 and response.json()["payments"][0]["card_type"] == card_type
    assert fake.invoices[-1]["paymentForm"] == "CASH" and fake.invoices[-1]["paymentMethod"] == expected


def test_card_type_rejected_on_non_card_channel(ctx):
    db, client, fake, product, channels, _ = ctx
    sale = open_sale(db, product)
    response = client.post(f"{API}/sales/{sale.id}/checkout",
                           json={"channel_id": str(channels["Nequi"].id), "card_type": "DEBIT"})
    assert response.status_code == 422
    assert db.get(Sale, sale.id).status == SaleStatus.OPEN


def test_split_with_different_methods_is_blocked_same_method_is_ready(ctx):
    db, client, fake, product, channels, _ = ctx
    sale = open_sale(db, product)
    client.post(f"{API}/sales/{sale.id}/checkout", json={"payments": [
        {"channel_id": str(channels["Efectivo"].id), "amount": "4000", "received_amount": "4000"},
        {"channel_id": str(channels["Nequi"].id), "amount": "6000"}]})
    preflight = client.get(f"{API}/electronic-invoices/sale/{sale.id}/preflight").json()
    assert preflight["status"] == "BLOCKED"
    assert preflight["requirements"][0]["code"] == "PAYMENT_METHOD_MIXED"
    assert preflight["requirements"][0]["message"] == ("Esta venta usó varios medios de pago y necesita revisión "
                                                       "antes de facturarse.")
    assert db.get(Sale, sale.id).status == SaleStatus.COMPLETED   # the sale is never touched


def test_channel_mapping_is_configurable_and_not_wiped_by_old_clients(ctx):
    db, client, fake, product, channels, _ = ctx
    nequi = channels["Nequi"]
    base = {"name": "Nequi", "payment_type": "TRANSFER", "display_order": 20}
    assert client.put(f"{API}/payment-channels/{nequi.id}", json=base).json()["alegra_payment_method"] == "CREDIT_TRANSFER"
    changed = client.put(f"{API}/payment-channels/{nequi.id}", json={**base, "alegra_payment_method": "ACH_CREDIT"})
    assert changed.json()["alegra_payment_method"] == "ACH_CREDIT"
    assert client.put(f"{API}/payment-channels/{nequi.id}", json={**base, "alegra_payment_method": "INVENTADO"}).status_code == 422


# ---- no web issue path outside auto-issue / controlled script ------------------------------------------

def test_web_issue_endpoint_is_disabled_by_default(ctx):
    db, client, fake, product, channels, _ = ctx
    sale = open_sale(db, product)
    client.post(f"{API}/sales/{sale.id}/checkout", json={"channel_id": str(channels["Efectivo"].id), "received_amount": "10000"})
    response = client.post(f"{API}/electronic-invoices/sale/{sale.id}/issue", json={})
    assert response.status_code == 403 and fake.invoices == []
    assert db.scalar(select(func.count()).select_from(ElectronicInvoice)) == 0


def test_web_retry_is_disabled_before_cutover(ctx):
    db, client, fake, product, channels, settings = ctx
    sale = open_sale(db, product)
    client.post(f"{API}/sales/{sale.id}/checkout", json={"channel_id": str(channels["Efectivo"].id), "received_amount": "10000"})
    record = ElectronicInvoice(sale_id=sale.id, status=ElectronicInvoiceStatus.ERROR)
    db.add(record); db.commit()
    assert client.post(f"{API}/electronic-invoices/{record.id}/retry").status_code == 403   # auto-issue off
    assert fake.invoices == []


def test_administrative_override_is_explicit(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    monkeypatch.setattr(settings, "alegra_manual_issue_enabled", True)
    sale = open_sale(db, product)
    client.post(f"{API}/sales/{sale.id}/checkout", json={**FC, "channel_id": str(channels["Efectivo"].id), "received_amount": "10000"})
    assert client.post(f"{API}/electronic-invoices/sale/{sale.id}/issue", json={}).status_code == 200


# ---- COBRAR -> automatic electronic invoice (the flow validated with controlled_issue) ----------------------------

STAMPED = {"status": "open", "numberTemplate": {"prefix": "ERG", "number": "4700", "fullNumber": "ERG4700"},
           "stamp": {"legalStatus": "STAMPED_AND_ACCEPTED", "cufe": "cufe-xyz"},
           "pdf": "https://alegra.invalid/f.pdf", "xml": "https://alegra.invalid/f.xml"}


def auto_on(monkeypatch, settings, stamp=True):
    monkeypatch.setattr(settings, "alegra_auto_issue", True)
    monkeypatch.setattr(settings, "alegra_auto_issue_stamp", stamp)


def invoice_of(db, sale_id):
    db.expire_all()
    return db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale_id))


def test_cobrar_completes_the_sale_then_issues_stamps_and_syncs(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    auto_on(monkeypatch, settings)
    fake.get_invoice = lambda provider_invoice_id, fields=None: {"id": provider_invoice_id, **STAMPED}
    sale = open_sale(db, product)
    response = client.post(f"{API}/sales/{sale.id}/checkout",
                           json={**FC, "channel_id": str(channels["Efectivo"].id), "received_amount": "10000"})
    assert response.status_code == 200 and response.json()["status"] == "COMPLETED"
    record = invoice_of(db, sale.id)
    assert fake.invoices[0]["stamp"] == {"generateStamp": True}                  # AUTO_ISSUE_STAMP -> DIAN
    assert fake.invoices[0]["observations"] == f"POS sale_id={sale.id} number={sale.number}"
    # the post-issue GET persisted what the cashier and the panel show
    assert record.status == ElectronicInvoiceStatus.ISSUED and record.display_status == "ACCEPTED"
    assert record.cufe == "cufe-xyz" and record.pdf_url.endswith(".pdf") and record.xml_url.endswith(".xml")
    assert record.legal_status == "STAMPED_AND_ACCEPTED" and len(fake.invoices) == 1


def test_post_issue_sync_failure_keeps_the_invoice_issued_and_never_reissues(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    auto_on(monkeypatch, settings)

    def down(provider_invoice_id, fields=None):
        raise AlegraNetworkError("sin red")
    fake.get_invoice = down
    sale = open_sale(db, product)
    client.post(f"{API}/sales/{sale.id}/checkout", json={**FC, "channel_id": str(channels["Efectivo"].id),
                                                        "received_amount": "10000"})
    record = invoice_of(db, sale.id)
    assert record.status == ElectronicInvoiceStatus.ISSUED and record.provider_invoice_id == "9001"
    assert db.get(Sale, sale.id).status == SaleStatus.COMPLETED and len(fake.invoices) == 1
    fake.get_invoice = lambda provider_invoice_id, fields=None: {"id": provider_invoice_id, **STAMPED}
    synced = client.post(f"{API}/electronic-invoices/{record.id}/sync")         # later: sync, not re-issue
    assert synced.status_code == 200 and synced.json()["cufe"] == "cufe-xyz" and len(fake.invoices) == 1


def test_total_mismatch_blocks_before_any_post_and_keeps_the_sale(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    sale = open_sale(db, product)
    client.post(f"{API}/sales/{sale.id}/checkout", json={**FC, "channel_id": str(channels["Efectivo"].id),
                                                        "received_amount": "10000"})    # auto issue still off
    db.get(Sale, sale.id).total = Decimal("9999"); db.commit()                     # inconsistent data
    import app.core.db as core_db
    invoicing.auto_issue_for_sale(sale.id, session_factory=core_db.SessionLocal, provider=fake)
    record = invoice_of(db, sale.id)
    assert record.status == ElectronicInvoiceStatus.BLOCKED and record.error_code == "TOTAL_MISMATCH"
    assert fake.invoices == [] and db.get(Sale, sale.id).status == SaleStatus.COMPLETED
    assert current_stock(db, product.id) == Decimal("49")


def test_retry_uses_the_same_dian_setting_as_the_automatic_issue(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    auto_on(monkeypatch, settings)
    fake.create_invoice_error = AlegraServerError("Alegra caído")
    sale = open_sale(db, product)
    client.post(f"{API}/sales/{sale.id}/checkout", json={**FC, "channel_id": str(channels["Efectivo"].id),
                                                        "received_amount": "10000"})
    fake.create_invoice_error = None
    record = invoice_of(db, sale.id)
    assert client.post(f"{API}/electronic-invoices/{record.id}/retry").json()["status"] == "ISSUED"
    assert fake.invoices[-1]["stamp"] == {"generateStamp": True} and len(fake.invoices) == 1


def test_a_lost_background_run_leaves_a_visible_pending_that_can_be_retried(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    auto_on(monkeypatch, settings)
    monkeypatch.setattr(invoicing, "auto_issue_for_sale", lambda *a, **k: None)    # e.g. the API restarted
    sale = open_sale(db, product)
    client.post(f"{API}/sales/{sale.id}/checkout", json={**FC, "channel_id": str(channels["Efectivo"].id),
                                                        "received_amount": "10000"})
    record = invoice_of(db, sale.id)
    assert record.status == ElectronicInvoiceStatus.PENDING and fake.invoices == []
    from app.services.invoicing_ops import retryable
    assert retryable(record) is True
    assert client.post(f"{API}/electronic-invoices/{record.id}/retry").json()["status"] == "ISSUED"
    assert len(fake.invoices) == 1


def manual_category(db):
    from app.models import OperationalCategory
    db.add(OperationalCategory(code="MASCOTAS_SIN_CODIGO", name="Mascotas sin código / Bultos")); db.commit()


@pytest.mark.parametrize("source,channel,item_id", [("MANUAL_SACK", "Efectivo", "2988"),
                                                    ("MANUAL_BULK", "Nequi", "372")])
def test_manual_granel_and_bulto_sales_enter_the_same_automatic_flow(ctx, monkeypatch, source, channel, item_id):
    db, client, fake, product, channels, settings = ctx
    manual_category(db)
    auto_on(monkeypatch, settings)
    monkeypatch.setattr(settings, "alegra_item_manual_bulk", "372")
    monkeypatch.setattr(settings, "alegra_item_manual_sack", "2988")
    movements_before = db.scalar(select(func.count()).select_from(InventoryMovement))
    body = {"source": source, "description": "Concentrado perro", "amount": "45000",
            "channel_id": str(channels[channel].id), "idempotency_key": f"manual-{source}-001", **FC}
    response = client.post(f"{API}/sales/manual", json=body)
    assert response.status_code == 201 and response.json()["status"] == "COMPLETED"
    sale_id = UUID(response.json()["id"])
    assert client.post(f"{API}/sales/manual", json=body).json()["id"] == str(sale_id)   # idempotent request
    record = invoice_of(db, sale_id)
    assert record.status == ElectronicInvoiceStatus.ISSUED and len(fake.invoices) == 1
    line = fake.invoices[0]["items"][0]
    assert line["id"] == item_id and line["price"] == 45000.0 and line["description"] == "Concentrado perro"
    # generic item: no stock in Alegra and no inventory movement in the POS
    assert db.scalar(select(func.count()).select_from(InventoryMovement)) == movements_before


def test_manual_sale_with_auto_issue_off_creates_no_invoice(ctx):
    db, client, fake, product, channels, _ = ctx
    manual_category(db)
    response = client.post(f"{API}/sales/manual", json={"source": "MANUAL_SACK", "description": "Bulto",
                                                        "amount": "90000", "channel_id": str(channels["Nequi"].id),
                                                        "idempotency_key": "manual-off-0001"})
    assert response.status_code == 201
    assert db.scalar(select(func.count()).select_from(ElectronicInvoice)) == 0 and fake.invoices == []


# ---- Consumidor final: always Alegra's EXISTING contact, verified to be 222222222222 -------------------------------

def charge(client, sale, channels, invoice=FC):
    return client.post(f"{API}/sales/{sale.id}/checkout",
                       json={**invoice, "channel_id": str(channels["Efectivo"].id), "received_amount": "10000"})


def gets_of(fake, monkeypatch):
    calls = []
    real = fake.client.get

    def spy(path, params=None):
        calls.append(path)
        return real(path, params)
    monkeypatch.setattr(fake.client, "get", spy)
    return calls


def test_sale_without_customer_bills_the_verified_final_consumer(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    auto_on(monkeypatch, settings)
    calls = gets_of(fake, monkeypatch)
    for _ in range(2):                                       # 1st: found by identification; 2nd: synced row
        sale = open_sale(db, product)
        assert charge(client, sale, channels).json()["status"] == "COMPLETED"
        record = invoice_of(db, sale.id)
        assert record.status == ElectronicInvoiceStatus.ISSUED and record.customer_id is None
        assert record.customer_snapshot["final_consumer"] is True
        assert record.customer_snapshot["identification_number"] == "222222222222"
    assert [inv["client"] for inv in fake.invoices] == [{"id": "1"}, {"id": "1"}]
    assert "/contacts/1" in calls                            # the 2nd sale re-verified the synced id
    assert fake.created_contacts == []                       # never creates a Consumidor final


def test_configured_and_verified_final_consumer_is_ready(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    monkeypatch.setattr(settings, "alegra_final_consumer_contact_id", "1")
    sale = open_sale(db, product)
    charge(client, sale, channels)                           # auto issue off: preview only
    preview = client.get(f"{API}/electronic-invoices/sale/{sale.id}/preview").json()
    assert preview["ready"] is True and preview["payload"]["client"] == {"id": "1"}
    assert preview["customer"]["identification_number"] == "222222222222"
    assert fake.invoices == [] and fake.created_contacts == []


def test_configured_contact_that_is_not_222222222222_blocks_and_never_falls_back(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    auto_on(monkeypatch, settings)
    monkeypatch.setattr(settings, "alegra_final_consumer_contact_id", "4")      # a real person's contact
    sale = open_sale(db, product)
    assert charge(client, sale, channels).json()["status"] == "COMPLETED"
    record = invoice_of(db, sale.id)
    assert record.status == ElectronicInvoiceStatus.BLOCKED and "1370000001" in record.error_message
    assert fake.invoices == [] and fake.created_contacts == []                  # 0 POST, not even to id 1
    assert db.get(Sale, sale.id).status == SaleStatus.COMPLETED and current_stock(db, product.id) == Decimal("49")


def test_synced_row_whose_alegra_contact_changed_blocks(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    auto_on(monkeypatch, settings)
    db.add(Customer(alegra_contact_id="1", identification_type="CC", identification_number="222222222222",
                    kind_of_person="PERSON_ENTITY", name="CONSUMIDOR FINAL", is_final_consumer=True)); db.commit()
    fake.contacts[0] = contact("1", "999999999", name="Otra persona")             # edited in Alegra since the sync
    sale = open_sale(db, product)
    charge(client, sale, channels)
    record = invoice_of(db, sale.id)
    assert record.status == ElectronicInvoiceStatus.BLOCKED and "no es Consumidor final" in record.error_message
    assert fake.invoices == [] and fake.created_contacts == []


def test_unverifiable_final_consumer_never_guesses_creates_or_posts(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    auto_on(monkeypatch, settings)
    monkeypatch.setattr(settings, "alegra_final_consumer_contact_id", "1")

    def down(path, params=None):
        raise AlegraNetworkError("sin red")
    real_get = fake.client.get
    monkeypatch.setattr(fake.client, "get", down)
    sale = open_sale(db, product)
    assert charge(client, sale, channels).json()["status"] == "COMPLETED"
    record = invoice_of(db, sale.id)
    assert record.status == ElectronicInvoiceStatus.ERROR and record.error_code == "CONTACT_PENDING"
    assert fake.invoices == [] and fake.created_contacts == []
    assert db.get(Sale, sale.id).status == SaleStatus.COMPLETED
    monkeypatch.setattr(fake.client, "get", real_get)                            # Alegra back: retry, once
    assert client.post(f"{API}/electronic-invoices/{record.id}/retry").json()["status"] == "ISSUED"
    assert fake.invoices[0]["client"] == {"id": "1"} and len(fake.invoices) == 1


@pytest.mark.parametrize("contacts", [[], [FINAL, contact("8", "222222222222", name="CONSUMIDOR FINAL 2")]])
def test_no_or_several_final_consumers_in_alegra_block_without_creating(ctx, monkeypatch, contacts):
    db, client, fake, product, channels, settings = ctx
    auto_on(monkeypatch, settings)
    fake.contacts = contacts
    sale = open_sale(db, product)
    charge(client, sale, channels)
    record = invoice_of(db, sale.id)
    assert record.status == ElectronicInvoiceStatus.BLOCKED and "Consumidor final" in record.error_message
    assert fake.invoices == [] and fake.created_contacts == []
    assert db.get(Sale, sale.id).status == SaleStatus.COMPLETED


def test_sale_with_real_customer_never_uses_the_final_consumer(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    auto_on(monkeypatch, settings)
    customer = client.post(f"{API}/customers", json=NEW_PERSON).json()["customer"]
    calls = gets_of(fake, monkeypatch)
    sale = open_sale(db, product)
    client.put(f"{API}/sales/{sale.id}/customer", json={"customer_id": customer["id"]})
    charge(client, sale, channels, to_customer(customer["id"]))
    record = invoice_of(db, sale.id)
    assert record.status == ElectronicInvoiceStatus.ISSUED
    assert fake.invoices[0]["client"] == {"id": customer["alegra_contact_id"]} != {"id": "1"}
    assert record.customer_snapshot["final_consumer"] is False
    assert record.customer_snapshot["identification_number"] == "1020304050"
    assert not any(path.startswith("/contacts/") for path in calls)              # no final-consumer lookup


# ---- "Factura electrónica" checkbox: the ONLY trigger; the customer never triggers an invoice ----------------------

def counts(db):
    db.expire_all()
    return {"sales_completed": db.scalar(select(func.count()).select_from(Sale).where(Sale.status == SaleStatus.COMPLETED)),
            "invoices": db.scalar(select(func.count()).select_from(ElectronicInvoice)),
            "payments": db.scalar(select(func.count()).select_from(Payment)),
            "cash": db.scalar(select(func.count()).select_from(CashMovement))}


def test_01_invoice_off_without_customer_completes_with_no_invoice(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    auto_on(monkeypatch, settings)
    sale = open_sale(db, product)
    response = charge(client, sale, channels, invoice={})
    assert response.status_code == 200 and response.json()["status"] == "COMPLETED"
    assert response.json()["requires_electronic_invoice"] is False
    assert counts(db)["invoices"] == 0 and fake.invoices == [] and fake.created_contacts == []


def test_02_invoice_off_with_real_customer_keeps_the_customer_and_never_invoices(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    auto_on(monkeypatch, settings)
    customer = client.post(f"{API}/customers", json=NEW_PERSON).json()["customer"]
    sale = open_sale(db, product)
    assert client.put(f"{API}/sales/{sale.id}/customer", json={"customer_id": customer["id"]}).status_code == 200
    response = charge(client, sale, channels, invoice={})
    assert response.status_code == 200 and response.json()["customer"]["id"] == customer["id"]
    stored = db.get(Sale, sale.id); db.refresh(stored)
    assert str(stored.customer_id) == customer["id"] and stored.requires_electronic_invoice is False
    assert counts(db)["invoices"] == 0 and fake.invoices == []


def test_03_invoice_on_final_consumer_issues_once_with_stamp(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    auto_on(monkeypatch, settings)
    sale = open_sale(db, product)
    response = charge(client, sale, channels)                                     # FC
    assert response.status_code == 200 and response.json()["requires_electronic_invoice"] is True
    record = invoice_of(db, sale.id)
    assert record.status == ElectronicInvoiceStatus.ISSUED and record.customer_id is None
    assert record.customer_snapshot["identification_number"] == "222222222222"
    assert len(fake.invoices) == 1 and fake.invoices[0]["client"] == {"id": "1"}
    assert fake.invoices[0]["stamp"] == {"generateStamp": True} and fake.created_contacts == []


def test_04_invoice_on_customer_bills_that_customer_independent_of_the_sale_customer(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    auto_on(monkeypatch, settings)
    customer = client.post(f"{API}/customers", json=NEW_PERSON).json()["customer"]
    sale = open_sale(db, product)                                                 # no sale customer at all
    charge(client, sale, channels, to_customer(customer["id"]))
    stored = db.get(Sale, sale.id); db.refresh(stored)
    assert stored.customer_id is None and str(stored.invoice_customer_id) == customer["id"]
    record = invoice_of(db, sale.id)
    assert fake.invoices[0]["client"] == {"id": customer["alegra_contact_id"]}
    assert record.customer_snapshot["final_consumer"] is False and str(record.customer_id) == customer["id"]


@pytest.mark.parametrize("intent,status", [
    ({"recipient": "CUSTOMER"}, 422),                                             # no recipient
    ({"recipient": "CUSTOMER", "customer_id": "00000000-0000-0000-0000-000000000001"}, 404),   # unknown
    ({"recipient": "FINAL_CONSUMER", "customer_id": "00000000-0000-0000-0000-000000000001"}, 422),
    ({"recipient": "OTRO"}, 422)])
def test_05_invoice_on_without_valid_recipient_is_rejected_before_the_sale(ctx, monkeypatch, intent, status):
    db, client, fake, product, channels, settings = ctx
    auto_on(monkeypatch, settings)
    sale = open_sale(db, product)
    before = counts(db)
    response = charge(client, sale, channels, invoice={"electronic_invoice": intent})
    assert response.status_code == status
    stored = db.get(Sale, sale.id); db.refresh(stored)
    assert stored.status == SaleStatus.OPEN and counts(db) == before                # no payment, cash or invoice
    assert current_stock(db, product.id) == Decimal("50") and fake.invoices == []


def test_05b_inactive_customer_as_recipient_is_rejected_before_the_sale(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    auto_on(monkeypatch, settings)
    customer = client.post(f"{API}/customers", json=NEW_PERSON).json()["customer"]
    client.patch(f"{API}/customers/{customer['id']}/active", json={"active": False})
    sale = open_sale(db, product)
    assert charge(client, sale, channels, to_customer(customer["id"])).status_code == 409
    assert current_stock(db, product.id) == Decimal("50") and counts(db)["sales_completed"] == 0


def test_08_double_cobrar_gives_one_sale_and_one_invoice(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    auto_on(monkeypatch, settings)
    sale = open_sale(db, product)
    assert charge(client, sale, channels).status_code == 200
    assert charge(client, sale, channels).status_code == 404                      # already completed
    assert counts(db)["sales_completed"] == 1 and counts(db)["invoices"] == 1 and len(fake.invoices) == 1
    assert current_stock(db, product.id) == Decimal("49") and counts(db)["payments"] == 1


def test_10_auto_issue_off_leaves_the_requested_invoice_pending_without_posting(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx                            # ALEGRA_AUTO_ISSUE=false
    sale = open_sale(db, product)
    assert charge(client, sale, channels).json()["status"] == "COMPLETED"
    record = invoice_of(db, sale.id)
    assert record.status == ElectronicInvoiceStatus.PENDING and record.customer_id is None
    assert fake.invoices == [] and fake.created_contacts == []


def test_11_manual_sale_without_checkbox_is_not_invoiced_even_with_auto_issue(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    auto_on(monkeypatch, settings)
    manual_category(db)
    movements_before = db.scalar(select(func.count()).select_from(InventoryMovement))
    response = client.post(f"{API}/sales/manual", json={"source": "MANUAL_SACK", "description": "Bulto", "amount": "90000",
                                                        "channel_id": str(channels["Nequi"].id),
                                                        "idempotency_key": "manual-noinvoice-1"})
    assert response.status_code == 201 and response.json()["requires_electronic_invoice"] is False
    assert counts(db)["invoices"] == 0 and fake.invoices == []
    assert db.scalar(select(func.count()).select_from(InventoryMovement)) == movements_before


def test_12_historical_invoice_without_the_flag_still_retries_and_syncs(ctx, monkeypatch):
    """A sale from before migration 0024: requires_electronic_invoice=false, recipient = its
    customer_id (the old rule), with an ElectronicInvoice that failed. Retry and sync work."""
    db, client, fake, product, channels, settings = ctx
    customer = client.post(f"{API}/customers", json=NEW_PERSON).json()["customer"]
    sale = open_sale(db, product)
    client.put(f"{API}/sales/{sale.id}/customer", json={"customer_id": customer["id"]})
    charge(client, sale, channels, invoice={})                                    # no checkbox -> no row
    record = ElectronicInvoice(sale_id=sale.id, status=ElectronicInvoiceStatus.ERROR, error_code="AlegraServerError")
    db.add(record); db.commit()                                                   # the historical row
    auto_on(monkeypatch, settings)
    retried = client.post(f"{API}/electronic-invoices/{record.id}/retry")
    assert retried.status_code == 200 and retried.json()["status"] == "ISSUED"
    assert fake.invoices[0]["client"] == {"id": customer["alegra_contact_id"]}    # old rule: the sale's customer
    fake.get_invoice = lambda provider_invoice_id, fields=None: {"id": provider_invoice_id, **STAMPED}
    synced = client.post(f"{API}/electronic-invoices/{record.id}/sync")
    assert synced.status_code == 200 and synced.json()["cufe"] == "cufe-xyz"
    assert db.get(Sale, sale.id).requires_electronic_invoice is False and len(fake.invoices) == 1


def test_00_auto_issue_true_never_means_invoice_every_sale(ctx, monkeypatch):
    """Regression for V-6ED776F2 / ERG4628 (2026-10-03, old code still deployed): with
    ALEGRA_AUTO_ISSUE=true, a normal checkout WITHOUT the "Factura electrónica" checkbox
    (electronic_invoice absent) must not invoice, schedule anything or touch Alegra."""
    db, client, fake, product, channels, settings = ctx
    auto_on(monkeypatch, settings)
    scheduled, item_posts = [], []
    monkeypatch.setattr(invoicing, "auto_issue_for_sale", lambda *a, **k: scheduled.append(a))
    fake.create_item = lambda payload: item_posts.append(payload)
    sale = open_sale(db, product)
    response = client.post(f"{API}/sales/{sale.id}/checkout",
                           json={"channel_id": str(channels["Efectivo"].id), "received_amount": "10000"})
    assert response.status_code == 200 and response.json()["status"] == "COMPLETED"
    stored = db.get(Sale, sale.id); db.refresh(stored)
    assert stored.requires_electronic_invoice is False and stored.invoice_customer_id is None
    assert counts(db)["invoices"] == 0                       # no ElectronicInvoice
    assert scheduled == []                                   # no background issue
    assert fake.invoices == [] and item_posts == []          # 0 POST /invoices, 0 POST /items
    assert fake.created_contacts == []


# ---- "Producto manual" = a line in the OPEN sale (never a checkout) ---------------------------------------------

def manual_categories(db):
    from app.models import OperationalCategory
    for code, name in (("SUPER", "Super"), ("MASCOTAS_SIN_CODIGO", "Mascotas sin código / Bultos")):
        db.add(OperationalCategory(code=code, name=name))
    db.commit()


def add_manual(client, sale, kind, amount, description="Producto manual"):
    return client.post(f"{API}/sales/{sale.id}/manual-items",
                       json={"kind": kind, "description": description, "amount": str(amount)})


def granel_electronic(client, sale):
    """Checkout choice "Pago electrónico" for the comida a granel (the old, unchanged flow)."""
    response = client.put(f"{API}/sales/{sale.id}/granel-settlement", json={"settlement": "POS"})
    assert response.status_code == 200 and response.json()["granel_settlement"] == "POS"


def side_effects(db):
    db.expire_all()
    return {"payments": db.scalar(select(func.count()).select_from(Payment)),
            "invoices": db.scalar(select(func.count()).select_from(ElectronicInvoice)),
            "movements": db.scalar(select(func.count()).select_from(InventoryMovement)),
            "cash": db.scalar(select(func.count()).select_from(CashMovement))}


@pytest.mark.parametrize("kind,category", [("SUPER", "SUPER"), ("GRANEL", "MASCOTAS_SIN_CODIGO"),
                                           ("BULTO", "MASCOTAS_SIN_CODIGO")])
def test_m1_m3_manual_line_goes_to_the_cart_and_the_sale_stays_open(ctx, kind, category):
    db, client, fake, product, channels, settings = ctx
    manual_categories(db)
    sale = create_sale(db)
    before = side_effects(db)
    response = add_manual(client, sale, kind, 500, "Dulce surtido")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "OPEN" and body["total"] == "500.00"
    line = body["items"][0]
    assert line["product_id"] is None and line["manual_kind"] == kind and line["category_code"] == category
    assert line["product_name"] == "Dulce surtido" and line["quantity"] == "1.000" and line["line_total"] == "500.00"
    assert side_effects(db) == before                                            # no payment/invoice/stock/cash
    assert fake.invoices == []


def test_m4_m5_mixed_cart_is_one_sale_scanned_manual_granel_then_scanned_again(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    manual_categories(db)
    sale = create_sale(db)
    granel_electronic(client, sale)                       # "Pago electrónico" (caso B)
    client.post(f"{API}/sales/{sale.id}/items", json={"barcode": product.barcode})            # Arroz 10.000
    add_manual(client, sale, "SUPER", 500, "Dulce surtido")
    add_manual(client, sale, "GRANEL", 8000, "Comida perro a granel")
    body = client.post(f"{API}/sales/{sale.id}/items", json={"barcode": product.barcode}).json()  # scan again
    assert body["status"] == "OPEN" and len(body["items"]) == 3 and body["total"] == "28500.00"
    assert [i["manual_kind"] for i in body["items"]].count(None) == 1 and body["items"][0]["quantity"] == "2.000"
    # one checkout for everything: granel paid electronically, the rest in cash
    paid = client.post(f"{API}/sales/{sale.id}/checkout", json={"payments": [
        {"channel_id": str(channels["Nequi"].id), "amount": "8000"},
        {"channel_id": str(channels["Efectivo"].id), "amount": "20500", "received_amount": "20500"}]})
    assert paid.status_code == 200 and paid.json()["status"] == "COMPLETED"
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Sale).where(Sale.status == SaleStatus.COMPLETED)) == 1
    assert current_stock(db, product.id) == Decimal("48")                                     # only the catalog line
    sale_movements = db.scalars(select(InventoryMovement).where(InventoryMovement.reference_id == sale.id)).all()
    assert [m.quantity for m in sale_movements] == [Decimal("-2")]


def test_m6_invoice_off_mixed_cart_creates_no_invoice(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    auto_on(monkeypatch, settings)
    manual_categories(db)
    sale = open_sale(db, product)
    add_manual(client, sale, "BULTO", 90000, "Bulto concentrado")
    response = client.post(f"{API}/sales/{sale.id}/checkout", json={"channel_id": str(channels["Nequi"].id)})
    assert response.status_code == 200 and response.json()["status"] == "COMPLETED"
    assert side_effects(db)["invoices"] == 0 and fake.invoices == []


def test_m7_invoice_on_mixed_cart_issues_one_invoice_with_generic_items(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    auto_on(monkeypatch, settings)
    monkeypatch.setattr(settings, "alegra_item_manual_bulk", "372")
    monkeypatch.setattr(settings, "alegra_item_manual_sack", "2988")
    manual_categories(db)
    sale = open_sale(db, product)                                                              # Arroz 10.000
    granel_electronic(client, sale)                       # "Pago electrónico" (caso B)
    add_manual(client, sale, "GRANEL", 8000, "Granel perro")
    add_manual(client, sale, "BULTO", 90000, "Bulto gato")
    response = client.post(f"{API}/sales/{sale.id}/checkout",
                           json={**FC, "channel_id": str(channels["Nequi"].id)})
    assert response.status_code == 200 and response.json()["status"] == "COMPLETED"
    assert side_effects(db)["invoices"] == 1 and len(fake.invoices) == 1                       # ONE invoice
    lines = fake.invoices[0]["items"]
    assert [(l["id"], l["price"]) for l in lines] == [("77", 10000.0), ("372", 8000.0), ("2988", 90000.0)]
    assert [l.get("description") for l in lines[1:]] == ["Granel perro", "Bulto gato"]
    assert invoice_of(db, sale.id).status == ElectronicInvoiceStatus.ISSUED


def test_m7b_manual_super_without_configured_item_blocks_the_invoice_never_guesses(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    auto_on(monkeypatch, settings)
    monkeypatch.setattr(settings, "alegra_item_manual_super", "")           # not configured (independent of .env)
    manual_categories(db)
    sale = open_sale(db, product)
    add_manual(client, sale, "SUPER", 500, "Dulce surtido")
    response = client.post(f"{API}/sales/{sale.id}/checkout", json={**FC, "channel_id": str(channels["Nequi"].id)})
    assert response.status_code == 200 and response.json()["status"] == "COMPLETED"           # sale never reverted
    record = invoice_of(db, sale.id)
    assert record.status == ElectronicInvoiceStatus.BLOCKED and "Súper" in record.error_message
    assert fake.invoices == []


def test_m7c_manual_super_with_configured_item_is_billed_as_that_item(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    auto_on(monkeypatch, settings)
    monkeypatch.setattr(settings, "alegra_item_manual_super", "1782")
    manual_categories(db)
    sale = open_sale(db, product)
    add_manual(client, sale, "SUPER", 500, "Dulce surtido")
    client.post(f"{API}/sales/{sale.id}/checkout", json={**FC, "channel_id": str(channels["Nequi"].id)})
    assert invoice_of(db, sale.id).status == ElectronicInvoiceStatus.ISSUED and len(fake.invoices) == 1
    assert fake.invoices[0]["items"][1] == {"id": "1782", "price": 500.0, "quantity": 1.0, "tax": [],
                                            "description": "Dulce surtido"}


def test_m8_report_manual_super_in_super_and_granel_bulto_in_mascotas_sin_codigo(ctx):
    from app.services.operations import daily_summary
    db, client, fake, product, channels, settings = ctx
    manual_categories(db)
    sale = create_sale(db)
    granel_electronic(client, sale)                       # "Pago electrónico" (caso B)
    add_manual(client, sale, "SUPER", 500, "Dulce")
    add_manual(client, sale, "GRANEL", 8000, "Granel")
    add_manual(client, sale, "BULTO", 90000, "Bulto")
    assert client.post(f"{API}/sales/{sale.id}/checkout", json={"channel_id": str(channels["Nequi"].id)}).status_code == 200
    db.expire_all()
    now = datetime.now(timezone.utc)
    summary = daily_summary(db, now - timedelta(hours=1), now + timedelta(hours=1))
    totals = {c.category_code: c.total for c in summary.categories}
    assert totals == {"SUPER": Decimal("500"), "MASCOTAS_SIN_CODIGO": Decimal("98000")}


@pytest.mark.parametrize("payments,status", [
    ("CASH_ONLY", 409),            # granel paid in cash: the scale already recorded it
    ("CASH_COVERS_GRANEL", 409),   # split, but the cash part eats into the granel subtotal
    ("ELECTRONIC", 200),
    ("SPLIT_OK", 200)])            # electronic >= granel subtotal, cash for the rest
def test_m9_granel_cash_rule_holds_in_the_cart(ctx, payments, status):
    db, client, fake, product, channels, settings = ctx
    manual_categories(db)
    sale = open_sale(db, product)                                                              # Arroz 10.000
    granel_electronic(client, sale)                       # "Pago electrónico" (caso B)
    add_manual(client, sale, "GRANEL", 8000, "Granel")                                         # total 18.000
    cash, nequi = str(channels["Efectivo"].id), str(channels["Nequi"].id)
    body = {"CASH_ONLY": {"channel_id": cash, "received_amount": "18000"},
            "CASH_COVERS_GRANEL": {"payments": [{"channel_id": nequi, "amount": "5000"},
                                                {"channel_id": cash, "amount": "13000", "received_amount": "13000"}]},
            "ELECTRONIC": {"channel_id": nequi},
            "SPLIT_OK": {"payments": [{"channel_id": nequi, "amount": "8000"},
                                      {"channel_id": cash, "amount": "10000", "received_amount": "10000"}]}}[payments]
    before = side_effects(db)
    response = client.post(f"{API}/sales/{sale.id}/checkout", json=body)
    assert response.status_code == status
    db.expire_all()
    if status == 409:
        assert "granel" in response.json()["detail"] and db.get(Sale, sale.id).status == SaleStatus.OPEN
        assert side_effects(db) == before and current_stock(db, product.id) == Decimal("50")
    else:
        assert db.get(Sale, sale.id).status == SaleStatus.COMPLETED


def test_m9b_bulto_and_manual_super_can_be_paid_in_cash(ctx):
    db, client, fake, product, channels, settings = ctx
    manual_categories(db)
    sale = create_sale(db)
    add_manual(client, sale, "BULTO", 90000, "Bulto")
    add_manual(client, sale, "SUPER", 500, "Dulce")
    response = client.post(f"{API}/sales/{sale.id}/checkout",
                           json={"channel_id": str(channels["Efectivo"].id), "received_amount": "100000"})
    assert response.status_code == 200 and response.json()["payments"][0]["change_amount"] == "9500.00"


def test_m10_manual_line_can_be_removed_before_checkout(ctx):
    db, client, fake, product, channels, settings = ctx
    manual_categories(db)
    sale = open_sale(db, product)
    body = add_manual(client, sale, "SUPER", 500, "Dulce").json()
    manual_line = next(i for i in body["items"] if i["manual_kind"] == "SUPER")
    after = client.delete(f"{API}/sales/{sale.id}/items/{manual_line['id']}").json()
    assert after["status"] == "OPEN" and after["total"] == "10000.00" and len(after["items"]) == 1
    assert side_effects(db)["payments"] == 0


def test_m11_manual_line_validation_and_closed_cash(ctx):
    db, client, fake, product, channels, settings = ctx
    manual_categories(db)
    sale = create_sale(db)
    assert add_manual(client, sale, "SUPER", 0).status_code == 422
    assert client.post(f"{API}/sales/{sale.id}/manual-items",
                       json={"kind": "OTRO", "description": "x", "amount": "500"}).status_code == 422
    assert client.post(f"{API}/sales/{sale.id}/manual-items",
                       json={"kind": "SUPER", "description": "   ", "amount": "500"}).status_code == 422
    db.scalar(select(CashSession)).closed_at = datetime.now(timezone.utc); db.commit()
    assert add_manual(client, sale, "SUPER", 500).status_code == 409                           # jornada cerrada


# ---- Comida a granel "Báscula / efectivo": in the customer's bill, never POS money ---------------------------------

def scale_state(db, sale_id):
    from app.models import SaleExternalItem
    from app.services.cash import expected_cash
    db.expire_all()
    session = db.scalar(select(CashSession))
    sale = db.get(Sale, sale_id)
    return {"status": sale.status, "pos_total": sale.total,
            "payments": sorted((p.amount, p.received_amount, p.change_amount) for p in
                               db.scalars(select(Payment).where(Payment.sale_id == sale_id)).all()),
            "cash_movements": db.scalar(select(func.coalesce(func.sum(CashMovement.amount), 0))
                                        .where(CashMovement.reference_id == sale_id)),
            "expected_cash": expected_cash(db, session),
            "external": sorted((e.description, e.amount, e.settlement, e.kind) for e in
                               db.scalars(select(SaleExternalItem).where(SaleExternalItem.sale_id == sale_id)).all()),
            "granel_lines": [i.line_total for i in sale.items if i.manual_kind == "GRANEL"]}


def cash_checkout(client, sale, channels, received):
    return client.post(f"{API}/sales/{sale.id}/checkout",
                       json={"channel_id": str(channels["Efectivo"].id), "received_amount": str(received)})


def test_s1_s2_s3_s7_s13_s16_pos_50k_plus_scale_10k_cash_100k(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    auto_on(monkeypatch, settings)
    manual_categories(db)
    sale = open_sale(db, product, "5")                                         # POS 50.000 (Arroz x5)
    body = add_manual(client, sale, "GRANEL", 10000, "Granel perro").json()
    assert body["total"] == "60000.00" and body["granel_settlement"] == "EXTERNAL_SCALE"   # customer's total
    response = cash_checkout(client, sale, channels, 100000)
    assert response.status_code == 200 and response.json()["status"] == "COMPLETED"
    assert response.json()["payments"][0]["change_amount"] == "40000.00"     # change on the whole bill
    state = scale_state(db, sale.id)
    assert state["pos_total"] == Decimal("50000")
    assert state["payments"] == [(Decimal("50000"), Decimal("100000"), Decimal("40000"))]   # POS cash = 50.000
    assert state["cash_movements"] == Decimal("50000")                       # not 60.000, not 100.000
    assert state["expected_cash"] == Decimal("50000")                        # opening 0 + POS cash only
    assert state["external"] == [("Granel perro", Decimal("10000"), "EXTERNAL_SCALE", "GRANEL")]
    assert state["granel_lines"] == []                                       # not a POS sale line any more
    assert current_stock(db, product.id) == Decimal("45")                    # catalog stock intact
    assert counts(db)["invoices"] == 0 and fake.invoices == []               # factura OFF
    assert response.json()["external_items"][0]["amount"] == "10000.00"     # visible when consulting the sale


def test_s4_s12_super_manual_47350_plus_scale_13700_change_38950(ctx):
    db, client, fake, product, channels, settings = ctx
    manual_categories(db)
    sale = create_sale(db)
    add_manual(client, sale, "SUPER", 47350, "Mercado")
    assert add_manual(client, sale, "GRANEL", 13700, "Granel").json()["total"] == "61050.00"
    response = cash_checkout(client, sale, channels, 100000)
    assert response.status_code == 200 and response.json()["payments"][0]["change_amount"] == "38950.00"
    state = scale_state(db, sale.id)
    assert state["pos_total"] == Decimal("47350") and state["cash_movements"] == Decimal("47350")
    assert state["external"][0][1] == Decimal("13700")


def test_s6_mixed_scale_10k_nequi_20k_cash_30k(ctx):
    db, client, fake, product, channels, settings = ctx
    manual_categories(db)
    sale = open_sale(db, product, "5")                                         # POS 50.000
    add_manual(client, sale, "GRANEL", 10000, "Granel")                        # customer total 60.000
    response = client.post(f"{API}/sales/{sale.id}/checkout", json={"payments": [
        {"channel_id": str(channels["Nequi"].id), "amount": "20000"},
        {"channel_id": str(channels["Efectivo"].id), "amount": "30000", "received_amount": "50000"}]})
    assert response.status_code == 200
    state = scale_state(db, sale.id)
    assert state["payments"] == [(Decimal("20000"), None, None), (Decimal("30000"), Decimal("50000"), Decimal("10000"))]
    assert state["cash_movements"] == Decimal("30000") and state["expected_cash"] == Decimal("30000")   # not 40.000
    assert state["external"][0][1] == Decimal("10000")


def test_s8_daily_summary_never_counts_the_scale_as_pos_money(ctx):
    from app.services.operations import daily_summary
    db, client, fake, product, channels, settings = ctx
    manual_categories(db)
    sale = open_sale(db, product, "5")
    add_manual(client, sale, "GRANEL", 10000, "Granel")
    assert cash_checkout(client, sale, channels, 60000).status_code == 200
    db.expire_all()
    now = datetime.now(timezone.utc)
    summary = daily_summary(db, now - timedelta(hours=1), now + timedelta(hours=1))
    assert summary.sales_total == Decimal("50000") and summary.external_scale_total == Decimal("10000")
    assert sum((c.total for c in summary.categories), Decimal("0")) == Decimal("50000")
    by_channel = sum((ch.amount for c in summary.categories for ch in c.channels if ch.channel_name == "Efectivo"), Decimal("0"))
    assert by_channel == Decimal("50000")                                      # POS cash 50.000, not 60.000
    assert "MASCOTAS_SIN_CODIGO" not in {c.category_code for c in summary.categories}


def test_s5_electronic_granel_is_still_a_pos_sale(ctx):
    from app.services.operations import daily_summary
    db, client, fake, product, channels, settings = ctx
    manual_categories(db)
    sale = open_sale(db, product, "5")
    add_manual(client, sale, "GRANEL", 10000, "Granel")
    granel_electronic(client, sale)
    assert cash_checkout(client, sale, channels, 100000).status_code == 409    # electronic granel never in POS cash
    response = client.post(f"{API}/sales/{sale.id}/checkout", json={"channel_id": str(channels["Nequi"].id)})
    assert response.status_code == 200
    state = scale_state(db, sale.id)
    assert state["pos_total"] == Decimal("60000") and state["external"] == [] and state["granel_lines"] == [Decimal("10000")]
    assert state["payments"] == [(Decimal("60000"), None, None)] and state["cash_movements"] == Decimal("0")
    now = datetime.now(timezone.utc)
    summary = daily_summary(db, now - timedelta(hours=1), now + timedelta(hours=1))
    totals = {c.category_code: (c.total, c.electronic) for c in summary.categories}
    assert totals["MASCOTAS_SIN_CODIGO"] == (Decimal("10000"), Decimal("10000")) and summary.external_scale_total == 0


def test_s9_the_choice_survives_refresh_and_is_per_sale(ctx):
    db, client, fake, product, channels, settings = ctx
    manual_categories(db)
    first, second = create_sale(db), create_sale(db)
    add_manual(client, first, "GRANEL", 10000, "Granel")
    granel_electronic(client, first)
    reloaded = {s["id"]: s for s in client.get(f"{API}/sales/open").json()}       # what a refresh reads
    assert reloaded[str(first.id)]["granel_settlement"] == "POS"
    assert reloaded[str(second.id)]["granel_settlement"] is None               # no granel -> no scale meaning
    add_manual(client, second, "GRANEL", 5000, "Granel")
    assert client.get(f"{API}/sales/open").json()[-1]["granel_settlement"] == "EXTERNAL_SCALE"   # natural default


def test_s10_several_granel_lines_make_one_scale_subtotal(ctx):
    db, client, fake, product, channels, settings = ctx
    manual_categories(db)
    sale = open_sale(db, product)                                              # POS 10.000
    add_manual(client, sale, "GRANEL", 4000, "Granel perro")
    add_manual(client, sale, "GRANEL", 6000, "Granel gato")
    response = cash_checkout(client, sale, channels, 20000)
    assert response.status_code == 200 and response.json()["payments"][0]["change_amount"] == "0.00"
    state = scale_state(db, sale.id)
    assert sum(e[1] for e in state["external"]) == Decimal("10000") and len(state["external"]) == 2
    assert state["pos_total"] == Decimal("10000") and state["cash_movements"] == Decimal("10000")


def test_s11_bulto_keeps_its_behaviour_next_to_scale_granel(ctx):
    db, client, fake, product, channels, settings = ctx
    manual_categories(db)
    sale = create_sale(db)
    add_manual(client, sale, "BULTO", 90000, "Bulto")
    add_manual(client, sale, "GRANEL", 10000, "Granel")
    response = cash_checkout(client, sale, channels, 100000)                   # bulto in POS cash: allowed
    assert response.status_code == 200
    state = scale_state(db, sale.id)
    assert state["pos_total"] == Decimal("90000") and state["cash_movements"] == Decimal("90000")
    assert [i.manual_kind for i in db.get(Sale, sale.id).items] == ["BULTO"]


def test_s14_cancel_before_checkout_has_no_financial_effect(ctx):
    db, client, fake, product, channels, settings = ctx
    manual_categories(db)
    sale = open_sale(db, product)
    add_manual(client, sale, "GRANEL", 10000, "Granel")
    before = side_effects(db)
    assert client.post(f"{API}/sales/{sale.id}/cancel").json()["status"] == "CANCELLED"
    state = scale_state(db, sale.id)
    assert side_effects(db) == before and state["external"] == [] and state["payments"] == []


@pytest.mark.parametrize("case,status", [("NOT_ENOUGH_CASH", 400), ("EXTERNAL_ONLY", 409), ("WRONG_SPLIT", 409)])
def test_s15_invalid_checkout_rolls_back_everything(ctx, case, status):
    db, client, fake, product, channels, settings = ctx
    manual_categories(db)
    sale = create_sale(db) if case == "EXTERNAL_ONLY" else open_sale(db, product, "5")
    add_manual(client, sale, "GRANEL", 10000, "Granel")
    before = side_effects(db)
    body = {"NOT_ENOUGH_CASH": {"channel_id": str(channels["Efectivo"].id), "received_amount": "55000"},  # < 60.000
            "EXTERNAL_ONLY": {"channel_id": str(channels["Efectivo"].id), "received_amount": "10000"},
            "WRONG_SPLIT": {"payments": [{"channel_id": str(channels["Nequi"].id), "amount": "60000"}]}}[case]  # pays the scale part
    response = client.post(f"{API}/sales/{sale.id}/checkout", json=body)
    assert response.status_code == status
    state = scale_state(db, sale.id)
    assert state["status"] == SaleStatus.OPEN and state["external"] == [] and state["granel_lines"] == [Decimal("10000")]
    assert side_effects(db) == before


def test_s17_invoice_on_with_scale_granel_is_blocked_safely(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    auto_on(monkeypatch, settings)
    monkeypatch.setattr(settings, "alegra_item_manual_bulk", "372")
    manual_categories(db)
    sale = open_sale(db, product, "5")
    add_manual(client, sale, "GRANEL", 10000, "Granel")
    response = client.post(f"{API}/sales/{sale.id}/checkout", json={
        **FC, "channel_id": str(channels["Efectivo"].id), "received_amount": "60000"})
    assert response.status_code == 200 and response.json()["status"] == "COMPLETED"       # sale never reverted
    record = invoice_of(db, sale.id)
    assert record.status == ElectronicInvoiceStatus.BLOCKED and "báscula" in record.error_message
    assert fake.invoices == []                                                             # 0 POST /invoices


# ---- Reconciliation block 1 + Mascotas sin código taxonomy (GRANEL / BULTO / PET_OTHER) ---------------------------

def block1_categories(db):
    """Catalog categories for block 1 + SUPER / MASCOTAS_SIN_CODIGO; one product with stock per category."""
    from app.models import OperationalCategory
    manual_categories(db)
    made = {}
    for code, name, barcode in (("CIGARROS", "Cigarros", "9001"), ("MASCOTAS_CON_CODIGO", "Mascotas con código", "9002"),
                                ("LICORES", "Licores", "9003")):
        category = OperationalCategory(code=code, name=name)
        db.add(category); db.flush()
        product = Product(barcode=barcode, name=f"Producto {code}", sale_price=Decimal("1000"),
                          unit_cost=Decimal("500"), operational_category_id=category.id)
        db.add(product); db.flush()
        db.add(InventoryMovement(product_id=product.id, movement_type=InventoryMovementType.PURCHASE,
                                 quantity=Decimal("500"), unit_cost=Decimal("500"), user_id=db.scalar(select(User.id))))
        made[code] = product
    db.commit()
    return made


def sell(client, db, channels, lines, body):
    """lines: [("scan", product, qty) | ("manual", kind, amount)]; body: checkout payload."""
    sale = create_sale(db)
    for line in lines:
        if line[0] == "scan":
            client.post(f"{API}/sales/{sale.id}/items", json={"barcode": line[1].barcode, "quantity": str(line[2])})
        else:
            add_manual(client, sale, line[1], line[2], f"{line[1]} prueba")
    response = client.post(f"{API}/sales/{sale.id}/checkout", json=body(channels))
    assert response.status_code == 200, response.text
    return sale


cash = lambda received: (lambda ch: {"channel_id": str(ch["Efectivo"].id), "received_amount": str(received)})
nequi = lambda ch: {"channel_id": str(ch["Nequi"].id)}


def recon(client, session_id=None):
    url = f"{API}/cash/reconciliation" + (f"?session_id={session_id}" if session_id else "")
    response = client.get(url)
    assert response.status_code == 200, response.text
    data = response.json()
    data["by"] = {row["category_code"]: Decimal(row["amount"]) for row in data["pos_cash_by_category"]}
    return data


def test_r1_r3_super_50k_plus_granel_10k_scale_is_external_not_pos(ctx):
    db, client, fake, product, channels, settings = ctx
    manual_categories(db)
    sale = create_sale(db)
    add_manual(client, sale, "SUPER", 50000, "Mercado")
    assert add_manual(client, sale, "GRANEL", 10000, "Granel").json()["total"] == "60000.00"       # customer total
    response = cash_checkout(client, sale, channels, 100000)
    assert response.json()["payments"][0]["change_amount"] == "40000.00"                           # change
    state = scale_state(db, sale.id)
    assert state["payments"] == [(Decimal("50000"), Decimal("100000"), Decimal("40000"))]          # no granel Payment
    assert state["cash_movements"] == Decimal("50000")                                             # no granel CashMovement
    assert state["external"] == [("Granel", Decimal("10000"), "EXTERNAL_SCALE", "GRANEL")]       # operational record


def test_r4_bulto_is_a_normal_pos_sale_in_mascotas_sin_codigo(ctx):
    db, client, fake, product, channels, settings = ctx
    manual_categories(db)
    sale = sell(client, db, channels, [("manual", "BULTO", 90000)], cash(90000))
    state = scale_state(db, sale.id)
    assert state["cash_movements"] == Decimal("90000") and state["external"] == []
    assert db.get(Sale, sale.id).items[0].category_code == "MASCOTAS_SIN_CODIGO"
    assert db.get(Sale, sale.id).granel_settlement is None                                         # no scale meaning


@pytest.mark.parametrize("pay,in_cash", [(cash(5000), True), (nequi, False)])
def test_r5_r6_pet_other_is_a_normal_pos_sale_never_scale(ctx, pay, in_cash):
    db, client, fake, product, channels, settings = ctx
    manual_categories(db)
    amount = 5000 if in_cash else 20000
    sale = create_sale(db)
    line = add_manual(client, sale, "PET_OTHER", amount, "Bolsas para mascota" if in_cash else "Tapete").json()["items"][0]
    assert line["manual_kind"] == "PET_OTHER" and line["category_code"] == "MASCOTAS_SIN_CODIGO"
    assert client.post(f"{API}/sales/{sale.id}/checkout", json=pay(channels)).status_code == 200
    state = scale_state(db, sale.id)
    assert state["pos_total"] == Decimal(amount) and state["external"] == []                       # NOT scale
    assert state["cash_movements"] == (Decimal(amount) if in_cash else Decimal("0"))
    assert recon(client)["by"]["MASCOTAS_SIN_CODIGO"] == (Decimal(amount) if in_cash else Decimal("0"))


def test_r7_granel_is_never_confused_with_pet_other(ctx):
    db, client, fake, product, channels, settings = ctx
    manual_categories(db)
    sale = create_sale(db)
    add_manual(client, sale, "PET_OTHER", 5000, "Tapete")
    assert client.get(f"{API}/sales/open").json()[-1]["granel_settlement"] is None                 # PET_OTHER: no scale
    add_manual(client, sale, "GRANEL", 8000, "Granel")
    cash_checkout(client, sale, channels, 13000)
    state = scale_state(db, sale.id)
    assert state["external"] == [("Granel", Decimal("8000"), "EXTERNAL_SCALE", "GRANEL")]
    assert state["pos_total"] == Decimal("5000") and [i.manual_kind for i in db.get(Sale, sale.id).items] == ["PET_OTHER"]


def test_r8_r9_scale_closing_derives_cash_and_rejects_digital_over_total(ctx):
    db, client, fake, product, channels, settings = ctx
    session_id = db.scalar(select(CashSession.id))
    bad = client.put(f"{API}/cash/sessions/{session_id}/scale-closing", json={"scale_total": "300000", "scale_digital": "300001"})
    assert bad.status_code == 422
    assert client.put(f"{API}/cash/sessions/{session_id}/scale-closing",
                      json={"scale_total": "300000", "scale_digital": "80000", "scale_cash": "1"}).status_code == 200
    data = recon(client)
    assert data["scale"]["total"] == "300000.00" and data["scale"]["digital"] == "80000.00"
    assert data["scale"]["cash"] == "220000.00"                                                    # derived, never typed
    from app.models import CashSessionScaleClosing
    assert db.scalar(select(func.count()).select_from(CashSessionScaleClosing)) == 1               # survives refresh (DB)


def test_r10_r11_r12_r20_block1_subtotal_380k(ctx):
    from app.services.cash import expected_cash
    db, client, fake, product, channels, settings = ctx
    p = block1_categories(db)
    sell(client, db, channels, [("scan", p["CIGARROS"], 40)], cash(40000))                   # cigarros cash 40.000
    sell(client, db, channels, [("scan", p["MASCOTAS_CON_CODIGO"], 35)], cash(35000))        # mascotas código 35.000
    sell(client, db, channels, [("scan", p["LICORES"], 70)], cash(70000))                    # licores 70.000
    sell(client, db, channels, [("manual", "BULTO", 10000), ("manual", "PET_OTHER", 5000)], cash(15000))  # msc no báscula
    sell(client, db, channels, [("scan", p["LICORES"], 25)], nequi)                          # electronic: never cash
    sell(client, db, channels, [("manual", "SUPER", 50000), ("manual", "GRANEL", 30000)], cash(80000))   # granel báscula
    session = db.scalar(select(CashSession))
    expected_before = expected_cash(db, session)
    before = recon(client)
    assert before["block1_subtotal"] is None and before["scale"] is None                          # no scale closing yet
    client.put(f"{API}/cash/sessions/{session.id}/scale-closing", json={"scale_total": "300000", "scale_digital": "80000"})
    data = recon(client)
    assert data["by"] == {"CIGARROS": Decimal("40000"), "MASCOTAS_CON_CODIGO": Decimal("35000"),
                          "LICORES": Decimal("70000"), "MASCOTAS_SIN_CODIGO": Decimal("15000")}   # granel NOT re-added
    assert Decimal(data["pos_cash_subtotal"]) == Decimal("160000")
    assert Decimal(data["block1_subtotal"]) == Decimal("380000")                                  # 220.000 + 160.000
    assert Decimal(data["observed"]["granel_external_scale"]) == Decimal("30000")                 # observed apart
    db.expire_all()
    assert expected_cash(db, session) == expected_before                                         # ledger unchanged
    assert expected_before == Decimal("210000")      # POS cash only: 40+35+70+15 + SUPER 50 (scale granel excluded)


def test_r13_split_payment_mixed_categories_allocates_cash_correctly(ctx):
    db, client, fake, product, channels, settings = ctx
    p = block1_categories(db)
    sale = create_sale(db)
    client.post(f"{API}/sales/{sale.id}/items", json={"barcode": p["CIGARROS"].barcode, "quantity": "10"})   # 10.000
    client.post(f"{API}/sales/{sale.id}/items", json={"barcode": p["LICORES"].barcode, "quantity": "30"})    # 30.000
    add_manual(client, sale, "GRANEL", 5000, "Granel")                                                       # 5.000 POS
    granel_electronic(client, sale)
    response = client.post(f"{API}/sales/{sale.id}/checkout", json={"payments": [
        {"channel_id": str(channels["Nequi"].id), "amount": "15000"},
        {"channel_id": str(channels["Efectivo"].id), "amount": "30000", "received_amount": "30000"}]})
    assert response.status_code == 200
    data = recon(client)
    # electronic 15.000 covers granel 5.000 first; cash 30.000 spread over cigarros 10k / licores 30k
    assert data["by"]["CIGARROS"] == Decimal("7500") and data["by"]["LICORES"] == Decimal("22500")
    assert data["by"]["MASCOTAS_SIN_CODIGO"] == Decimal("0")                                       # granel never cash
    assert Decimal(data["observed"]["granel_pos"]) == Decimal("5000")


def test_r14_r15_manual_kind_and_settlement_survive_refresh_per_tab(ctx):
    db, client, fake, product, channels, settings = ctx
    manual_categories(db)
    first, second = create_sale(db), create_sale(db)
    add_manual(client, first, "GRANEL", 8000, "Granel")
    add_manual(client, second, "PET_OTHER", 5000, "Bolsa")
    granel_electronic(client, first)
    reloaded = {s["id"]: s for s in client.get(f"{API}/sales/open").json()}
    assert reloaded[str(first.id)]["granel_settlement"] == "POS"
    assert reloaded[str(first.id)]["items"][0]["manual_kind"] == "GRANEL"
    assert reloaded[str(second.id)]["granel_settlement"] is None
    assert reloaded[str(second.id)]["items"][0]["manual_kind"] == "PET_OTHER"


def test_r16_cancel_has_no_financial_or_reconciliation_effect(ctx):
    db, client, fake, product, channels, settings = ctx
    manual_categories(db)
    sale = create_sale(db)
    add_manual(client, sale, "GRANEL", 8000, "Granel")
    add_manual(client, sale, "PET_OTHER", 5000, "Bolsa")
    before = side_effects(db)
    client.post(f"{API}/sales/{sale.id}/cancel")
    assert side_effects(db) == before
    data = recon(client)
    assert sum(data["by"].values()) == 0 and Decimal(data["observed"]["granel_external_scale"]) == 0


def test_r17_r18_invoice_off_none_and_on_with_scale_blocked(ctx, monkeypatch):
    db, client, fake, product, channels, settings = ctx
    auto_on(monkeypatch, settings)
    manual_categories(db)
    off = sell(client, db, channels, [("manual", "SUPER", 1000), ("manual", "GRANEL", 2000)], cash(3000))
    assert invoice_of(db, off.id) is None
    on = create_sale(db)
    add_manual(client, on, "SUPER", 1000, "Dulce")
    add_manual(client, on, "GRANEL", 2000, "Granel")
    client.post(f"{API}/sales/{on.id}/checkout", json={**FC, "channel_id": str(channels["Efectivo"].id), "received_amount": "3000"})
    assert invoice_of(db, on.id).status == ElectronicInvoiceStatus.BLOCKED and fake.invoices == []


def test_r19_historical_sales_keep_their_totals_and_no_scale_meaning(ctx):
    """A sale without granel (like every historical sale) has granel_settlement NULL, no external
    items, and the existing reports read exactly what they read before."""
    from app.services.operations import daily_summary
    db, client, fake, product, channels, settings = ctx
    sale = open_sale(db, product, "2")
    assert cash_checkout(client, sale, channels, 20000).status_code == 200
    stored = db.get(Sale, sale.id); db.refresh(stored)
    assert stored.granel_settlement is None and stored.external_items == [] and stored.total == Decimal("20000")
    now = datetime.now(timezone.utc)
    summary = daily_summary(db, now - timedelta(hours=1), now + timedelta(hours=1))
    assert summary.sales_total == Decimal("20000") and summary.external_scale_total == 0


# ---- Block 1 close report: tied to the cash session, recomputed deterministically, never touches the close ---------

def ledger_counts(db):
    db.expire_all()
    return {"cash_movements": db.scalar(select(func.count()).select_from(CashMovement)),
            "payments": db.scalar(select(func.count()).select_from(Payment)),
            "cash_sum": db.scalar(select(func.coalesce(func.sum(CashMovement.amount), 0)))}


def close_session(client, counted, note="Prueba de cierre"):
    expected = client.get(f"{API}/cash/current").json()["expected_cash"]
    response = client.post(f"{API}/cash/close", json={"counted_amount": str(counted), "expected_amount": expected, "note": note})
    assert response.status_code == 200, response.text
    return Decimal(expected)


def test_c1_c12_close_report_values_survive_and_reproduce_after_closing(ctx):
    from app.services.cash import expected_cash
    db, client, fake, product, channels, settings = ctx
    p = block1_categories(db)
    sell(client, db, channels, [("scan", p["CIGARROS"], 40)], cash(40000))
    sell(client, db, channels, [("scan", p["MASCOTAS_CON_CODIGO"], 35)], cash(35000))
    sell(client, db, channels, [("scan", p["LICORES"], 70)], cash(70000))
    sell(client, db, channels, [("manual", "BULTO", 10000), ("manual", "PET_OTHER", 5000)], cash(15000))
    sell(client, db, channels, [("manual", "SUPER", 50000), ("manual", "GRANEL", 30000)], cash(80000))   # SUPER + scale granel
    session = db.scalar(select(CashSession))
    before = ledger_counts(db)
    client.put(f"{API}/cash/sessions/{session.id}/scale-closing", json={"scale_total": "300000", "scale_digital": "80000"})
    assert ledger_counts(db) == before                                         # registering the scale writes no money
    expected = close_session(client, 217000, "Conteo con sobrante de prueba")
    db.expire_all(); session = db.get(CashSession, session.id)
    # The close keeps its own logic: expected = opening + POS cash movements; counted = what was typed.
    assert expected == Decimal("210000") == session.closing_expected == expected_cash(db, session)
    assert session.closing_actual == Decimal("217000") and session.difference == Decimal("7000")
    assert ledger_counts(db) == before                                         # no CashMovement / Payment from block 1
    first = client.get(f"{API}/cash/reconciliation?session_id={session.id}").json()
    second = client.get(f"{API}/cash/reconciliation?session_id={session.id}").json()
    assert first == second and first["session_status"] == "CLOSED"            # same report when consulted again
    assert first["scale"]["total"] == "300000.00" and first["scale"]["digital"] == "80000.00"
    assert first["scale"]["cash"] == "220000.00"
    by = {row["category_code"]: row["amount"] for row in first["pos_cash_by_category"]}
    assert by == {"CIGARROS": "40000.00", "MASCOTAS_CON_CODIGO": "35000.00", "LICORES": "70000.00",
                  "MASCOTAS_SIN_CODIGO": "15000.00"}                         # SUPER absent; granel not re-added
    assert first["pos_cash_subtotal"] == "160000.00" and first["block1_subtotal"] == "380000.00"
    assert first["observed"]["granel_external_scale"] == "30000.00"           # shown apart
    assert Decimal(first["block1_subtotal"]) != session.closing_actual        # never the counted cash
    # Default resolution after the close = that last closed session (what Caja shows right after closing).
    assert client.get(f"{API}/cash/reconciliation").json() == first


def test_c13_closed_without_scale_never_invents_a_subtotal(ctx):
    db, client, fake, product, channels, settings = ctx
    p = block1_categories(db)
    sell(client, db, channels, [("scan", p["LICORES"], 10)], cash(10000))
    close_session(client, 10000)
    data = client.get(f"{API}/cash/reconciliation").json()
    assert data["session_status"] == "CLOSED" and data["scale"] is None and data["block1_subtotal"] is None
    assert data["pos_cash_subtotal"] == "10000.00"                            # POS part still shown


def test_c_scale_can_be_completed_after_closing_without_touching_the_close(ctx):
    db, client, fake, product, channels, settings = ctx
    session_id = db.scalar(select(CashSession.id))
    close_session(client, 0)
    db.expire_all(); closed = db.get(CashSession, session_id)
    snapshot = (closed.closing_expected, closed.closing_actual, closed.difference, closed.closed_at)
    assert client.put(f"{API}/cash/sessions/{session_id}/scale-closing",
                      json={"scale_total": "50000", "scale_digital": "0"}).json()["block1_subtotal"] == "50000.00"
    db.expire_all(); closed = db.get(CashSession, session_id)
    assert (closed.closing_expected, closed.closing_actual, closed.difference, closed.closed_at) == snapshot
