"""Synthetic commercial fixture for the optional LLM evaluation; no store data."""

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.domain.enums import InventoryMovementType, PurchasePaymentTerms, PurchaseStatus, SaleStatus
from app.models import InventoryMovement, Product, Purchase, Sale, SaleItem, Supplier, User
from app.services.business_analytics import PosBusinessAnalytics


def synthetic_analytics():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    user = User(name="Operador sintético")
    supplier = Supplier(name="Proveedor sintético")
    product = Product(name="Arroz sintético", sale_price=50, unit_cost=60, minimum_stock=2)
    db.add_all([user, supplier, product])
    db.flush()
    sale = Sale(number="EVAL-2026", status=SaleStatus.COMPLETED, created_by=user.id,
                total=Decimal("100"), completed_at=datetime(2026, 11, 25, 2, tzinfo=timezone.utc))
    db.add(sale)
    db.flush()
    db.add(SaleItem(sale_id=sale.id, product_id=product.id, product_name=product.name,
                    quantity=2, unit_price=50, unit_cost=30, line_total=100))
    db.add(InventoryMovement(product_id=product.id, movement_type=InventoryMovementType.INITIAL_STOCK,
                             quantity=1, unit_cost=60, user_id=user.id))
    for index in range(201):
        db.add(Purchase(supplier_id=supplier.id, status=PurchaseStatus.SCHEDULED,
                        created_by=user.id, scheduled_at=datetime(2026, 11, 24, 12, tzinfo=timezone.utc),
                        expected_delivery_date=date(2026, 11, 24),
                        payment_terms=PurchasePaymentTerms.CREDIT,
                        payment_due_date=date(2026, 11, 24),
                        estimated_total=Decimal("20") if index == 0 else Decimal("0")))
    db.flush()
    return engine, db, PosBusinessAnalytics(db)


def add_uncalculable_sale(db: Session) -> None:
    user = db.query(User).first()
    product = Product(name="Costo desconocido sintético", sale_price=100, unit_cost=0)
    db.add(product)
    db.flush()
    sale = Sale(number="EVAL-2020", status=SaleStatus.COMPLETED, created_by=user.id,
                total=Decimal("100"), completed_at=datetime(2020, 1, 1, 12, tzinfo=timezone.utc))
    db.add(sale)
    db.flush()
    db.add(SaleItem(sale_id=sale.id, product_id=product.id, product_name=product.name,
                    quantity=1, unit_price=100, unit_cost=0, line_total=100))
    db.flush()
