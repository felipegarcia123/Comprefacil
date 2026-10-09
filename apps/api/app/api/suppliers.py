from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.domain.enums import PurchaseStatus
from app.models import Purchase, Supplier
from app.schemas.suppliers import (SupplierActiveIn, SupplierCreate, SupplierDetailOut,
                                   SupplierOut, SupplierPurchaseSummary, SupplierUpdate)

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


def ensure_unique(db: Session, body: SupplierCreate | SupplierUpdate,
                  exclude_id: UUID | None = None):
    stmt = select(Supplier).where(func.lower(Supplier.name) == body.name.lower())
    if exclude_id:
        stmt = stmt.where(Supplier.id != exclude_id)
    if db.scalar(stmt):
        raise HTTPException(409, "Ya existe un proveedor con ese nombre")
    if body.document:
        stmt = select(Supplier).where(Supplier.document == body.document)
        if exclude_id:
            stmt = stmt.where(Supplier.id != exclude_id)
        if db.scalar(stmt):
            raise HTTPException(409, "Ya existe un proveedor con ese documento")


@router.get("", response_model=list[SupplierOut])
def list_suppliers(q: str = Query(default="", max_length=100), active: bool | None = None,
                   db: Session = Depends(get_db)):
    stmt = select(Supplier)
    if q:
        term = f"%{q.strip()}%"
        stmt = stmt.where(or_(Supplier.name.ilike(term), Supplier.document.ilike(term),
                              Supplier.contact_name.ilike(term)))
    if active is not None:
        stmt = stmt.where(Supplier.active == active)
    return db.scalars(stmt.order_by(Supplier.name)).all()


@router.post("", response_model=SupplierOut, status_code=201)
def create_supplier(body: SupplierCreate, db: Session = Depends(get_db)):
    ensure_unique(db, body)
    supplier = Supplier(**body.model_dump())
    db.add(supplier); db.commit(); db.refresh(supplier)
    return supplier


@router.get("/{supplier_id}", response_model=SupplierDetailOut)
def supplier_detail(supplier_id: UUID, db: Session = Depends(get_db)):
    supplier = db.get(Supplier, supplier_id)
    if not supplier:
        raise HTTPException(404, "Proveedor no encontrado")
    purchases = db.scalars(select(Purchase).where(Purchase.supplier_id == supplier_id)
                           .order_by(Purchase.purchased_at.desc())).unique().all()
    received = [purchase for purchase in purchases if purchase.status == PurchaseStatus.RECEIVED]
    return SupplierDetailOut(
        **SupplierOut.model_validate(supplier).model_dump(),
        purchases=[SupplierPurchaseSummary(id=p.id, status=p.status.value,
                   invoice_number=p.invoice_number, purchased_at=p.purchased_at,
                   received_at=p.received_at, total=p.total) for p in purchases],
        total_purchased=sum((p.total for p in received), Decimal("0")),
        last_purchase_at=received[0].received_at if received else None)


@router.put("/{supplier_id}", response_model=SupplierOut)
def update_supplier(supplier_id: UUID, body: SupplierUpdate, db: Session = Depends(get_db)):
    supplier = db.get(Supplier, supplier_id)
    if not supplier:
        raise HTTPException(404, "Proveedor no encontrado")
    ensure_unique(db, body, supplier_id)
    for key, value in body.model_dump().items():
        setattr(supplier, key, value)
    db.commit(); db.refresh(supplier)
    return supplier


@router.patch("/{supplier_id}/active", response_model=SupplierOut)
def set_supplier_active(supplier_id: UUID, body: SupplierActiveIn,
                        db: Session = Depends(get_db)):
    supplier = db.get(Supplier, supplier_id)
    if not supplier:
        raise HTTPException(404, "Proveedor no encontrado")
    supplier.active = body.active
    db.commit(); db.refresh(supplier)
    return supplier
