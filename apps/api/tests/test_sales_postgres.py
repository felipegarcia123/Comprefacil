"""PostgreSQL-only regression: Sale rows are locked with SELECT ... FOR UPDATE
(checkout, add_item, cancel, set customer). Any eager *joined* nullable
relationship on Sale (e.g. Sale.customer) turns that into a LEFT JOIN that
PostgreSQL rejects ("FOR UPDATE cannot be applied to the nullable side of an
outer join"); SQLite accepts it silently. Read-only: the planner raises the
error with zero matching rows, so no data is created. Run it against a COPY
of the database (DATABASE_URL), never the real one; skipped without PostgreSQL."""
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.db import engine
from app.models import ElectronicInvoice, Sale


@pytest.fixture()
def pg_session():
    if engine.dialect.name != "postgresql":
        pytest.skip("DATABASE_URL no apunta a PostgreSQL")
    try:
        with engine.connect():
            pass
    except OperationalError:
        pytest.skip("No hay una instancia de PostgreSQL alcanzable en DATABASE_URL")
    with Session(engine) as db:
        yield db
        db.rollback()


@pytest.mark.parametrize("model", [Sale, ElectronicInvoice])
def test_row_lock_is_valid_sql_on_postgresql(pg_session, model):
    assert pg_session.scalar(select(model).where(model.id == uuid4()).with_for_update()) is None


def test_barcode_null_many_and_unique_alegra_item_on_postgresql(pg_session):
    """Real PostgreSQL semantics (SQLite can't prove them): many NULL barcodes under the
    unique index, real codes unique, one product per alegra_item_id. Rolled back."""
    from decimal import Decimal
    from sqlalchemy.exc import IntegrityError
    from app.models import Product
    tag = uuid4().hex[:8]
    pg_session.add_all([Product(barcode=None, name=f"PGTEST {tag} A", sale_price=Decimal(1), unit_cost=Decimal(1)),
                        Product(barcode=None, name=f"PGTEST {tag} B", sale_price=Decimal(1), unit_cost=Decimal(1),
                                alegra_item_id=f"pg-{tag}")])
    pg_session.flush()                                                  # two NULLs coexist
    pg_session.add(Product(barcode=None, name=f"PGTEST {tag} C", sale_price=Decimal(1), unit_cost=Decimal(1),
                           alegra_item_id=f"pg-{tag}"))
    with pytest.raises(IntegrityError):
        pg_session.flush()
    pg_session.rollback()


def test_two_registers_ensuring_the_same_new_product_create_one_item(pg_session, monkeypatch):
    """Two sessions (two cash registers) invoice the same never-seen product at once. The row
    lock serializes search -> POST /items -> save: exactly one POST, both end linked to it.
    Writes a temporary product to the COPY database and removes it afterwards."""
    import threading
    from decimal import Decimal
    from sqlalchemy import delete
    from app.core.config import settings
    from app.models import CatalogSyncOperation, Product
    from app.services import alegra_catalog_sync as sync
    monkeypatch.setattr(settings, "alegra_catalog_create_enabled", True)

    class Fake:
        def __init__(self):
            self.items, self.posts, self.gate, self.entered = [], 0, threading.Event(), threading.Event()
            fake = self
            class Client:
                def get(self, path, params=None):
                    return [i for i in fake.items if (params or {}).get("reference", "") in i["reference"]]
            self.client = Client()

        def create_item(self, payload):
            self.posts += 1
            self.entered.set()
            self.gate.wait(5)                 # hold the first POST open while the other register arrives
            item = {"id": f"pg{self.posts}", "reference": payload["reference"], "status": "active"}
            self.items.append(item)
            return item

    product = Product(barcode=f"PGT{uuid4().hex[:10]}", name="PG CONCURRENCIA", sale_price=Decimal(10), unit_cost=Decimal(1))
    pg_session.add(product); pg_session.commit()
    fake, results = Fake(), {}
    try:
        def register(name):
            try:
                with Session(engine) as db:
                    results[name] = sync.ensure_alegra_item(db, product.id, fake)["status"]
            except Exception as exc:  # noqa: BLE001 - surfaced by the asserts below
                results[name] = repr(exc)
        first = threading.Thread(target=register, args=("a",)); first.start()
        assert fake.entered.wait(5), results
        second = threading.Thread(target=register, args=("b",)); second.start()
        second.join(1)
        assert second.is_alive()                  # blocked on the row lock, not searching/posting
        fake.gate.set(); first.join(5); second.join(5)
        assert fake.posts == 1 and sorted(results.values()) == ["ALREADY_LINKED", "CREATED"]
        pg_session.expire_all()
        assert pg_session.get(Product, product.id).alegra_item_id == "pg1"
    finally:
        pg_session.rollback()
        pg_session.execute(delete(CatalogSyncOperation).where(CatalogSyncOperation.product_id == product.id))
        pg_session.execute(delete(Product).where(Product.id == product.id))
        pg_session.commit()


def test_stamp_claim_is_valid_sql_on_postgresql(pg_session):
    """The atomic claim that serializes stamping a remote draft (C3) is a single conditional
    UPDATE; on PostgreSQL it must compile and run. A random id matches no row: nothing changes."""
    from types import SimpleNamespace
    from app.services import electronic_invoicing as invoicing
    assert invoicing._claim_stamp(pg_session, SimpleNamespace(id=uuid4(), provider_invoice_id="0")) is False


def test_several_manual_lines_in_one_sale_on_postgresql(pg_session):
    """UNIQUE(sale_id, product_id) must allow several manual lines (product_id NULL) in the same
    OPEN sale — PostgreSQL treats NULLs as distinct. Rolled back."""
    from decimal import Decimal
    from app.models import SaleItem, User
    user = pg_session.scalar(select(User).limit(1))
    if user is None:
        pytest.skip("Sin usuarios en la copia")
    sale = Sale(number=f"V-T{uuid4().hex[:7].upper()}", created_by=user.id, total=Decimal("8500"))
    for kind, name, amount in (("SUPER", "Dulce", "500"), ("GRANEL", "Granel", "8000"), ("SUPER", "Dulce", "500")):
        sale.items.append(SaleItem(product_id=None, manual_kind=kind, product_name=name, quantity=Decimal("1"),
                                   unit_price=Decimal(amount), unit_cost=Decimal("0"), line_total=Decimal(amount)))
    pg_session.add(sale)
    pg_session.flush()
    assert len(sale.items) == 3
    pg_session.rollback()


def test_scale_closing_constraints_on_postgresql(pg_session):
    """The database itself refuses scale_digital > scale_total and a second row per session. Rolled back."""
    from decimal import Decimal
    from sqlalchemy.exc import IntegrityError
    from app.models import CashSession, CashSessionScaleClosing
    session = pg_session.scalar(select(CashSession).limit(1))
    if session is None:
        pytest.skip("Sin jornadas en la copia")
    pg_session.add(CashSessionScaleClosing(cash_session_id=session.id, scale_total=Decimal("100"), scale_digital=Decimal("101")))
    with pytest.raises(IntegrityError):
        pg_session.flush()
    pg_session.rollback()
