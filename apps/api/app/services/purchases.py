from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import json
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.enums import (CashMovementType, InventoryMovementType, PaymentMethod,
                              PurchasePaymentTerms, PurchaseStatus)
from app.models import (CashMovement, PaymentChannel, InventoryMovement, Product, Purchase,
                        PurchaseItem, Supplier, SupplierPayment)
from app.schemas.purchases import (PurchaseCreate, ReceiveComparisonIn, ReceivePurchaseIn,
                                   SchedulePurchaseIn, SupplierPaymentIn)
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
        unit_cost=item.unit_cost, line_total=_line_total(item.quantity, item.unit_cost))
        for item in body.items]
    purchase.subtotal = sum((item.line_total for item in purchase.items), Decimal("0"))
    purchase.total = purchase.subtotal


def _line_total(quantity: Decimal, unit_cost: Decimal) -> Decimal:
    """Round each line to cents, half up; totals are sums of rounded lines."""
    return (quantity * unit_cost).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _fingerprint(payload: dict) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode("utf-8")).hexdigest()


def _receipt_fingerprint(comparison: ReceiveComparisonIn, payment: SupplierPaymentIn | None) -> str:
    payload = comparison.model_dump(mode="json", exclude={"request_id"})
    payload["items"] = sorted(payload["items"], key=lambda item: item["product_id"])
    payload["payment"] = payment.model_dump(mode="json") if payment else None
    if payload["payment"] is not None:
        payload["payment"].pop("comparison", None)
    return _fingerprint(payload)


def _check_invoice_duplicate(db: Session, purchase: Purchase) -> None:
    if not purchase.invoice_number or not purchase.invoice_number.strip():
        return
    duplicate = db.scalar(select(Purchase.id).where(
        Purchase.id != purchase.id,
        Purchase.supplier_id == purchase.supplier_id,
        func.lower(func.trim(Purchase.invoice_number)) == purchase.invoice_number.strip().lower(),
        Purchase.status != PurchaseStatus.CANCELLED,
    ).limit(1))
    if duplicate:
        raise HTTPException(409, "Ya existe una compra con ese número de factura para este proveedor")


def _validate_schedule(purchase: Purchase, body: SchedulePurchaseIn) -> dict[UUID, object]:
    if body.payment_terms == PurchasePaymentTerms.CREDIT:
        if body.payment_due_date is None or body.payment_due_date <= body.expected_delivery_date:
            raise HTTPException(422, "El crédito requiere un vencimiento posterior a la entrega prevista")
    elif body.payment_due_date is not None:
        raise HTTPException(422, "Solo el credito puede tener fecha de vencimiento")
    items = {item.product_id: item for item in purchase.items}
    offers = {offer.product_id: offer for offer in body.offers}
    if len(offers) != len(body.offers) or not set(offers) <= set(items):
        raise HTTPException(422, "Las ofertas deben referirse una sola vez a productos del pedido")
    for product_id, offer in offers.items():
        if (offer.list_unit_cost is None) != (offer.discount_per_unit is None):
            raise HTTPException(422, "Indica precio de lista y descuento por unidad juntos")
        if offer.list_unit_cost is not None:
            if offer.discount_per_unit > offer.list_unit_cost or (offer.list_unit_cost - offer.discount_per_unit) != items[product_id].unit_cost:
                raise HTTPException(422, "El costo estimado debe coincidir con el precio de lista menos el descuento")
    return offers


