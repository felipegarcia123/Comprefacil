from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.domain.enums import PurchaseStatus
from app.models import Product, Purchase, Supplier
from app.schemas.purchases import (CommitmentsOut, PurchaseAgendaItemOut, PurchaseCreate, PurchaseItemOut,
                                   PurchaseOut, ReceiveComparisonIn, ReceivePurchaseIn, SchedulePurchaseIn, SupplierPaymentIn,
                                   SupplierPaymentOut)
from app.services.purchases import (cancel_purchase, create_purchase, receive_purchase,
                                    update_purchase, register_supplier_payment, receive_purchase_with_payment,
                                    schedule_purchase, purchase_agenda, purchase_commitments)

router = APIRouter(prefix="/purchases", tags=["purchases"])


def purchase_out(db: Session, purchase: Purchase) -> PurchaseOut:
    supplier = db.get(Supplier, purchase.supplier_id)
    product_ids = [item.product_id for item in purchase.items]
    products = {product.id: product for product in db.scalars(
        select(Product).where(Product.id.in_(product_ids))).all()} if product_ids else {}
    payment = purchase.payment
    return PurchaseOut(
        id=purchase.id, supplier_id=purchase.supplier_id,
        supplier_name=supplier.name if supplier else "Proveedor no disponible",
        status=purchase.status, invoice_number=purchase.invoice_number,
        purchased_at=purchase.purchased_at, received_at=purchase.received_at,
        notes=purchase.notes, subtotal=purchase.subtotal, total=purchase.total,
        created_at=purchase.created_at, created_by=purchase.created_by,
        scheduled_at=purchase.scheduled_at, expected_delivery_date=purchase.expected_delivery_date,
        payment_terms=purchase.payment_terms, payment_due_date=purchase.payment_due_date,
        estimated_total=purchase.estimated_total, offer_notes=purchase.offer_notes,
        paid_amount=payment.amount if payment else Decimal("0"),
        outstanding_amount=purchase.total - (payment.amount if payment else Decimal("0"))
                           if purchase.status == PurchaseStatus.RECEIVED else None,
        payment=SupplierPaymentOut(id=payment.id, purchase_id=payment.purchase_id,
            supplier_id=payment.supplier_id, amount=payment.amount, payment_channel_id=payment.payment_channel_id,
            channel_name=payment.channel.name, payment_type=payment.channel.payment_type,
            affects_cash=payment.channel.affects_cash, reference=payment.reference, notes=payment.notes,
            paid_at=payment.paid_at) if payment else None,
        items=[PurchaseItemOut(
            id=item.id, product_id=item.product_id,
            product_name=products[item.product_id].name,
            barcode=products[item.product_id].barcode,
            quantity=item.quantity, unit_cost=item.unit_cost,
            line_total=item.line_total, ordered_quantity=item.ordered_quantity,
            ordered_unit_cost=item.ordered_unit_cost,
            offer_list_unit_cost=item.offer_list_unit_cost,
            offer_discount_per_unit=item.offer_discount_per_unit, offer_note=item.offer_note,
            difference_reason=item.difference_reason,
            actual_unit_cost=item.unit_cost if purchase.status == PurchaseStatus.RECEIVED and item.quantity > 0 else None,
            quantity_difference=(item.quantity - item.ordered_quantity) if purchase.status == PurchaseStatus.RECEIVED
                                and item.ordered_quantity is not None else None,
            unit_cost_difference=(item.unit_cost - item.ordered_unit_cost) if purchase.status == PurchaseStatus.RECEIVED
                                 and item.quantity > 0 and item.ordered_unit_cost is not None else None,
            amount_difference=(item.line_total - (item.ordered_quantity * item.ordered_unit_cost).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP))
                              if purchase.status == PurchaseStatus.RECEIVED and item.ordered_quantity is not None
                              and item.ordered_unit_cost is not None else None)
            for item in purchase.items])


