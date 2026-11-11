from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.enums import CashMovementType, InventoryMovementType, PaymentMethod, PurchaseStatus
from app.models import (CashMovement, PaymentChannel, InventoryMovement, Product, Purchase,
                        PurchaseItem, Supplier, SupplierPayment)
from app.schemas.purchases import PurchaseCreate, SupplierPaymentIn
from app.services.cash import current_session, expected_cash
from app.services.operational_session import require_open_session
from app.services.pos import get_operator


def _validate_supplier(db: Session, supplier_id: UUID, require_active: bool = True, lock: bool = False) -> Supplier:
    stmt = select(Supplier).where(Supplier.id == supplier_id)
    if lock:
        stmt = stmt.with_for_update(of=Supplier)
    supplier = db.scalar(stmt)
    if not supplier:
        raise HTTPException(404, "Proveedor no encontrado")
    if require_active and not supplier.active:
        raise HTTPException(409, "El proveedor está inactivo")
    return supplier


def _validated_products(db: Session, body: PurchaseCreate) -> dict[UUID, Product]:
    ids = [item.product_id for item in body.items]
    if len(ids) != len(set(ids)):
        raise HTTPException(422, "Un producto no puede repetirse en la misma compra")
    products = {product.id: product for product in db.scalars(
        select(Product).where(Product.id.in_(ids))).all()}
    missing = set(ids) - set(products)
    if missing:
        raise HTTPException(404, "Uno o más productos no existen")
    return products


def _apply_items(purchase: Purchase, body: PurchaseCreate) -> None:
    purchase.items = [PurchaseItem(
        product_id=item.product_id, quantity=item.quantity,
        unit_cost=item.unit_cost, line_total=item.quantity * item.unit_cost)
        for item in body.items]
    purchase.subtotal = sum((item.line_total for item in purchase.items), Decimal("0"))
    purchase.total = purchase.subtotal


def create_purchase(db: Session, body: PurchaseCreate, *, enforce_invoice_unique: bool = False) -> Purchase:
    supplier = _validate_supplier(db, body.supplier_id, lock=True)
    if enforce_invoice_unique and body.invoice_number:
        duplicate = db.scalar(select(Purchase.id).where(
            Purchase.supplier_id == supplier.id,
            Purchase.invoice_number == body.invoice_number.strip(),
            Purchase.status != PurchaseStatus.CANCELLED,
        ).limit(1))
        if duplicate:
            raise HTTPException(409, "Ya existe una compra con ese número de factura para este proveedor")
    _validated_products(db, body)
    purchase = Purchase(
        supplier_id=body.supplier_id, invoice_number=body.invoice_number.strip() if body.invoice_number else None,
        purchased_at=body.purchased_at or datetime.now(timezone.utc), notes=body.notes,
        created_by=get_operator(db).id, status=PurchaseStatus.DRAFT)
    _apply_items(purchase, body)
    db.add(purchase); db.commit(); db.refresh(purchase)
    return purchase


def update_purchase(db: Session, purchase_id: UUID, body: PurchaseCreate) -> Purchase:
    purchase = db.scalar(select(Purchase).where(Purchase.id == purchase_id).with_for_update(of=Purchase))
    if not purchase:
        raise HTTPException(404, "Compra no encontrada")
    if purchase.status != PurchaseStatus.DRAFT:
        raise HTTPException(409, "Solo se puede modificar una compra en borrador")
    _validate_supplier(db, body.supplier_id)
    _validated_products(db, body)
    purchase.items.clear(); db.flush()
    purchase.supplier_id = body.supplier_id
    purchase.invoice_number = body.invoice_number
    purchase.purchased_at = body.purchased_at or purchase.purchased_at
    purchase.notes = body.notes
    _apply_items(purchase, body)
    db.commit(); db.refresh(purchase)
    return purchase


def receive_purchase(db: Session, purchase_id: UUID) -> Purchase:
    try:
        require_open_session(db, lock=True)
        purchase = db.scalar(select(Purchase).where(Purchase.id == purchase_id).with_for_update(of=Purchase))
        if not purchase:
            raise HTTPException(404, "Compra no encontrada")
        if purchase.status != PurchaseStatus.DRAFT:
            raise HTTPException(409, "La compra ya fue recibida o cancelada")
        if not purchase.items:
            raise HTTPException(422, "No se puede recibir una compra vacía")
        product_ids = [item.product_id for item in purchase.items]
        products = {product.id: product for product in db.scalars(
            select(Product).where(Product.id.in_(product_ids)).with_for_update(of=Product)).all()}
        if len(products) != len(product_ids):
            raise HTTPException(409, "La compra contiene un producto que ya no existe")
        operator = get_operator(db)
        # Last received cost is isolated here so a future valuation strategy can replace it.
        for item in purchase.items:
            product = products[item.product_id]
            db.add(InventoryMovement(
                product_id=item.product_id, movement_type=InventoryMovementType.PURCHASE,
                quantity=item.quantity, unit_cost=item.unit_cost,
                reference_id=purchase.id, reason=None, user_id=operator.id))
            product.unit_cost = item.unit_cost
            product.supplier_id = purchase.supplier_id
        purchase.status = PurchaseStatus.RECEIVED
        purchase.received_at = datetime.now(timezone.utc)
        db.commit(); db.refresh(purchase)
        return purchase
    except Exception:
        db.rollback()
        raise


