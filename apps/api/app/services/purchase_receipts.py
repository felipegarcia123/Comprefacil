"""One transactional path for legacy and compared purchase receipts."""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.enums import CashMovementType, InventoryMovementType, PaymentMethod, PurchasePaymentTerms, PurchaseStatus
from app.models import CashMovement, InventoryMovement, PaymentChannel, Product, Purchase, Supplier, SupplierPayment
from app.schemas.purchases import ReceiveComparisonIn, SupplierPaymentIn
from app.services.cash import expected_cash
from app.services.operational_session import require_open_session
from app.services.pos import get_operator
from app.services.purchases import _check_invoice_duplicate, _line_total, _receipt_fingerprint, _validate_supplier


def _validate_comparison(purchase: Purchase, comparison: ReceiveComparisonIn) -> None:
    planned = {item.product_id: item for item in purchase.items}
    received = {item.product_id: item for item in comparison.items}
    if len(received) != len(comparison.items) or set(received) != set(planned):
        raise HTTPException(422, "Incluye exactamente una vez cada producto del pedido")
    if not any(item.received_quantity > 0 for item in received.values()):
        raise HTTPException(422, "No se puede recibir un pedido sin mercancía")
    for product_id, row in received.items():
        item = planned[product_id]
        if item.ordered_quantity is None or item.ordered_unit_cost is None:
            raise HTTPException(409, "El pedido no tiene una fotografía original completa")
        if (row.received_quantity > 0 and row.actual_unit_cost is None) or (
                row.received_quantity == 0 and row.actual_unit_cost is not None):
            raise HTTPException(422, "Indica costo real solo para productos recibidos")
        changed = (row.received_quantity != item.ordered_quantity or
                   (row.received_quantity > 0 and row.actual_unit_cost != item.ordered_unit_cost))
        if changed and not (row.difference_reason and row.difference_reason.strip()):
            raise HTTPException(422, "Explica cada diferencia de cantidad o costo")
        if not changed and row.difference_reason:
            raise HTTPException(422, "No hay diferencia que justifique el motivo")