@router.get("", response_model=list[PurchaseOut])
def list_purchases(q: str = Query(default="", max_length=100),
                   status: PurchaseStatus | None = None, db: Session = Depends(get_db)):
    stmt = select(Purchase).join(Supplier, Supplier.id == Purchase.supplier_id)
    if q:
        term = f"%{q.strip()}%"
        stmt = stmt.where(or_(Supplier.name.ilike(term), Purchase.invoice_number.ilike(term)))
    if status:
        stmt = stmt.where(Purchase.status == status)
    purchases = db.scalars(stmt.order_by(Purchase.created_at.desc()).limit(200)).unique().all()
    return [purchase_out(db, purchase) for purchase in purchases]


@router.post("", response_model=PurchaseOut, status_code=201)
def new_purchase(body: PurchaseCreate, db: Session = Depends(get_db),
                 idempotency_key: UUID | None = Header(default=None, alias="Idempotency-Key")):
    return purchase_out(db, create_purchase(db, body, request_id=idempotency_key))


@router.get("/agenda", response_model=list[PurchaseAgendaItemOut])
def agenda(as_of: date | None = None, horizon_days: int = Query(default=30, ge=0, le=365),
           db: Session = Depends(get_db)):
    return purchase_agenda(db, as_of=as_of, horizon_days=horizon_days)


@router.get("/commitments", response_model=CommitmentsOut)
def commitments(date_from: date, date_to: date, db: Session = Depends(get_db)):
    return purchase_commitments(db, date_from, date_to)


@router.get("/{purchase_id}", response_model=PurchaseOut)
def purchase_detail(purchase_id: UUID, db: Session = Depends(get_db)):
    purchase = db.get(Purchase, purchase_id)
    if not purchase:
        raise HTTPException(404, "Compra no encontrada")
    return purchase_out(db, purchase)


@router.post("/{purchase_id}/schedule", response_model=PurchaseOut)
def schedule(purchase_id: UUID, body: SchedulePurchaseIn, db: Session = Depends(get_db)):
    return purchase_out(db, schedule_purchase(db, purchase_id, body))


@router.put("/{purchase_id}", response_model=PurchaseOut)
def edit_purchase(purchase_id: UUID, body: PurchaseCreate, db: Session = Depends(get_db)):
    return purchase_out(db, update_purchase(db, purchase_id, body))


@router.post("/{purchase_id}/receive", response_model=PurchaseOut)
def receive(purchase_id: UUID, body: ReceiveComparisonIn | None = None, db: Session = Depends(get_db)):
    return purchase_out(db, receive_purchase(db, purchase_id, body))

@router.post("/{purchase_id}/receive-with-payment", response_model=PurchaseOut, status_code=201)
def receive_with_payment(purchase_id: UUID, body: ReceivePurchaseIn, db: Session = Depends(get_db)):
    return purchase_out(db, receive_purchase_with_payment(db, purchase_id, body))

@router.post("/{purchase_id}/payment", response_model=PurchaseOut, status_code=201)
def pay(purchase_id: UUID, body: SupplierPaymentIn, db: Session = Depends(get_db)):
    return purchase_out(db, register_supplier_payment(db, purchase_id, body))

@router.get("/{purchase_id}/payment", response_model=SupplierPaymentOut)
def payment(purchase_id: UUID, db: Session = Depends(get_db)):
    purchase = db.get(Purchase, purchase_id)
    if not purchase or not purchase.payment:
        raise HTTPException(404, "Pago de proveedor no encontrado")
    p = purchase.payment
    return SupplierPaymentOut(id=p.id, purchase_id=p.purchase_id, supplier_id=p.supplier_id, amount=p.amount,
        payment_channel_id=p.payment_channel_id, channel_name=p.channel.name,
        payment_type=p.channel.payment_type, affects_cash=p.channel.affects_cash,
        reference=p.reference, notes=p.notes, paid_at=p.paid_at)


@router.post("/{purchase_id}/cancel", response_model=PurchaseOut)
def cancel(purchase_id: UUID, db: Session = Depends(get_db)):
    return purchase_out(db, cancel_purchase(db, purchase_id))
