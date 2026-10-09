from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.domain.enums import InventoryMovementType, PurchaseStatus
from app.models import (InventoryMovement, OperationalCategory, Product, Purchase,
                        PurchaseItem, Supplier)
from app.services.pos import get_operator
from app.services.operational_session import require_open_session


LOW_STOCK_THRESHOLD = Decimal("5")


def stock_expression():
    """Canonical SQL expression for the signed inventory ledger balance."""
    return func.coalesce(func.sum(InventoryMovement.quantity), 0)


def stock_subquery():
    return (select(InventoryMovement.product_id,
                   stock_expression().label("current_stock"))
            .group_by(InventoryMovement.product_id).subquery())


def current_stock(db: Session, product_id: UUID) -> Decimal:
    value = db.scalar(select(stock_expression()).where(
        InventoryMovement.product_id == product_id))
    return Decimal(value or 0)


def inventory_products_query() -> Select:
    stocks = stock_subquery()
    last_purchase_cost = (select(PurchaseItem.unit_cost)
        .join(Purchase, Purchase.id == PurchaseItem.purchase_id)
        .where(PurchaseItem.product_id == Product.id,
               Purchase.status == PurchaseStatus.RECEIVED)
        .order_by(Purchase.received_at.desc(), Purchase.created_at.desc())
        .limit(1).correlate(Product).scalar_subquery())
    return (select(Product, Supplier.name.label("supplier"),
                   OperationalCategory.name.label("category"),
                   func.coalesce(stocks.c.current_stock, 0).label("current_stock"),
                   last_purchase_cost.label("last_purchase_cost"))
            .outerjoin(Supplier, Supplier.id == Product.supplier_id)
            .outerjoin(OperationalCategory, OperationalCategory.id == Product.operational_category_id)
            .outerjoin(stocks, stocks.c.product_id == Product.id))


def movements_query() -> Select:
    return (select(InventoryMovement, Product.name.label("product_name"))
            .join(Product, Product.id == InventoryMovement.product_id))


def build_adjustment(product: Product, quantity_delta: Decimal, reason: str, user_id: UUID,
                     reference_id: UUID | None = None) -> InventoryMovement:
    """Single construction point for ADJUSTMENT ledger rows (manual and physical count)."""
    return InventoryMovement(
        product_id=product.id, movement_type=InventoryMovementType.ADJUSTMENT,
        quantity=quantity_delta, unit_cost=product.unit_cost,
        reference_id=reference_id, reason=reason.strip(), user_id=user_id)


def create_adjustment(db: Session, product_id: UUID, quantity_delta: Decimal,
                      reason: str) -> tuple[InventoryMovement, str, Decimal]:
    require_open_session(db, lock=True)
    if quantity_delta == 0:
        raise HTTPException(422, "La cantidad del ajuste no puede ser cero")
    try:
        product = db.scalar(select(Product).where(Product.id == product_id).with_for_update(of=Product))
        if not product:
            raise HTTPException(404, "Producto no encontrado")
        balance = current_stock(db, product_id)
        resulting_stock = balance + quantity_delta
        if resulting_stock < 0:
            raise HTTPException(409, "El ajuste dejaría el inventario con stock negativo")
        movement = build_adjustment(product, quantity_delta, reason, get_operator(db).id)
        db.add(movement)
        db.commit()
        db.refresh(movement)
        return movement, product.name, resulting_stock
    except Exception:
        db.rollback()
        raise