def receive_transaction(db: Session, purchase_id: UUID, comparison: ReceiveComparisonIn | None = None,
                        payment_body: SupplierPaymentIn | None = None) -> Purchase:
    fingerprint = _receipt_fingerprint(comparison, payment_body) if comparison else None
    # An already completed retry works even if the cash session has since closed.
    if comparison:
        prior = db.execute(select(Purchase.status, Purchase.receipt_request_id,
                                  Purchase.receipt_request_hash).where(Purchase.id == purchase_id)).one_or_none()
        if prior and prior.status == PurchaseStatus.RECEIVED and prior.receipt_request_id == comparison.request_id:
            if prior.receipt_request_hash != fingerprint:
                raise HTTPException(409, "La clave de recepción ya se usó con otros datos")
            return db.get(Purchase, purchase_id)
    try:
        session = require_open_session(db, lock=True)
        purchase = db.scalar(select(Purchase).where(Purchase.id == purchase_id)
                             .with_for_update(of=Purchase).execution_options(populate_existing=True))
        if not purchase:
            raise HTTPException(404, "Compra no encontrada")
        if purchase.status == PurchaseStatus.RECEIVED and comparison and purchase.receipt_request_id == comparison.request_id:
            if purchase.receipt_request_hash != fingerprint:
                raise HTTPException(409, "La clave de recepción ya se usó con otros datos")
            return purchase
        if purchase.status not in (PurchaseStatus.DRAFT, PurchaseStatus.SCHEDULED):
            raise HTTPException(409, "La compra ya fue recibida o cancelada")
        scheduled = purchase.status == PurchaseStatus.SCHEDULED
        if scheduled and comparison is None:
            raise HTTPException(422, "El pedido programado requiere recepción comparada")
        if not scheduled and comparison is not None:
            raise HTTPException(422, "La recepción comparada solo aplica a pedidos programados")
        if scheduled:
            if payment_body is None and purchase.payment_terms != PurchasePaymentTerms.CREDIT:
                raise HTTPException(409, "El pago contra entrega debe registrarse junto con la recepción")
            if payment_body is not None and purchase.payment_terms == PurchasePaymentTerms.CREDIT:
                raise HTTPException(409, "La compra a crédito se liquida después de recibirla")
            _validate_supplier(db, purchase.supplier_id, require_active=False, lock=True)
            if comparison.invoice_number is not None:
                if not comparison.invoice_number.strip():
                    raise HTTPException(422, "El número de factura no puede estar vacío")
                purchase.invoice_number = comparison.invoice_number.strip()
            _check_invoice_duplicate(db, purchase)
            _validate_comparison(purchase, comparison)
            other = db.scalar(select(Purchase.id).where(
                Purchase.receipt_request_id == comparison.request_id, Purchase.id != purchase.id))
            if other:
                raise HTTPException(409, "La clave de recepción ya pertenece a otra compra")
        if not purchase.items:
            raise HTTPException(422, "No se puede recibir una compra vacía")
        channel = None
        if payment_body:
            channel = db.get(PaymentChannel, payment_body.payment_channel_id)
            if not channel or not channel.active or not (
                    channel.affects_cash or channel.payment_type == PaymentMethod.TRANSFER):
                raise HTTPException(422, "Para proveedores solo se permite efectivo o transferencia")
            if scheduled:
                if purchase.payment_terms == PurchasePaymentTerms.CASH_ON_DELIVERY and not channel.affects_cash:
                    raise HTTPException(422, "Este pedido requiere pago en efectivo contra entrega")
                if purchase.payment_terms == PurchasePaymentTerms.DIGITAL_ON_DELIVERY and channel.affects_cash:
                    raise HTTPException(422, "Este pedido requiere pago digital contra entrega")
        products = {product.id: product for product in db.scalars(
            select(Product).where(Product.id.in_([item.product_id for item in purchase.items]))
            .order_by(Product.id).with_for_update(of=Product)).all()}
        if len(products) != len(purchase.items):
            raise HTTPException(409, "La compra contiene un producto que ya no existe")
        operator = get_operator(db)
        received = {item.product_id: item for item in comparison.items} if comparison else {}
        for item in purchase.items:
            if scheduled:
                row = received[item.product_id]
                item.quantity = row.received_quantity
                item.unit_cost = row.actual_unit_cost if row.actual_unit_cost is not None else Decimal("0")
                item.line_total = _line_total(item.quantity, item.unit_cost)
                item.difference_reason = row.difference_reason.strip() if row.difference_reason else None
            if item.quantity == 0:
                continue
            product = products[item.product_id]
            db.add(InventoryMovement(product_id=item.product_id,
                movement_type=InventoryMovementType.PURCHASE, quantity=item.quantity,
                unit_cost=item.unit_cost, reference_id=purchase.id, user_id=operator.id))
            product.unit_cost = item.unit_cost
            product.supplier_id = purchase.supplier_id
        if scheduled:
            purchase.subtotal = sum((item.line_total for item in purchase.items), Decimal("0"))
            purchase.total = purchase.subtotal
            purchase.receipt_request_id = comparison.request_id
            purchase.receipt_request_hash = fingerprint
        purchase.status = PurchaseStatus.RECEIVED
        purchase.received_at = datetime.now(timezone.utc)
        if channel:
            db.add(SupplierPayment(purchase_id=purchase.id, supplier_id=purchase.supplier_id,
                amount=purchase.total, payment_channel_id=channel.id, reference=payment_body.reference,
                notes=payment_body.notes, paid_at=datetime.now(timezone.utc), created_by=operator.id))
            if channel.affects_cash:
                if expected_cash(db, session) < purchase.total:
                    raise HTTPException(409, "El efectivo disponible en caja no es suficiente")
                db.add(CashMovement(cash_session_id=session.id,
                    movement_type=CashMovementType.SUPPLIER_PAYMENT, amount=-purchase.total,
                    reference_id=purchase.id,
                    notes=f"Pago proveedor - {db.get(Supplier, purchase.supplier_id).name}",
                    user_id=operator.id))
        db.commit(); db.refresh(purchase)
        return purchase
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "La recepción o el pago ya fueron registrados")
    except Exception:
        db.rollback()
        raise
