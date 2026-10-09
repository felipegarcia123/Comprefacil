"""Integration checks against the real PostgreSQL instance.

Purchase.payment is a nullable, eagerly-joined relationship (lazy="joined",
uselist=False), so `SELECT ... FOR UPDATE` on a DRAFT purchase (no payment
yet) hits `FOR UPDATE cannot be applied to the nullable side of an outer
join` on PostgreSQL. SQLite accepts the clause silently instead of
validating it, so the regular test suite (test_purchases.py, sqlite://)
cannot catch this class of bug. This module targets the real database
behind DATABASE_URL (the same one docker-compose starts) instead.

Skipped automatically when no PostgreSQL is reachable, so `pytest` keeps
working unchanged for anyone running the suite without the stack up.
Uses a disposable supplier/product created and deleted per test; never
touches seeded or business data.
"""
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.db import engine
from app.domain.enums import PurchaseStatus
from app.models import Product, Purchase, Supplier
from app.schemas.purchases import PurchaseCreate, PurchaseItemIn
from app.services.purchases import cancel_purchase, create_purchase, update_purchase


@pytest.fixture()
def pg_db():
    if engine.dialect.name != "postgresql":
        pytest.skip("DATABASE_URL no apunta a PostgreSQL; se omite la prueba de locking real.")
    try:
        with engine.connect():
            pass
    except OperationalError:
        pytest.skip("No hay una instancia de PostgreSQL alcanzable en DATABASE_URL.")

    with Session(engine, expire_on_commit=False) as db:
        tag = uuid4().hex[:10]
        supplier = Supplier(name=f"PGLOCK-TEST-{tag}", active=True)
        product = Product(barcode=f"PGLOCK-{tag}", name=f"PGLOCK-TEST producto {tag}",
                          sale_price=Decimal("1000"), unit_cost=Decimal("500"))
        db.add_all([supplier, product])
        db.commit()
        supplier_id, product_id = supplier.id, product.id
        try:
            yield db, supplier, product
        finally:
            for purchase in db.scalars(select(Purchase).where(Purchase.supplier_id == supplier_id)).all():
                db.delete(purchase)
            db.flush()
            leftover_product = db.get(Product, product_id)
            if leftover_product:
                db.delete(leftover_product)
            leftover_supplier = db.get(Supplier, supplier_id)
            if leftover_supplier:
                db.delete(leftover_supplier)
            db.commit()


def _draft(db, supplier, product, invoice_number, quantity="1"):
    return create_purchase(db, PurchaseCreate(
        supplier_id=supplier.id, invoice_number=invoice_number,
        items=[PurchaseItemIn(product_id=product.id, quantity=Decimal(quantity), unit_cost=Decimal("500"))]))


def test_update_purchase_does_not_hit_for_update_outer_join_error(pg_db):
    """Regression guard: update_purchase() must lock only the purchases row."""
    db, supplier, product = pg_db
    purchase = _draft(db, supplier, product, "PGLOCK-UPDATE")
    updated = update_purchase(db, purchase.id, PurchaseCreate(
        supplier_id=supplier.id, invoice_number="PGLOCK-UPDATE-EDITADA",
        items=[PurchaseItemIn(product_id=product.id, quantity=Decimal("2"), unit_cost=Decimal("500"))]))
    assert updated.status == PurchaseStatus.DRAFT
    assert updated.invoice_number == "PGLOCK-UPDATE-EDITADA"
    assert updated.items[0].quantity == Decimal("2")


def test_cancel_purchase_does_not_hit_for_update_outer_join_error(pg_db):
    """Regression guard: cancel_purchase() must lock only the purchases row."""
    db, supplier, product = pg_db
    purchase = _draft(db, supplier, product, "PGLOCK-CANCEL")
    cancelled = cancel_purchase(db, purchase.id)
    assert cancelled.status == PurchaseStatus.CANCELLED
