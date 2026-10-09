from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Expense, InternalConsumption, OperationalCategory, Product
from app.schemas.operations import (DailyOperationsSummary, ExpenseIn, ExpenseOut,
                                    InternalConsumptionIn, InternalConsumptionOut,
                                    ManualSaleIn, OperationalCategoryOut,
                                    ProductCategoryIn)
from app.schemas.pos import SaleOut
from app.services.operations import (create_expense, create_internal_consumption,
                                     create_manual_sale, daily_summary,
                                     resolve_operational_window)

router = APIRouter(tags=["daily operations"])


@router.get("/operational-categories", response_model=list[OperationalCategoryOut])
def categories(active: bool | None = None, db: Session = Depends(get_db)):
    stmt = select(OperationalCategory)
    if active is not None:
        stmt = stmt.where(OperationalCategory.active == active)
    return db.scalars(stmt.order_by(OperationalCategory.display_order)).all()


@router.patch("/products/{product_id}/operational-category", response_model=OperationalCategoryOut)
def assign_category(product_id: UUID, body: ProductCategoryIn, db: Session = Depends(get_db)):
    product = db.get(Product, product_id); category = db.get(OperationalCategory, body.category_id)
    if not product:
        raise HTTPException(404, "Producto no encontrado")
    if not category or not category.active:
        raise HTTPException(404, "Categoría operativa no encontrada")
    product.operational_category_id = category.id; db.commit(); db.refresh(category)
    return category


@router.post("/sales/manual", response_model=SaleOut, status_code=201)
def manual_sale(body: ManualSaleIn, background: BackgroundTasks, db: Session = Depends(get_db)):
    sale = create_manual_sale(db, body)
    # Idempotent: a repeated request returns the same sale; auto_issue_for_sale only acts on PENDING.
    from app.services.electronic_invoicing import schedule_auto_issue
    schedule_auto_issue(background, sale)
    return sale


@router.post("/expenses", response_model=ExpenseOut, status_code=201)
def add_expense(body: ExpenseIn, db: Session = Depends(get_db)):
    return create_expense(db, body)


@router.get("/expenses", response_model=list[ExpenseOut])
def expenses(date_from: datetime | None = None, date_to: datetime | None = None,
             db: Session = Depends(get_db)):
    stmt = select(Expense)
    if date_from: stmt = stmt.where(Expense.created_at >= date_from)
    if date_to: stmt = stmt.where(Expense.created_at <= date_to)
    return db.scalars(stmt.order_by(Expense.created_at.desc()).limit(200)).all()


@router.post("/internal-consumption", response_model=InternalConsumptionOut, status_code=201)
def add_consumption(body: InternalConsumptionIn, db: Session = Depends(get_db)):
    return create_internal_consumption(db, body)


@router.get("/internal-consumption", response_model=list[InternalConsumptionOut])
def consumptions(date_from: datetime | None = None, date_to: datetime | None = None,
                 db: Session = Depends(get_db)):
    stmt = select(InternalConsumption)
    if date_from: stmt = stmt.where(InternalConsumption.created_at >= date_from)
    if date_to: stmt = stmt.where(InternalConsumption.created_at <= date_to)
    return db.scalars(stmt.order_by(InternalConsumption.created_at.desc()).limit(200)).all()


@router.get("/daily-operations/summary", response_model=DailyOperationsSummary)
def summary(date_from: datetime | None = None, date_to: datetime | None = None,
            db: Session = Depends(get_db)):
    resolved_from, resolved_to, status = resolve_operational_window(db)
    custom = date_from is not None or date_to is not None
    return daily_summary(db, date_from or resolved_from, date_to or resolved_to,
                         session_status="CUSTOM" if custom else status)