def cancel_purchase(db: Session, purchase_id: UUID) -> Purchase:
    purchase = db.scalar(select(Purchase).where(Purchase.id == purchase_id).with_for_update(of=Purchase))
    if not purchase:
        raise HTTPException(404, "Compra no encontrada")
    if purchase.status != PurchaseStatus.DRAFT:
        raise HTTPException(409, "Solo se puede cancelar una compra en borrador")
    purchase.status = PurchaseStatus.CANCELLED
    db.commit(); db.refresh(purchase)
    return purchase


def register_supplier_payment(db: Session, purchase_id: UUID, body: SupplierPaymentIn) -> Purchase:
    """One immutable, exact-total payment; cash ledger entry is committed atomically."""
    try:
        require_open_session(db, lock=True)
        purchase = db.scalar(select(Purchase).where(Purchase.id == purchase_id).with_for_update(of=Purchase))
        if not purchase:
            raise HTTPException(404, "Compra no encontrada")
        if purchase.status != PurchaseStatus.RECEIVED:
            raise HTTPException(409, "Solo se puede pagar una compra recibida")
        if db.scalar(select(SupplierPayment.id).where(SupplierPayment.purchase_id == purchase.id)):
            raise HTTPException(409, "Esta compra ya tiene un pago registrado")
        channel = db.get(PaymentChannel, body.payment_channel_id)
        if not channel or not channel.active:
            raise HTTPException(422, "Canal de pago no disponible")
        # Names are presentation data; semantic channel fields drive behaviour.
        if not (channel.affects_cash or channel.payment_type == PaymentMethod.TRANSFER):
            raise HTTPException(422, "Para proveedores solo se permite efectivo o transferencia")
        operator = get_operator(db)
        payment = SupplierPayment(purchase_id=purchase.id, supplier_id=purchase.supplier_id,
            amount=purchase.total, payment_channel_id=channel.id, reference=body.reference,
            notes=body.notes, paid_at=datetime.now(timezone.utc), created_by=operator.id)
        db.add(payment)
        if channel.affects_cash:
            session = current_session(db, lock=True)
            if not session:
                raise HTTPException(409, "No hay una caja abierta para registrar este pago en efectivo.")
            if expected_cash(db, session) < purchase.total:
                raise HTTPException(409, "El efectivo disponible en caja no es suficiente.")
            db.add(CashMovement(cash_session_id=session.id,
                movement_type=CashMovementType.SUPPLIER_PAYMENT, amount=-purchase.total,
                reference_id=purchase.id, notes=f"Pago proveedor - {_validate_supplier(db, purchase.supplier_id).name}",
                user_id=operator.id))
        db.commit(); db.refresh(purchase)
        return purchase
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Esta compra ya tiene un pago registrado")
    except Exception:
        db.rollback()
        raise


def receive_purchase_with_payment(db: Session, purchase_id: UUID, body: SupplierPaymentIn) -> Purchase:
    try:
        session = require_open_session(db, lock=True)
        purchase = db.scalar(select(Purchase).where(Purchase.id == purchase_id).with_for_update(of=Purchase))
        if not purchase: raise HTTPException(404, "Compra no encontrada")
        if purchase.status != PurchaseStatus.DRAFT: raise HTTPException(409, "La compra ya fue recibida o cancelada")
        if not purchase.items: raise HTTPException(422, "No se puede recibir una compra vacía")
        channel = db.get(PaymentChannel, body.payment_channel_id)
        if not channel or not channel.active or not (
                channel.affects_cash or channel.payment_type == PaymentMethod.TRANSFER):
            raise HTTPException(422, "Para proveedores solo se permite efectivo o transferencia")
        operator = get_operator(db)
        products = {p.id:p for p in db.scalars(select(Product).where(Product.id.in_([i.product_id for i in purchase.items])).with_for_update(of=Product)).all()}
        if len(products) != len(purchase.items): raise HTTPException(409, "La compra contiene un producto que ya no existe")
        for item in purchase.items:
            product = products[item.product_id]
            db.add(InventoryMovement(product_id=item.product_id, movement_type=InventoryMovementType.PURCHASE, quantity=item.quantity, unit_cost=item.unit_cost, reference_id=purchase.id, user_id=operator.id))
            product.unit_cost=item.unit_cost; product.supplier_id=purchase.supplier_id
        purchase.status=PurchaseStatus.RECEIVED; purchase.received_at=datetime.now(timezone.utc)
        db.add(SupplierPayment(purchase_id=purchase.id, supplier_id=purchase.supplier_id, amount=purchase.total, payment_channel_id=channel.id, reference=body.reference, notes=body.notes, paid_at=datetime.now(timezone.utc), created_by=operator.id))
        if channel.affects_cash:
            if expected_cash(db, session) < purchase.total: raise HTTPException(409, "El efectivo disponible en caja no es suficiente.")
            db.add(CashMovement(cash_session_id=session.id, movement_type=CashMovementType.SUPPLIER_PAYMENT, amount=-purchase.total, reference_id=purchase.id, notes=f"Pago proveedor - {_validate_supplier(db,purchase.supplier_id).name}", user_id=operator.id))
        db.commit(); db.refresh(purchase); return purchase
    except IntegrityError:
        db.rollback(); raise HTTPException(409, "Esta compra ya tiene un pago registrado")
    except Exception:
        db.rollback(); raise