def schedule_purchase(db: Session, purchase_id: UUID, body: SchedulePurchaseIn) -> Purchase:
    try:
        purchase = db.scalar(select(Purchase).where(Purchase.id == purchase_id).with_for_update(of=Purchase))
        if not purchase:
            raise HTTPException(404, "Compra no encontrada")
        offers = _validate_schedule(purchase, body)
        if purchase.status == PurchaseStatus.SCHEDULED:
            same = (purchase.expected_delivery_date == body.expected_delivery_date
                    and purchase.payment_terms == body.payment_terms
                    and purchase.payment_due_date == body.payment_due_date
                    and purchase.offer_notes == body.offer_notes
                    and all((item.offer_list_unit_cost == (offers[item.product_id].list_unit_cost if item.product_id in offers else None)
                             and item.offer_discount_per_unit == (offers[item.product_id].discount_per_unit if item.product_id in offers else None)
                             and item.offer_note == (offers[item.product_id].note if item.product_id in offers else None))
                            for item in purchase.items))
            if same:
                return purchase
            raise HTTPException(409, "El pedido ya fue programado con condiciones diferentes")
        if purchase.status != PurchaseStatus.DRAFT:
            raise HTTPException(409, "Solo puede programarse una compra en borrador")
        if not purchase.items:
            raise HTTPException(422, "El pedido no puede estar vacío")
        _validate_supplier(db, purchase.supplier_id, lock=True)
        _check_invoice_duplicate(db, purchase)
        for item in purchase.items:
            item.ordered_quantity = item.quantity
            item.ordered_unit_cost = item.unit_cost
            offer = offers.get(item.product_id)
            if offer:
                item.offer_list_unit_cost = offer.list_unit_cost
                item.offer_discount_per_unit = offer.discount_per_unit
                item.offer_note = offer.note
        purchase.estimated_total = purchase.total
        purchase.scheduled_at = datetime.now(timezone.utc)
        purchase.expected_delivery_date = body.expected_delivery_date
        purchase.payment_terms = body.payment_terms
        purchase.payment_due_date = body.payment_due_date
        purchase.offer_notes = body.offer_notes
        purchase.status = PurchaseStatus.SCHEDULED
        db.commit(); db.refresh(purchase)
        return purchase
    except Exception:
        db.rollback()
        raise


def purchase_agenda(db: Session, *, as_of: date | None = None, horizon_days: int = 30) -> list[dict]:
    today = as_of or datetime.now(ZoneInfo("America/Bogota")).date()
    last_day = today + timedelta(days=horizon_days)
    rows = db.execute(select(Purchase, Supplier.name).join(Supplier, Supplier.id == Purchase.supplier_id)
        .where(Purchase.status == PurchaseStatus.SCHEDULED,
               Purchase.expected_delivery_date <= last_day)
        .order_by(Purchase.expected_delivery_date, Purchase.created_at).limit(200)).unique().all()
    return [{"purchase_id": purchase.id, "supplier_id": purchase.supplier_id,
             "supplier_name": supplier_name, "expected_delivery_date": purchase.expected_delivery_date,
             "estimated_total": purchase.estimated_total, "payment_terms": purchase.payment_terms,
             "payment_due_date": purchase.payment_due_date, "status": purchase.status,
             "alert": "OVERDUE" if purchase.expected_delivery_date < today else
                      "DUE_SOON" if purchase.expected_delivery_date <= today + timedelta(days=3) else "UPCOMING"}
            for purchase, supplier_name in rows]


