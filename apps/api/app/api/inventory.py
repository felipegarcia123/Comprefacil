from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.orm import Session
from typing import Literal

from app.core.db import get_db
from app.domain.enums import InventoryMovementType
from app.models import InventoryMovement, OperationalCategory, Product
from app.schemas.inventory import (InventoryAdjustmentIn, InventoryAdjustmentOut,
                                   InventoryMovementOut, InventoryPageOut,
                                   InventoryProductDetailOut, InventoryProductOut)
from app.services.inventory import (LOW_STOCK_THRESHOLD, create_adjustment,
                                    inventory_products_query, movements_query,
                                    stock_expression)
from app.services.pricing import gross_margin_percent

router = APIRouter(prefix="/inventory", tags=["inventory"])


def estimated_margin(sale_price: Decimal, unit_cost: Decimal) -> Decimal | None:
    return gross_margin_percent(sale_price, unit_cost)


def movement_out(movement: InventoryMovement, product_name: str) -> InventoryMovementOut:
    return InventoryMovementOut(
        id=movement.id, product_id=movement.product_id, product_name=product_name,
        movement_type=movement.movement_type, quantity=movement.quantity,
        unit_cost=movement.unit_cost, reference_id=movement.reference_id,
        reason=movement.reason, created_at=movement.created_at, user_id=movement.user_id)


SortField = Literal["name", "stock", "cost", "price", "margin"]


def _inventory_predicates(q: str, category_id: UUID | None, supplier_id: UUID | None,
                          active: bool | None, out_of_stock: bool, low_stock: bool,
                          balance) -> list:
    """Shared WHERE clauses for both the count query and the page query — kept in one place so they can never drift apart."""
    predicates = []
    if q:
        term = f"%{q.strip()}%"
        predicates.append(or_(Product.name.ilike(term), Product.barcode.ilike(term),
                              OperationalCategory.name.ilike(term)))
    if category_id:
        predicates.append(Product.operational_category_id == category_id)
    if supplier_id:
        predicates.append(Product.supplier_id == supplier_id)
    if active is not None:
        predicates.append(Product.active == active)
    if out_of_stock:
        predicates.append(balance <= 0)
    elif low_stock:
        predicates.append(balance > 0)
        predicates.append(balance <= Product.minimum_stock)
    return predicates


@router.get("", response_model=InventoryPageOut)
def list_inventory(q: str = Query(default="", max_length=100),
                   low_stock: bool = False, out_of_stock: bool = False,
                   category_id: UUID | None = None, supplier_id: UUID | None = None,
                   active: bool | None = None,
                   sort: SortField = "name", sort_dir: Literal["asc", "desc"] = "asc",
                   page: int = Query(default=1, ge=1),
                   page_size: int = Query(default=50, ge=1, le=100),
                   db: Session = Depends(get_db)):
    """
    Server-side paginated + filtered + sorted listing. Never loads the full ~7 500-product
    catalog: the page query only ever selects `page_size` rows, and the count query is a
    lightweight `COUNT` that skips the ledger aggregation entirely unless a stock filter needs it
    (see `_inventory_predicates`/`balance`: a correlated scalar subquery, independent from the
    `stock_subquery()` GROUP BY join that `inventory_products_query()` uses only for display).
    """
    balance = (select(stock_expression()).where(
        InventoryMovement.product_id == Product.id).correlate(Product).scalar_subquery())
    predicates = _inventory_predicates(q, category_id, supplier_id, active, out_of_stock, low_stock, balance)

    count_stmt: Select = (select(func.count(Product.id)).select_from(Product)
        .outerjoin(OperationalCategory, OperationalCategory.id == Product.operational_category_id))
    total = db.scalar(count_stmt.where(*predicates)) or 0
    total_pages = max(1, -(-total // page_size))  # ceil division

    stmt = inventory_products_query().where(*predicates)
    margin_expr = case((Product.sale_price > 0, (Product.sale_price - Product.unit_cost) / Product.sale_price), else_=None)
    columns = {"name": Product.name, "stock": balance, "cost": Product.unit_cost,
              "price": Product.sale_price, "margin": margin_expr}
    order_column = columns[sort]
    stmt = stmt.order_by(order_column.desc() if sort_dir == "desc" else order_column.asc(), Product.name)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = db.execute(stmt).all()
    items = [InventoryProductOut(id=p.id, barcode=p.barcode, name=p.name,
                                sale_price=p.sale_price, unit_cost=p.unit_cost,
                                last_purchase_cost=last_cost,
                                estimated_margin_percent=estimated_margin(p.sale_price, p.unit_cost),
                                current_stock=stock, supplier=supplier,
                                supplier_id=p.supplier_id, category=category,
                                category_id=p.operational_category_id,
                                minimum_stock=p.minimum_stock, unit=p.unit,
                                active=p.active, description=p.description)
            for p, supplier, category, stock, last_cost in rows]
    return InventoryPageOut(items=items, total=total, page=page, page_size=page_size, total_pages=total_pages)


@router.get("/movements", response_model=list[InventoryMovementOut])
def list_movements(product_id: UUID | None = None,
                   movement_type: InventoryMovementType | None = None,
                   date_from: datetime | None = None, date_to: datetime | None = None,
                   db: Session = Depends(get_db)):
    stmt = movements_query()
    if product_id:
        stmt = stmt.where(InventoryMovement.product_id == product_id)
    if movement_type:
        stmt = stmt.where(InventoryMovement.movement_type == movement_type)
    if date_from:
        stmt = stmt.where(InventoryMovement.created_at >= date_from)
    if date_to:
        stmt = stmt.where(InventoryMovement.created_at <= date_to)
    rows = db.execute(stmt.order_by(InventoryMovement.created_at.desc()).limit(500)).all()
    return [movement_out(m, name) for m, name in rows]


@router.post("/adjustments", response_model=InventoryAdjustmentOut, status_code=201)
def adjustment(body: InventoryAdjustmentIn, db: Session = Depends(get_db)):
    movement, product_name, balance = create_adjustment(
        db, body.product_id, body.quantity_delta, body.reason)
    return InventoryAdjustmentOut(movement=movement_out(movement, product_name),
                                  current_stock=balance)


@router.get("/{product_id}", response_model=InventoryProductDetailOut)
def inventory_detail(product_id: UUID, db: Session = Depends(get_db)):
    row = db.execute(inventory_products_query().where(Product.id == product_id)).first()
    if not row:
        raise HTTPException(404, "Producto no encontrado")
    product, supplier, category, balance, last_cost = row
    recent = db.execute(movements_query().where(
        InventoryMovement.product_id == product_id).order_by(
            InventoryMovement.created_at.desc()).limit(20)).all()
    return InventoryProductDetailOut(
        id=product.id, barcode=product.barcode, name=product.name,
        sale_price=product.sale_price, unit_cost=product.unit_cost,
        last_purchase_cost=last_cost,
        estimated_margin_percent=estimated_margin(product.sale_price, product.unit_cost),
        supplier_id=product.supplier_id, supplier=supplier,
        category_id=product.operational_category_id, category=category,
        minimum_stock=product.minimum_stock, unit=product.unit,
        active=product.active, description=product.description,
        current_stock=balance,
        recent_movements=[movement_out(m, name) for m, name in recent])
