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
from datetime import date
from decimal import Decimal
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.db import engine
from app.domain.enums import PaymentMethod, PurchaseStatus
from app.models import CashMovement, CashSession, InventoryMovement, PaymentChannel, Product, Purchase, Supplier, SupplierPayment, User
from app.schemas.purchases import PurchaseCreate, PurchaseItemIn, ReceiveComparisonIn, ReceivePurchaseIn, SchedulePurchaseIn
from app.services.purchases import (cancel_purchase, create_purchase, receive_purchase,
                                    receive_purchase_with_payment, schedule_purchase, update_purchase)


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
            db.execute(delete(InventoryMovement).where(InventoryMovement.product_id == product_id))
            for purchase in db.scalars(select(Purchase).where(Purchase.supplier_id == supplier_id)).all():
                db.delete(purchase)
            db.flush()
            leftover_product = db.get(Product, product_id)
            if leftover_product:
                db.delete(leftover_product)
                db.flush()
            leftover_supplier = db.get(Supplier, supplier_id)
            if leftover_supplier:
                db.delete(leftover_supplier)
            db.commit()


def _draft(db, supplier, product, invoice_number, quantity="1"):
    return create_purchase(db, PurchaseCreate(
        supplier_id=supplier.id, invoice_number=invoice_number,
        items=[PurchaseItemIn(product_id=product.id, quantity=Decimal(quantity), unit_cost=Decimal("500"))]))


def test_scheduled_purchase_lock_and_constraints_on_postgresql(pg_db):
    db, supplier, product = pg_db
    purchase = _draft(db, supplier, product, "PGLOCK-SCHEDULE")
    body = SchedulePurchaseIn(expected_delivery_date=date(2026, 12, 10), payment_terms="CREDIT",
                              payment_due_date=date(2027, 1, 15))
    scheduled = schedule_purchase(db, purchase.id, body)
    assert scheduled.status.value == "SCHEDULED"
    assert scheduled.items[0].ordered_quantity == Decimal("1")
    assert scheduled.items[0].ordered_unit_cost == Decimal("500")
    assert schedule_purchase(db, purchase.id, body).scheduled_at == scheduled.scheduled_at


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


def test_concurrent_schedule_same_purchase_is_idempotent_on_postgresql(pg_db):
    db, supplier, product = pg_db
    purchase = _draft(db, supplier, product, "PG-CONCURRENT-SCHEDULE")
    body = SchedulePurchaseIn(expected_delivery_date=date(2026, 12, 10), payment_terms="CREDIT",
                              payment_due_date=date(2027, 1, 15))
    barrier = Barrier(2)

    def worker():
        with Session(engine, expire_on_commit=False) as session:
            barrier.wait(timeout=10)
            result = schedule_purchase(session, purchase.id, body)
            return result.id, result.scheduled_at

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))
    assert results[0] == results[1]
    db.expire_all()
    assert db.get(Purchase, purchase.id).status == PurchaseStatus.SCHEDULED


def test_concurrent_receipt_same_request_moves_inventory_once_on_postgresql(pg_db):
    db, supplier, product = pg_db
    user = db.scalar(select(User).limit(1))
    cash_session = CashSession(opening_amount=Decimal("0"), opened_by=user.id)
    db.add(cash_session); db.commit()
    purchase = _draft(db, supplier, product, "PG-CONCURRENT-RECEIPT")
    schedule_purchase(db, purchase.id, SchedulePurchaseIn(
        expected_delivery_date=date(2026, 12, 10), payment_terms="CREDIT",
        payment_due_date=date(2027, 1, 15)))
    body = ReceiveComparisonIn(request_id=uuid4(), items=[{
        "product_id": product.id, "received_quantity": Decimal("2"),
        "actual_unit_cost": Decimal("550"), "difference_reason": "Precio modificado"}])
    barrier = Barrier(2)

    def worker():
        with Session(engine, expire_on_commit=False) as session:
            barrier.wait(timeout=10)
            result = receive_purchase(session, purchase.id, body)
            return result.id, result.received_at

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        assert results[0] == results[1]
        assert db.scalar(select(func.count()).select_from(InventoryMovement).where(
            InventoryMovement.reference_id == purchase.id)) == 1
        db.expire_all()
        assert db.get(Purchase, purchase.id).total == Decimal("1100")
    finally:
        db.execute(delete(CashSession).where(CashSession.id == cash_session.id))
        db.commit()


def test_concurrent_create_same_key_makes_one_draft_on_postgresql(pg_db):
    db, supplier, product = pg_db
    request_id = uuid4()
    body = PurchaseCreate(supplier_id=supplier.id, invoice_number=None,
                          items=[PurchaseItemIn(product_id=product.id,
                                                quantity=Decimal("1"), unit_cost=Decimal("500"))])
    barrier = Barrier(2)

    def worker():
        with Session(engine, expire_on_commit=False) as session:
            barrier.wait(timeout=10)
            return create_purchase(session, body, request_id=request_id).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))
    assert results[0] == results[1]
    assert db.scalar(select(func.count()).select_from(Purchase).where(
        Purchase.supplier_id == supplier.id)) == 1


def test_concurrent_cash_receipt_has_one_payment_and_cash_movement_on_postgresql(pg_db):
    db, supplier, product = pg_db
    user = db.scalar(select(User).limit(1))
    cash_session = CashSession(opening_amount=Decimal("100000"), opened_by=user.id)
    channel = PaymentChannel(name=f"E2 cash {uuid4().hex[:8]}",
                             payment_type=PaymentMethod.CASH, affects_cash=True)
    db.add_all([cash_session, channel]); db.commit()
    purchase = _draft(db, supplier, product, "PG-CONCURRENT-PAID")
    schedule_purchase(db, purchase.id, SchedulePurchaseIn(
        expected_delivery_date=date(2026, 12, 10), payment_terms="CASH_ON_DELIVERY"))
    body = ReceivePurchaseIn(payment_channel_id=channel.id, comparison=ReceiveComparisonIn(
        request_id=uuid4(), items=[{"product_id": product.id,
                                    "received_quantity": Decimal("1"),
                                    "actual_unit_cost": Decimal("500")}]))
    barrier = Barrier(2)

    def worker():
        with Session(engine, expire_on_commit=False) as session:
            barrier.wait(timeout=10)
            return receive_purchase_with_payment(session, purchase.id, body).id

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        assert results[0] == results[1]
        assert db.scalar(select(func.count()).select_from(InventoryMovement).where(
            InventoryMovement.reference_id == purchase.id)) == 1
        assert db.scalar(select(func.count()).select_from(SupplierPayment).where(
            SupplierPayment.purchase_id == purchase.id)) == 1
        assert db.scalar(select(func.count()).select_from(CashMovement).where(
            CashMovement.reference_id == purchase.id)) == 1
    finally:
        db.execute(delete(CashMovement).where(CashMovement.reference_id == purchase.id))
        db.execute(delete(SupplierPayment).where(SupplierPayment.purchase_id == purchase.id))
        db.execute(delete(CashSession).where(CashSession.id == cash_session.id))
        db.execute(delete(PaymentChannel).where(PaymentChannel.id == channel.id))
        db.commit()