def purchase_commitments(db: Session, date_from: date, date_to: date) -> dict:
    if date_to < date_from or (date_to - date_from).days > 366:
        raise HTTPException(422, "Consulta un rango ordenado de hasta 366 días")
    days: dict[date, dict] = {}
    fields = ("estimated_cash", "estimated_digital", "estimated_credit", "estimated_total",
              "actual_outstanding_cash", "actual_outstanding_digital", "actual_outstanding_credit",
              "actual_outstanding_unclassified", "actual_outstanding_total")
    overdue = {"cash": Decimal("0"), "digital": Decimal("0"),
               "credit": Decimal("0"), "unclassified": Decimal("0")}

    def add(day: date, field: str, amount: Decimal):
        if not date_from <= day <= date_to:
            return
        bucket = days.setdefault(day, {"date": day, **{name: Decimal("0") for name in fields}})
        bucket[field] += amount
        bucket["estimated_total" if field.startswith("estimated_") else "actual_outstanding_total"] += amount

    purchases = db.scalars(select(Purchase).where(Purchase.status.in_(
        [PurchaseStatus.SCHEDULED, PurchaseStatus.RECEIVED]))).unique().all()
    for purchase in purchases:
        if purchase.status == PurchaseStatus.SCHEDULED:
            day = (purchase.payment_due_date if purchase.payment_terms == PurchasePaymentTerms.CREDIT
                   else purchase.expected_delivery_date)
            field = {PurchasePaymentTerms.CASH_ON_DELIVERY: "estimated_cash",
                     PurchasePaymentTerms.DIGITAL_ON_DELIVERY: "estimated_digital",
                     PurchasePaymentTerms.CREDIT: "estimated_credit"}.get(purchase.payment_terms)
            if day and field and purchase.estimated_total is not None:
                add(day, field, purchase.estimated_total)
        elif purchase.payment is None:
            moment = purchase.received_at or purchase.purchased_at
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            day = (purchase.payment_due_date if purchase.payment_terms == PurchasePaymentTerms.CREDIT
                   else moment.astimezone(ZoneInfo("America/Bogota")).date())
            field = {PurchasePaymentTerms.CASH_ON_DELIVERY: "actual_outstanding_cash",
                     PurchasePaymentTerms.DIGITAL_ON_DELIVERY: "actual_outstanding_digital",
                     PurchasePaymentTerms.CREDIT: "actual_outstanding_credit"}.get(
                         purchase.payment_terms, "actual_outstanding_unclassified")
            if day:
                if day < date_from:
                    overdue[field.removeprefix("actual_outstanding_")] += purchase.total
                else:
                    add(day, field, purchase.total)
    rows = [days[key] for key in sorted(days)]
    return {"date_from": date_from, "date_to": date_to, "days": rows,
            "estimated_total": sum((row["estimated_total"] for row in rows), Decimal("0")),
            "actual_outstanding_total": sum((row["actual_outstanding_total"] for row in rows), Decimal("0")),
            "overdue_as_of": date_from, "overdue_outstanding_cash": overdue["cash"],
            "overdue_outstanding_digital": overdue["digital"],
            "overdue_outstanding_credit": overdue["credit"],
            "overdue_outstanding_unclassified": overdue["unclassified"],
            "overdue_outstanding_total": sum(overdue.values(), Decimal("0"))}


def create_purchase(db: Session, body: PurchaseCreate, *, enforce_invoice_unique: bool = False,
                    request_id: UUID | None = None) -> Purchase:
    supplier = _validate_supplier(db, body.supplier_id, lock=True)
    request_hash = _fingerprint(body.model_dump(mode="json")) if request_id else None
    if request_id:
        existing = db.scalar(select(Purchase).where(Purchase.creation_request_id == request_id))
        if existing:
            if existing.creation_request_hash != request_hash:
                raise HTTPException(409, "La clave de creación ya se usó con otros datos")
            return existing
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
        created_by=get_operator(db).id, status=PurchaseStatus.DRAFT,
        creation_request_id=request_id, creation_request_hash=request_hash)
    _apply_items(purchase, body)
    db.add(purchase)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if request_id:
            existing = db.scalar(select(Purchase).where(Purchase.creation_request_id == request_id))
            if existing and existing.creation_request_hash == request_hash:
                return existing
            if existing:
                raise HTTPException(409, "La clave de creación ya se usó con otros datos")
        raise
    db.refresh(purchase)
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


def receive_purchase(db: Session, purchase_id: UUID,
                     comparison: ReceiveComparisonIn | None = None) -> Purchase:
    from app.services.purchase_receipts import receive_transaction
    return receive_transaction(db, purchase_id, comparison)

def cancel_purchase(db: Session, purchase_id: UUID) -> Purchase:
    purchase = db.scalar(select(Purchase).where(Purchase.id == purchase_id).with_for_update(of=Purchase))
    if not purchase:
        raise HTTPException(404, "Compra no encontrada")
    if purchase.status not in (PurchaseStatus.DRAFT, PurchaseStatus.SCHEDULED):
        raise HTTPException(409, "Solo se puede cancelar una compra pendiente")
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


def receive_purchase_with_payment(db: Session, purchase_id: UUID, body: ReceivePurchaseIn) -> Purchase:
    from app.services.purchase_receipts import receive_transaction
    return receive_transaction(db, purchase_id, body.comparison, body)
