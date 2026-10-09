from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.domain.enums import SaleStatus
from app.models import CashSession, Product, Sale
from app.schemas.pos import (AddItemIn, CashSessionOut, CheckoutIn, GranelSettlementIn, ManualItemIn, OpenSessionIn,
                             ProductOut, QuantityIn, SaleCustomerIn, SaleOut)
from app.services.barcodes import barcode_candidates
from app.services.inventory import stock_subquery
from app.services.pos import (add_item, add_manual_item, cancel_sale, checkout, create_sale, get_operator,
                              set_granel_settlement, set_sale_customer)
from app.api.inventory import router as inventory_router
from app.api.inventory_count import router as inventory_count_router
from app.api.purchases import router as purchases_router
from app.api.pricing import router as pricing_router
from app.api.payments import router as payments_router
from app.api.cash import router as cash_router
from app.api.operations import router as operations_router
from app.api.products import router as products_router
from app.api.suppliers import router as suppliers_router
from app.api.reports import router as reports_router
from app.api.electronic_invoices import router as electronic_invoices_router
from app.api.customers import router as customers_router
from app.api.invoicing_ops import router as invoicing_ops_router
from app.api.alegra_catalog import router as alegra_catalog_router
from app.api.physical_audits import router as physical_audits_router
from app.api.post_sale import router as post_sale_router
from app.api.patrimony import router as patrimony_router

router = APIRouter()
router.include_router(inventory_count_router)
router.include_router(inventory_router)
router.include_router(suppliers_router)
router.include_router(purchases_router)
router.include_router(pricing_router)
router.include_router(payments_router)
router.include_router(cash_router)
router.include_router(operations_router)
router.include_router(products_router)
router.include_router(reports_router)
router.include_router(electronic_invoices_router)
router.include_router(customers_router)
router.include_router(alegra_catalog_router)
router.include_router(physical_audits_router)
router.include_router(post_sale_router)
router.include_router(patrimony_router)
router.include_router(invoicing_ops_router)  # after electronic_invoices: its GET /{invoice_id} is a catch-all


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/products", response_model=list[ProductOut])
def products(q: str = Query(default="", max_length=100), db: Session = Depends(get_db)):
    stocks = stock_subquery()
    stmt = (select(Product, func.coalesce(stocks.c.current_stock, 0).label("stock"))
            .outerjoin(stocks, stocks.c.product_id == Product.id)
            .order_by(Product.name).limit(20))
    stmt = stmt.where(Product.active.is_(True))
    if q:
        term = f"%{q.strip()}%"
        stmt = stmt.where(or_(Product.barcode.in_(barcode_candidates(q)), Product.barcode.ilike(term),
                              Product.name.ilike(term)))
    return [ProductOut(id=p.id, barcode=p.barcode, name=p.name, sale_price=p.sale_price, stock=s)
            for p, s in db.execute(stmt)]


@router.get("/sales/open", response_model=list[SaleOut])
def open_sales(db: Session = Depends(get_db)):
    return db.scalars(select(Sale).where(Sale.status == SaleStatus.OPEN).order_by(Sale.created_at)).unique().all()


@router.post("/sales", response_model=SaleOut, status_code=201)
def new_sale(db: Session = Depends(get_db)):
    return create_sale(db)


@router.post("/sales/{sale_id}/items", response_model=SaleOut)
def scan(sale_id: UUID, body: AddItemIn, db: Session = Depends(get_db)):
    return add_item(db, sale_id, body.barcode, body.quantity, product_id=body.product_id)


@router.post("/sales/{sale_id}/manual-items", response_model=SaleOut)
def add_manual(sale_id: UUID, body: ManualItemIn, db: Session = Depends(get_db)):
    """"Producto manual" (Súper / comida a granel / bulto) into the OPEN sale. Never a checkout."""
    return add_manual_item(db, sale_id, body)


@router.put("/sales/{sale_id}/granel-settlement", response_model=SaleOut)
def granel_settlement(sale_id: UUID, body: GranelSettlementIn, db: Session = Depends(get_db)):
    """"Báscula / efectivo" (EXTERNAL_SCALE) or "Pago electrónico" (POS) for the sale's comida a granel."""
    return set_granel_settlement(db, sale_id, body.settlement)


@router.patch("/sales/{sale_id}/items/{item_id}", response_model=SaleOut)
def change_quantity(sale_id: UUID, item_id: UUID, body: QuantityIn, db: Session = Depends(get_db)):
    sale = db.get(Sale, sale_id)
    if not sale or sale.status != SaleStatus.OPEN:
        raise HTTPException(404, "Venta abierta no encontrada")
    item = next((i for i in sale.items if i.id == item_id), None)
    if not item:
        raise HTTPException(404, "Artículo no encontrado")
    item.quantity = body.quantity
    item.line_total = item.quantity * item.unit_price
    sale.total = sum((i.line_total for i in sale.items), Decimal("0"))
    db.commit(); db.refresh(sale)
    return sale


@router.delete("/sales/{sale_id}/items/{item_id}", response_model=SaleOut)
def remove_item(sale_id: UUID, item_id: UUID, db: Session = Depends(get_db)):
    sale = db.get(Sale, sale_id)
    if not sale or sale.status != SaleStatus.OPEN:
        raise HTTPException(404, "Venta abierta no encontrada")
    item = next((i for i in sale.items if i.id == item_id), None)
    if not item:
        raise HTTPException(404, "Artículo no encontrado")
    sale.items.remove(item)
    sale.total = sum((i.line_total for i in sale.items), Decimal("0"))
    db.commit(); db.refresh(sale)
    return sale


@router.post("/sales/{sale_id}/cancel", response_model=SaleOut)
def cancel(sale_id: UUID, db: Session = Depends(get_db)):
    return cancel_sale(db, sale_id)


@router.put("/sales/{sale_id}/customer", response_model=SaleOut)
def assign_customer(sale_id: UUID, body: SaleCustomerIn, db: Session = Depends(get_db)):
    return set_sale_customer(db, sale_id, body.customer_id)


@router.post("/sales/{sale_id}/checkout", response_model=SaleOut)
def finish(sale_id: UUID, body: CheckoutIn, background: BackgroundTasks, db: Session = Depends(get_db)):
    sale = checkout(db, sale_id, body)
    from app.services.electronic_invoicing import schedule_auto_issue
    schedule_auto_issue(background, sale)
    return sale


@router.get("/cash-sessions/current", response_model=CashSessionOut | None)
def current_session(db: Session = Depends(get_db)):
    return db.scalar(select(CashSession).where(CashSession.closed_at.is_(None)))


@router.post("/cash-sessions/open", response_model=CashSessionOut, status_code=201)
def open_session(body: OpenSessionIn, db: Session = Depends(get_db)):
    if db.scalar(select(CashSession).where(CashSession.closed_at.is_(None))):
        raise HTTPException(409, "Ya existe una sesión de caja abierta")
    session = CashSession(opening_amount=body.opening_amount, opened_by=get_operator(db).id)
    db.add(session); db.commit(); db.refresh(session)
    return session
