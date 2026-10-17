"""POST-SALE MANAGEMENT (migration 0027) — a COMPLETED sale is immutable history.

Nothing here edits a Sale, SaleItem, Payment, InventoryMovement, CashMovement or ElectronicInvoice that
already exists. Every post-sale fact is a NEW, append-only record:

  * SaleNote            — a clarification; never moves money, stock, invoice or payment.
  * SaleExchange        — PRODUCT EXCHANGE, the business's usual case ("devuelve A, se lleva B"). Credit =
                          the value the returned units were delivered with; replacements use the current
                          price; only the signed DIFFERENCE moves money (EVEN / CUSTOMER_PAYS / STORE_REFUNDS).
  * SaleReturn (+lines) — money return, the exception: PARTIAL / TOTAL / VOID (VOID is shown "Anulada";
                          the sale stays COMPLETED). Restocked lines get ONE InventoryMovement RETURN.
  * SaleRefund          — the money of a return, per channel. Cash: CashMovement REFUND (negative) at once,
                          COMPLETED. Electronic: PENDING until the cashier confirms the external transfer.
  * PaymentCorrection   — the payment keeps its channel; the effective one is resolved by
                          services/effective_payments. Cash <-> electronic adds ONE CashMovement
                          PAYMENT_CORRECTION, comparing the effective channel before vs after.

PROVENANCE: the units a customer holds come from the original sale lines (SALE_ITEM) or from the
replacement lines of earlier exchanges (EXCHANGE_LINE). Returns and exchanges consume units of exactly one
source; availability = delivered - already consumed, per source, so a unit is never consumed twice and
chained exchanges (A -> B -> C) need no new sale. A unit's credit is always the value it was DELIVERED with.

Every writer locks the sale row (FOR UPDATE), so concurrent operations on the same sale are serialized;
quantities are re-checked after the lock and unique keys protect the rest (one VOID per sale, idempotency
keys, one inventory / cash movement per record).

Fiscal safety: a sale with ANY ElectronicInvoice row blocks every money / exchange operation here. Notes
are always allowed. Nothing here talks to Alegra.
"""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import (CashMovementType, ElectronicInvoiceStatus, InventoryMovementType, SaleStatus)
from app.models import (BusinessAccount, CashMovement, CashSession, Customer, ElectronicInvoice, InventoryMovement, Payment,
                        PaymentChannel, PaymentCorrection, Product, Sale, SaleExchange, SaleExchangeReplacementLine,
                        SaleExchangeReturnLine, SaleExchangeSettlement, SaleExternalItem, SaleItem, SaleNote,
                        SaleRefund, SaleReturn, SaleReturnLine)
from app.services.effective_payments import corrections_by_payment, effective_account_ids, effective_channels
from app.services.operational_session import SESSION_CLOSED_MESSAGE
from app.services.pos import get_operator

ZERO = Decimal("0")
CENT = Decimal("0.01")
SALE_ITEM, EXCHANGE_LINE = "SALE_ITEM", "EXCHANGE_LINE"

INVOICE_IN_PROCESS_MESSAGE = ("Esta venta tiene un proceso de factura electrónica asociado. "
                              "La operación post-venta requiere manejo fiscal antes de continuar.")
INVOICE_ISSUED_MESSAGE = "Esta venta tiene factura electrónica y requiere una nota crédito."
INVOICE_ISSUED_EXCHANGE_MESSAGE = ("Esta venta tiene factura electrónica. El cambio requiere manejo fiscal antes "
                                   "de continuar.")
VOIDED_MESSAGE = "La venta está anulada."
NOT_COMPLETED_MESSAGE = "Solo se puede gestionar una venta cobrada."
EXTERNAL_GRANEL_VOID_MESSAGE = ("La venta incluye comida a granel cobrada en la báscula. El POS no puede "
                                "devolver ese dinero: registra una devolución de los productos del POS.")
RETURNS_BLOCK_VOID_MESSAGE = "La venta ya tiene devoluciones: ya no se puede anular."
EXCHANGES_BLOCK_VOID_MESSAGE = "La venta ya tiene cambios de producto: ya no se puede anular."
RETURNS_BLOCK_CORRECTION_MESSAGE = ("La venta tiene devoluciones registradas: el canal de pago ya no se "
                                    "puede corregir.")
NOTHING_TO_RETURN_MESSAGE = "No quedan productos por cambiar ni devolver en esta venta."
NOTHING_TO_EXCHANGE_MESSAGE = NOTHING_TO_RETURN_MESSAGE
CREDIT_REMAINING_MESSAGE = ("Al cliente le quedan {amount} por usar: agrega otro producto o elige devolver "
                            "la diferencia.")
GRANEL_REPLACEMENT_MESSAGE = ("La comida a granel no se entrega en un cambio: se registra en la báscula. Agrega otro "
                              "producto.")
CASH_CORRECTION_NO_SESSION = "Esta corrección mueve efectivo del cajón: abre la caja para registrarla."
CASH_CORRECTION_OLD_SALE = ("La venta es de una jornada ya cerrada. Una corrección que mueve efectivo solo se permite "
                            "en ventas de la jornada abierta.")

RETURN_REASONS = {
    "CUSTOMER_RETURN": "El cliente devolvió el producto",
    "DEFECTIVE": "Producto defectuoso o vencido",
    "WRONG_ITEM": "Se registró un producto equivocado",
    "WRONG_QUANTITY": "Se registró una cantidad equivocada",
    "WRONG_CHARGE": "Cobro equivocado",
    "OTHER": "Otro motivo",
}
EXCHANGE_REASONS = {
    "WRONG_SIZE": "Talla o presentación incorrecta",
    "CHANGED_MIND": "El cliente cambió de opinión",
    "DEFECTIVE": "Producto defectuoso",
    "WRONG_ITEM": "Producto equivocado",
    "OTHER": "Otro motivo",
}
REPLACEMENT_MANUAL_CATEGORY = {"SUPER": "SUPER", "BULTO": "MASCOTAS_SIN_CODIGO", "PET_OTHER": "MASCOTAS_SIN_CODIGO"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _clean(text: str | None) -> str | None:
    text = (text or "").strip()
    return text or None


def _pesos(value: Decimal) -> str:
    return "$" + f"{value.quantize(Decimal('1')):,}".replace(",", ".")


def _open_session(db: Session, *, lock: bool = False) -> CashSession | None:
    statement = select(CashSession).where(CashSession.closed_at.is_(None))
    if lock:
        statement = statement.with_for_update()
    return db.scalar(statement)


def _session_closed() -> HTTPException:
    return HTTPException(409, detail={"code": "OPERATIONAL_SESSION_CLOSED", "message": SESSION_CLOSED_MESSAGE})


def _lock_sale(db: Session, sale_id: UUID) -> Sale:
    sale = db.scalar(select(Sale).where(Sale.id == sale_id).with_for_update())
    if sale is None:
        raise HTTPException(404, "Venta no encontrada")
    return sale


# ---- facts about a sale ------------------------------------------------------------------------------

def invoice_block(db: Session, sale: Sale, *, exchange: bool = False) -> str | None:
    """Any ElectronicInvoice row (PENDING / PROCESSING / ISSUED / BLOCKED / ERROR / ...) blocks money and
    exchange operations. ISSUED needs fiscal handling (nota crédito, not implemented); anything else needs
    fiscal handling first. Nothing here ever talks to Alegra."""
    invoice = db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale.id))
    if invoice is None:
        return None
    if invoice.status == ElectronicInvoiceStatus.ISSUED:
        return INVOICE_ISSUED_EXCHANGE_MESSAGE if exchange else INVOICE_ISSUED_MESSAGE
    return INVOICE_IN_PROCESS_MESSAGE


def _returns(db: Session, sale_id: UUID) -> list[SaleReturn]:
    return list(db.scalars(select(SaleReturn).where(SaleReturn.sale_id == sale_id)
                           .order_by(SaleReturn.created_at, SaleReturn.id)))


def _exchanges(db: Session, sale_id: UUID) -> list[SaleExchange]:
    return list(db.scalars(select(SaleExchange).where(SaleExchange.sale_id == sale_id)
                           .order_by(SaleExchange.created_at, SaleExchange.id)))


def _is_granel(sale: Sale, item: SaleItem) -> bool:
    from app.services.electronic_invoicing import manual_kind
    return item.product_id is None and manual_kind(sale, item) == "GRANEL"


def _has_external_granel(db: Session, sale: Sale) -> bool:
    return db.scalar(select(func.count(SaleExternalItem.id)).where(SaleExternalItem.sale_id == sale.id)) > 0


# ---- provenance: every unit the customer holds, and what is left of it ---------------------------------

def _line_key(line) -> tuple[str, UUID]:
    """Source of a return / exchange line: the original sale line or an earlier exchange's replacement."""
    if line.sale_item_id is not None:
        return SALE_ITEM, line.sale_item_id
    return EXCHANGE_LINE, line.source_replacement_line_id


def _units(sale: Sale, exchanges: list[SaleExchange]) -> dict[tuple[str, UUID], dict]:
    """Every source of units of this sale: original lines + lines delivered by its exchanges, each with the
    value it was DELIVERED with (never today's price)."""
    units: dict[tuple[str, UUID], dict] = {}
    for item in sale.items:
        units[(SALE_ITEM, item.id)] = {
            "key": (SALE_ITEM, item.id), "name": item.product_name, "product_id": item.product_id,
            "manual_kind": item.manual_kind, "is_granel": _is_granel(sale, item), "quantity": item.quantity,
            "unit_price": item.unit_price, "line_total": item.line_total, "unit_cost": item.unit_cost,
            "category_code": item.category_code or "SIN_CLASIFICAR", "origin": "Venta", "exchange_id": None}
    for index, exchange in enumerate(exchanges, start=1):
        for line in exchange.replacements:
            units[(EXCHANGE_LINE, line.id)] = {
                "key": (EXCHANGE_LINE, line.id), "name": line.product_name, "product_id": line.product_id,
                "manual_kind": line.manual_kind, "is_granel": False, "quantity": line.quantity,
                "unit_price": line.unit_price, "line_total": line.line_total, "unit_cost": line.unit_cost,
                "category_code": line.category_code, "origin": f"Cambio {index}", "exchange_id": exchange.id}
    return units


def _consumed(returns: list[SaleReturn], exchanges: list[SaleExchange]
              ) -> tuple[dict[tuple[str, UUID], Decimal], dict[tuple[str, UUID], Decimal]]:
    """Units (and value) of each source already given back, by money returns or exchanges. A unit is
    consumed once; the value keeps the rest of a line exact (no rounding drift)."""
    quantity: dict[tuple[str, UUID], Decimal] = defaultdict(lambda: ZERO)
    amount: dict[tuple[str, UUID], Decimal] = defaultdict(lambda: ZERO)
    for sale_return in returns:
        for line in sale_return.lines:
            quantity[_line_key(line)] += line.quantity
            amount[_line_key(line)] += line.amount
    for exchange in exchanges:
        for line in exchange.returned:
            quantity[_line_key(line)] += line.quantity
            amount[_line_key(line)] += line.line_credit
    return quantity, amount


def _request_key(line: dict) -> tuple[str, UUID]:
    sale_item_id, replacement_id = line.get("sale_item_id"), line.get("replacement_line_id")
    if (sale_item_id is None) == (replacement_id is None):
        raise HTTPException(422, "Cada línea indica sale_item_id o replacement_line_id (exactamente uno)")
    return (SALE_ITEM, sale_item_id) if sale_item_id is not None else (EXCHANGE_LINE, replacement_id)


def _plan_units(units: dict, consumed_qty: dict, consumed_amount: dict, lines: list[dict], verb: str) -> list[dict]:
    """Validates the requested units against what is left of each source and prices them with the value
    they were delivered with."""
    planned, seen = [], set()
    for line in lines:
        key = _request_key(line)
        if key not in units:
            raise HTTPException(422, "Una línea no pertenece a esta venta")
        if key in seen:
            raise HTTPException(422, "Una línea está repetida")
        seen.add(key)
        quantity = Decimal(str(line["quantity"]))
        if quantity <= 0:
            raise HTTPException(422, f"La cantidad a {verb} debe ser mayor que cero")
        unit = units[key]
        remaining = unit["quantity"] - consumed_qty.get(key, ZERO)
        if quantity > remaining:
            raise HTTPException(409, f"{unit['name']}: solo quedan {remaining.normalize():f} por cambiar o devolver")
        amount = (unit["line_total"] - consumed_amount.get(key, ZERO) if quantity == remaining
                  else (quantity * unit["unit_price"]).quantize(CENT))
        planned.append({"unit": unit, "quantity": quantity, "amount": amount,
                        "restock": bool(line.get("restock", False)) and unit["product_id"] is not None})
    return planned


def _money_by_channel(db: Session, sale: Sale, returns: list[SaleReturn], exchanges: list[SaleExchange]
                      ) -> tuple[dict[UUID, Decimal], dict[UUID, PaymentChannel]]:
    """Money of this sale still available per channel: sale payments (effective channel) + differences the
    customer paid in exchanges - money already given back (returns and exchange differences)."""
    effective = effective_channels(db, sale.payments)
    available: dict[UUID, Decimal] = defaultdict(lambda: ZERO)
    channels: dict[UUID, PaymentChannel] = {}
    for payment in sale.payments:
        channel = effective[payment.id]
        available[channel.id] += payment.amount
        channels[channel.id] = channel
    for exchange in exchanges:
        money = exchange.money
        if money is not None:
            available[money.payment_channel_id] += money.amount if money.direction == "IN" else -money.amount
            channels.setdefault(money.payment_channel_id, money.channel)
    for sale_return in returns:
        for refund in sale_return.refunds:
            available[refund.channel_id] -= refund.amount
    return available, channels


def display_status(sale: Sale, returns: list[SaleReturn], exchanges: list[SaleExchange] = ()) -> tuple[str, str]:
    if sale.status == SaleStatus.OPEN:
        return "OPEN", "Abierta"
    if sale.status == SaleStatus.CANCELLED:
        return "CANCELLED", "Cancelada"
    if any(r.kind == "VOID" for r in returns):
        return "VOIDED", "Anulada"
    if exchanges and returns:
        return "EXCHANGED_AND_RETURNED", "Cambio y devolución"
    if exchanges:
        return "EXCHANGED", "Con cambio de producto"
    if returns:
        consumed, _ = _consumed(returns, [])
        if all(consumed.get((SALE_ITEM, item.id), ZERO) >= item.quantity for item in sale.items):
            return "RETURNED", "Devuelta"
        return "PARTIALLY_RETURNED", "Devolución parcial"
    return "COMPLETED", "Completada"


def _action(allowed: bool, reason: str | None = None) -> dict:
    return {"allowed": allowed, "reason": None if allowed else reason}


def allowed_actions(db: Session, sale: Sale, returns: list[SaleReturn] | None = None,
                    exchanges: list[SaleExchange] | None = None) -> dict:
    """The backend decides what the cashier may do; the UI only shows it (disabled + reason)."""
    returns = _returns(db, sale.id) if returns is None else returns
    exchanges = _exchanges(db, sale.id) if exchanges is None else exchanges
    note = _action(sale.status != SaleStatus.OPEN, "La venta todavía está abierta.")
    blocked = None
    if sale.status != SaleStatus.COMPLETED:
        blocked = NOT_COMPLETED_MESSAGE
    elif any(r.kind == "VOID" for r in returns):
        blocked = VOIDED_MESSAGE
    else:
        blocked = invoice_block(db, sale)
    open_session = _open_session(db)
    units = _units(sale, exchanges)
    consumed, _ = _consumed(returns, exchanges)
    returnable = any(unit["quantity"] - consumed.get(key, ZERO) > 0 for key, unit in units.items())
    if blocked:
        exchange_blocked = invoice_block(db, sale, exchange=True) if blocked not in (NOT_COMPLETED_MESSAGE,
                                                                                      VOIDED_MESSAGE) else blocked
        return {"note": note, "exchange": _action(False, exchange_blocked), "return": _action(False, blocked),
                "void": _action(False, blocked), "correct_payment": _action(False, blocked),
                "correct_payment_cash": _action(False, blocked)}
    unit_reason = (NOTHING_TO_RETURN_MESSAGE if not returnable
                   else SESSION_CLOSED_MESSAGE if open_session is None else None)
    void_reason = None
    if returns:
        void_reason = RETURNS_BLOCK_VOID_MESSAGE
    elif exchanges:
        void_reason = EXCHANGES_BLOCK_VOID_MESSAGE
    elif _has_external_granel(db, sale):
        void_reason = EXTERNAL_GRANEL_VOID_MESSAGE
    elif open_session is None:
        void_reason = SESSION_CLOSED_MESSAGE
    correction_reason = RETURNS_BLOCK_CORRECTION_MESSAGE if returns else None
    if not sale.payments:
        correction_reason = "La venta no tiene pagos."
    # Corrections between cash and electronic move the drawer: only on a sale of the open jornada.
    cash_reason = correction_reason
    if cash_reason is None and open_session is None:
        cash_reason = CASH_CORRECTION_NO_SESSION
    elif cash_reason is None and _utc(sale.completed_at) < _utc(open_session.opened_at):
        cash_reason = CASH_CORRECTION_OLD_SALE
    return {"note": note, "exchange": _action(unit_reason is None, unit_reason),
            "return": _action(unit_reason is None, unit_reason),
            "void": _action(void_reason is None, void_reason),
            "correct_payment": _action(correction_reason is None, correction_reason),
            "correct_payment_cash": _action(cash_reason is None, cash_reason)}


# ---- detail ------------------------------------------------------------------------------------------

def _account_names(db: Session) -> dict:
    return {a.id: a.name for a in db.scalars(select(BusinessAccount))}


def _refund_out(refund: SaleRefund, accounts: dict | None = None) -> dict:
    accounts = accounts or {}
    return {"id": refund.id, "kind": "RETURN", "return_id": refund.return_id, "exchange_id": None,
            "payment_id": refund.payment_id, "channel_id": refund.channel_id, "channel_name": refund.channel.name,
            "affects_cash": refund.affects_cash, "amount": refund.amount, "status": refund.status,
            "account_id": refund.account_id, "account_name": accounts.get(refund.account_id),
            "requires_account": (not refund.affects_cash and refund.account_id is None
                                 and refund.channel.requires_destination_account),
            "created_at": refund.created_at, "confirmed_at": refund.confirmed_at,
            "confirmed_by_text": refund.confirmed_by_text}


def _exchange_refund_out(exchange: SaleExchange, accounts: dict | None = None) -> dict:
    money = exchange.money
    accounts = accounts or {}
    return {"id": money.id, "kind": "EXCHANGE", "return_id": None, "exchange_id": exchange.id, "payment_id": None,
            "channel_id": money.payment_channel_id, "channel_name": money.channel.name,
            "affects_cash": money.affects_cash, "amount": money.amount, "status": money.status,
            "account_id": money.account_id, "account_name": accounts.get(money.account_id),
            "requires_account": (not money.affects_cash and money.account_id is None
                                 and money.channel.requires_destination_account),
            "created_at": money.created_at, "confirmed_at": money.confirmed_at,
            "confirmed_by_text": money.confirmed_by_text}


def _unit_out(key: tuple[str, UUID], unit: dict, consumed_qty: dict, returned_qty: dict, exchanged_qty: dict) -> dict:
    return {"key": f"{key[0]}:{key[1]}", "source_type": key[0], "source_id": key[1], "product_name": unit["name"],
            "product_id": unit["product_id"], "manual_kind": unit["manual_kind"], "is_granel": unit["is_granel"],
            "restockable": unit["product_id"] is not None, "origin": unit["origin"], "quantity": unit["quantity"],
            "unit_price": unit["unit_price"], "line_total": unit["line_total"],
            "returned_quantity": returned_qty.get(key, ZERO), "exchanged_quantity": exchanged_qty.get(key, ZERO),
            "returnable_quantity": max(unit["quantity"] - consumed_qty.get(key, ZERO), ZERO)}


def sale_detail(db: Session, sale_id: UUID) -> dict:
    sale = db.get(Sale, sale_id)
    if sale is None:
        raise HTTPException(404, "Venta no encontrada")
    returns = _returns(db, sale.id)
    exchanges = _exchanges(db, sale.id)
    units = _units(sale, exchanges)
    consumed_qty, _ = _consumed(returns, exchanges)
    returned_qty, returned_amount = _consumed(returns, [])
    exchanged_qty, _ = _consumed([], exchanges)
    effective = effective_channels(db, sale.payments)
    effective_accounts = effective_account_ids(db, sale.payments)
    accounts = _account_names(db)
    chains = corrections_by_payment(db, [p.id for p in sale.payments])
    channel_names = {c.id: c.name for c in db.scalars(select(PaymentChannel))}
    invoice = db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale.id))
    customer = db.get(Customer, sale.customer_id) if sale.customer_id else None
    code, label = display_status(sale, returns, exchanges)
    refunds = [_refund_out(refund, accounts) for sale_return in returns for refund in sale_return.refunds]
    refunds += [_exchange_refund_out(e, accounts) for e in exchanges if e.money is not None and e.money.direction == "OUT"]
    pending = [r for r in refunds if r["status"] == "PENDING"]
    invoice_out = None
    if invoice is not None:
        from app.services.electronic_invoicing import display_status as invoice_display_status
        invoice_out = {"id": invoice.id, "status": invoice.status.value,
                       "display_status": invoice_display_status(invoice),
                       "provider_number": invoice.provider_number}
    return {
        "id": sale.id, "number": sale.number, "status": sale.status.value,
        "display_status": code, "display_status_label": label,
        "created_at": sale.created_at, "completed_at": sale.completed_at, "cancelled_at": sale.cancelled_at,
        "total": sale.total,
        "returned_total": sum((r.total_amount for r in returns), ZERO),
        "net_total": sale.total - sum((r.total_amount for r in returns), ZERO),
        "customer": None if customer is None else {"id": customer.id, "name": customer.name,
                                                    "identification_number": customer.identification_number},
        "lines": [{
            "id": item.id, "product_id": item.product_id, "product_name": item.product_name,
            "category_code": item.category_code, "manual_kind": item.manual_kind,
            "is_granel": _is_granel(sale, item), "restockable": item.product_id is not None,
            "quantity": item.quantity, "unit_price": item.unit_price, "line_total": item.line_total,
            "returned_quantity": returned_qty.get((SALE_ITEM, item.id), ZERO),
            "returned_amount": returned_amount.get((SALE_ITEM, item.id), ZERO),
            "exchanged_quantity": exchanged_qty.get((SALE_ITEM, item.id), ZERO),
            "returnable_quantity": max(item.quantity - consumed_qty.get((SALE_ITEM, item.id), ZERO), ZERO),
        } for item in sale.items],
        # What the customer can still bring back: original lines AND units delivered by exchanges.
        "units": [_unit_out(key, unit, consumed_qty, returned_qty, exchanged_qty) for key, unit in units.items()],
        "external_items": [{"description": e.description, "quantity": e.quantity, "amount": e.amount}
                           for e in sale.external_items],
        "payments": [{
            "id": payment.id, "amount": payment.amount,
            "original_channel_id": payment.payment_channel_id,
            "original_channel_name": channel_names.get(payment.payment_channel_id),
            "effective_channel_id": effective[payment.id].id,
            "effective_channel_name": effective[payment.id].name,
            "effective_affects_cash": effective[payment.id].affects_cash,
            "original_account_id": payment.destination_account_id,
            "original_account_name": accounts.get(payment.destination_account_id),
            "effective_account_id": effective_accounts[payment.id],
            "effective_account_name": accounts.get(effective_accounts[payment.id]),
            "corrected": bool(chains[payment.id]),
            "card_type": payment.card_type, "received_amount": payment.received_amount,
            "change_amount": payment.change_amount, "created_at": payment.created_at,
            "corrections": [{
                "id": c.id, "sequence": c.sequence, "from_channel_name": channel_names.get(c.from_channel_id),
                "to_channel_name": channel_names.get(c.to_channel_id), "amount": c.amount,
                "from_account_name": accounts.get(c.from_account_id), "to_account_name": accounts.get(c.to_account_id),
                "reason": c.reason, "cash_effect": _correction_cash_effect(db, c),
                "performed_by_text": c.performed_by_text, "created_at": c.created_at,
            } for c in chains[payment.id]],
        } for payment in sale.payments],
        "invoice": invoice_out,
        "notes": [{"id": n.id, "text": n.text, "performed_by_text": n.performed_by_text, "created_at": n.created_at}
                  for n in db.scalars(select(SaleNote).where(SaleNote.sale_id == sale.id)
                                      .order_by(SaleNote.created_at, SaleNote.id))],
        "returns": [{
            "id": r.id, "kind": r.kind, "reason_code": r.reason_code,
            "reason_label": RETURN_REASONS.get(r.reason_code, r.reason_code), "reason_text": r.reason_text,
            "total_amount": r.total_amount, "performed_by_text": r.performed_by_text, "created_at": r.created_at,
            "lines": [{"sale_item_id": line.sale_item_id, "replacement_line_id": line.source_replacement_line_id,
                       "product_name": units.get(_line_key(line), {}).get("name"),
                       "origin": units.get(_line_key(line), {}).get("origin"),
                       "quantity": line.quantity, "amount": line.amount, "restock": line.restock} for line in r.lines],
            "refunds": [_refund_out(refund, accounts) for refund in r.refunds],
        } for r in returns],
        "exchanges": [_exchange_out(exchange, index, units, consumed_qty, accounts)
                      for index, exchange in enumerate(exchanges, start=1)],
        "exchange_difference_total": sum((e.difference for e in exchanges), ZERO),
        "pending_refunds": pending,
        "refund_status": ("NONE" if not refunds else "PENDING" if pending else "COMPLETED"),
        "allowed_actions": allowed_actions(db, sale, returns, exchanges),
    }


def _correction_cash_effect(db: Session, correction: PaymentCorrection) -> Decimal:
    if correction.cash_movement_id is None:
        return ZERO
    movement = db.get(CashMovement, correction.cash_movement_id)
    return movement.amount if movement else ZERO


# ---- notes -------------------------------------------------------------------------------------------

def add_note(db: Session, sale_id: UUID, text: str, performed_by_text: str | None = None,
             idempotency_key: str | None = None) -> SaleNote:
    text = _clean(text)
    if not text:
        raise HTTPException(422, "Escribe la aclaración")
    try:
        sale = _lock_sale(db, sale_id)
        if sale.status == SaleStatus.OPEN:
            raise HTTPException(409, "La venta todavía está abierta.")
        if idempotency_key:
            existing = db.scalar(select(SaleNote).where(SaleNote.idempotency_key == idempotency_key))
            if existing is not None:
                if existing.sale_id != sale.id:
                    raise HTTPException(409, "La clave de idempotencia ya se usó en otra venta")
                return existing
        note = SaleNote(sale_id=sale.id, text=text[:500], user_id=get_operator(db).id,
                        performed_by_text=_clean(performed_by_text), idempotency_key=idempotency_key,
                        created_at=_now())
        db.add(note); db.commit(); db.refresh(note)
        return note
    except Exception:
        db.rollback()
        raise


# ---- returns / VOID ----------------------------------------------------------------------------------

def _existing_return(db: Session, sale: Sale, idempotency_key: str) -> SaleReturn | None:
    existing = db.scalar(select(SaleReturn).where(SaleReturn.idempotency_key == idempotency_key))
    if existing is not None and existing.sale_id != sale.id:
        raise HTTPException(409, "La clave de idempotencia ya se usó en otra venta")
    return existing


def _check_money_operation(db: Session, sale: Sale, returns: list[SaleReturn]) -> None:
    if sale.status != SaleStatus.COMPLETED:
        raise HTTPException(409, NOT_COMPLETED_MESSAGE)
    if any(r.kind == "VOID" for r in returns):
        raise HTTPException(409, VOIDED_MESSAGE)
    message = invoice_block(db, sale)
    if message:
        raise HTTPException(409, detail={"code": "ELECTRONIC_INVOICE", "message": message})


def _reason(reason_code: str, reason_text: str | None) -> tuple[str, str | None]:
    if reason_code not in RETURN_REASONS:
        raise HTTPException(422, "Motivo de devolución desconocido")
    reason_text = _clean(reason_text)
    if reason_code == "OTHER" and not reason_text:
        raise HTTPException(422, "Describe el motivo")
    return reason_code, reason_text


def _register(db: Session, sale: Sale, session: CashSession, *, kind: str, reason_code: str,
              reason_text: str | None, performed_by_text: str | None, idempotency_key: str,
              lines: list[dict], refunds: list[tuple[PaymentChannel, Decimal, UUID | None, UUID | None]]) -> SaleReturn:
    """Writes one return: lines (+ one RETURN movement per restocked line) and its refunds (cash ->
    CashMovement REFUND now; electronic -> PENDING). Caller already validated everything under lock."""
    user = get_operator(db)
    total = sum((line["amount"] for line in lines), ZERO)
    sale_return = SaleReturn(sale_id=sale.id, kind=kind, reason_code=reason_code, reason_text=reason_text,
                             total_amount=total, cash_session_id=session.id, user_id=user.id,
                             performed_by_text=_clean(performed_by_text), idempotency_key=idempotency_key,
                             created_at=_now())
    db.add(sale_return); db.flush()
    for line in lines:
        unit, movement_id = line["unit"], None
        if line["restock"]:
            movement = InventoryMovement(product_id=unit["product_id"], movement_type=InventoryMovementType.RETURN,
                                         quantity=line["quantity"], unit_cost=unit["unit_cost"],
                                         reference_id=sale_return.id, reason=f"Devolución {sale.number}",
                                         user_id=user.id)
            db.add(movement); db.flush()
            movement_id = movement.id
        kind_, source_id = unit["key"]
        sale_return.lines.append(SaleReturnLine(
            sale_item_id=source_id if kind_ == SALE_ITEM else None,
            source_replacement_line_id=source_id if kind_ == EXCHANGE_LINE else None,
            quantity=line["quantity"], unit_price=unit["unit_price"], amount=line["amount"],
            category_code=unit["category_code"], restock=line["restock"], inventory_movement_id=movement_id))
    label = "Anulación" if kind == "VOID" else "Devolución"
    for channel, amount, payment_id, account_id in refunds:
        if amount <= 0:
            continue
        refund = SaleRefund(channel_id=channel.id, payment_id=payment_id, affects_cash=channel.affects_cash,
                            amount=amount, status="PENDING", account_id=None if channel.affects_cash else account_id,
                            created_at=_now())
        if channel.affects_cash:
            movement = CashMovement(cash_session_id=session.id, movement_type=CashMovementType.REFUND,
                                    amount=-amount, reference_id=sale.id, user_id=user.id,
                                    notes=f"{label} {sale.number}")
            db.add(movement); db.flush()
            refund.cash_movement_id = movement.id
            refund.status = "COMPLETED"
            refund.confirmed_at, refund.confirmed_by = _now(), user.id
        sale_return.refunds.append(refund)
    db.flush()
    return sale_return


def create_return(db: Session, sale_id: UUID, *, lines: list[dict], reason_code: str, reason_text: str | None,
                  refund_channel_id: UUID | None, idempotency_key: str,
                  performed_by_text: str | None = None, refund_account_id: UUID | None = None) -> SaleReturn:
    """Money return (the exception): PARTIAL or TOTAL (TOTAL = after it nothing is left to bring back).
    `lines`: [{sale_item_id | replacement_line_id, quantity, restock}] — units of the original sale or units
    delivered by an exchange, at the value they were delivered with. Money goes back through ONE channel that
    money of this sale came in through, never more than what is still available on it."""
    reason_code, reason_text = _reason(reason_code, reason_text)
    if not lines:
        raise HTTPException(422, "Elige al menos un producto para devolver")
    try:
        session = _open_session(db, lock=True)
        sale = _lock_sale(db, sale_id)
        existing = _existing_return(db, sale, idempotency_key)
        if existing is not None:
            return existing
        if session is None:
            raise _session_closed()
        returns = _returns(db, sale.id)
        _check_money_operation(db, sale, returns)
        exchanges = _exchanges(db, sale.id)
        units = _units(sale, exchanges)
        consumed_qty, consumed_amount = _consumed(returns, exchanges)
        planned = _plan_units(units, consumed_qty, consumed_amount, lines, "devolver")
        total = sum((line["amount"] for line in planned), ZERO)
        available, channels = _money_by_channel(db, sale, returns, exchanges)
        usable = {cid: value for cid, value in available.items() if value > 0}
        if refund_channel_id is None:
            if len(usable) != 1:
                raise HTTPException(422, "Elige por qué medio se devuelve el dinero")
            refund_channel_id = next(iter(usable))
        if refund_channel_id not in channels:
            raise HTTPException(422, "El dinero solo se puede devolver por un medio con el que se pagó la venta")
        if total > available.get(refund_channel_id, ZERO):
            raise HTTPException(409, f"Por {channels[refund_channel_id].name} solo quedan "
                                     f"{max(available.get(refund_channel_id, ZERO), ZERO).quantize(Decimal('1'))} por devolver")
        requested = {line["unit"]["key"]: line["quantity"] for line in planned}
        fully = all(unit["quantity"] - consumed_qty.get(key, ZERO) - requested.get(key, ZERO) <= 0
                    for key, unit in units.items())
        sale_return = _register(db, sale, session, kind="TOTAL" if fully else "PARTIAL", reason_code=reason_code,
                                reason_text=reason_text, performed_by_text=performed_by_text,
                                idempotency_key=idempotency_key, lines=planned,
                                refunds=[(channels[refund_channel_id], total, None,
                                          _refund_account(db, sale, channels[refund_channel_id], refund_account_id))])
        db.commit(); db.refresh(sale_return)
        return sale_return
    except Exception:
        db.rollback()
        raise


def _refund_account(db: Session, sale: Sale, channel: PaymentChannel, explicit: UUID | None) -> UUID | None:
    """The account an electronic refund leaves from (0028): the one the cashier chose, else the account the
    sale's money of that channel landed in (if exactly one). It may stay unknown until the confirmation."""
    if channel.affects_cash:
        if explicit is not None:
            raise HTTPException(422, "El efectivo no sale de una cuenta: no indiques cuenta")
        return None
    if explicit is not None:
        from app.services.business_accounts import require_account
        return require_account(db, explicit).id
    effective = effective_channels(db, sale.payments)
    accounts = effective_account_ids(db, sale.payments)
    known = {accounts[p.id] for p in sale.payments if effective[p.id].id == channel.id and accounts[p.id]}
    return next(iter(known)) if len(known) == 1 else None


def void_sale(db: Session, sale_id: UUID, *, reason_code: str, reason_text: str | None, restock: bool,
              idempotency_key: str, performed_by_text: str | None = None) -> SaleReturn:
    """ANULAR: returns every line and refunds every payment through its effective channel (cash at
    once, electronic PENDING). At most one VOID per sale (unique index). The sale stays COMPLETED."""
    reason_code, reason_text = _reason(reason_code, reason_text)
    try:
        session = _open_session(db, lock=True)
        sale = _lock_sale(db, sale_id)
        existing = _existing_return(db, sale, idempotency_key)
        if existing is not None:
            return existing
        if session is None:
            raise _session_closed()
        returns = _returns(db, sale.id)
        _check_money_operation(db, sale, returns)
        if returns:
            raise HTTPException(409, RETURNS_BLOCK_VOID_MESSAGE)
        if _exchanges(db, sale.id):
            raise HTTPException(409, EXCHANGES_BLOCK_VOID_MESSAGE)
        if _has_external_granel(db, sale):
            raise HTTPException(409, EXTERNAL_GRANEL_VOID_MESSAGE)
        effective = effective_channels(db, sale.payments)
        accounts = effective_account_ids(db, sale.payments)
        units = _units(sale, [])
        sale_return = _register(
            db, sale, session, kind="VOID", reason_code=reason_code, reason_text=reason_text,
            performed_by_text=performed_by_text, idempotency_key=idempotency_key,
            lines=[{"unit": units[(SALE_ITEM, item.id)], "quantity": item.quantity, "amount": item.line_total,
                    "restock": restock and item.product_id is not None} for item in sale.items],
            refunds=[(effective[p.id], p.amount, p.id, accounts[p.id]) for p in sale.payments])
        db.commit(); db.refresh(sale_return)
        return sale_return
    except Exception:
        db.rollback()
        raise


def _confirm_account(db: Session, record, channel: PaymentChannel, account_id: UUID | None) -> None:
    """At confirmation the cashier knows which account the money really left from (0028). An account chosen
    earlier is kept (never silently replaced); an unknown one is set now or, if the channel requires it, asked."""
    if record.affects_cash:
        return
    if record.account_id is not None:
        if account_id is not None and account_id != record.account_id:
            raise HTTPException(409, "La cuenta de origen ya está registrada")
        return
    if account_id is not None:
        from app.services.business_accounts import require_account
        record.account_id = require_account(db, account_id).id
    elif channel.requires_destination_account:
        raise HTTPException(422, f"Elige de qué cuenta salió el dinero por {channel.name}")


def confirm_refund(db: Session, sale_id: UUID, refund_id: UUID, performed_by_text: str | None = None,
                   account_id: UUID | None = None) -> SaleRefund:
    """The cashier confirms an electronic refund was sent outside the POS. Idempotent. Only records the
    fact: no stock (the RETURN was created when the return was registered), no cash, no jornada needed."""
    try:
        refund = db.scalar(select(SaleRefund).where(SaleRefund.id == refund_id).with_for_update(of=SaleRefund))
        if refund is None:
            raise HTTPException(404, "Reembolso no encontrado")
        sale_return = db.get(SaleReturn, refund.return_id)
        if sale_return.sale_id != sale_id:
            raise HTTPException(404, "Reembolso no encontrado")
        if refund.status == "COMPLETED":
            return refund
        _confirm_account(db, refund, refund.channel, account_id)
        refund.status = "COMPLETED"
        refund.confirmed_at = _now()
        refund.confirmed_by = get_operator(db).id
        refund.confirmed_by_text = _clean(performed_by_text)
        db.commit(); db.refresh(refund)
        return refund
    except Exception:
        db.rollback()
        raise


# ---- payment corrections -----------------------------------------------------------------------------

def _correction_account(db: Session, current: PaymentChannel, current_account: UUID | None,
                        target: PaymentChannel, explicit: UUID | None) -> UUID | None:
    """Effective destination account AFTER the correction (0028). Cash has none. Money that was in the drawer
    and really went to an account needs it (if the channel requires one). An electronic payment without a
    known account (before 0028 / untracked channel) is never assigned one retroactively (no backfill). An
    electronic one with a known account keeps it unless the cashier says otherwise."""
    from app.services.business_accounts import destination_for, require_account
    if target.affects_cash:
        if explicit is not None:
            raise HTTPException(422, "El efectivo no llega a una cuenta: no indiques cuenta destino")
        return None
    if current.affects_cash:
        return destination_for(db, target, explicit)
    if current_account is None:
        if explicit is not None:
            raise HTTPException(422, "Este pago no tiene cuenta destino registrada (es anterior al control de "
                                     "cuentas): la corrección no le asigna una")
        return None
    if explicit is not None:
        return require_account(db, explicit, label="La cuenta destino").id
    return current_account if target.requires_destination_account else None


def correct_payment(db: Session, sale_id: UUID, payment_id: UUID, *, to_channel_id: UUID, reason: str,
                    idempotency_key: str, performed_by_text: str | None = None,
                    to_account_id: UUID | None = None) -> PaymentCorrection:
    """Append-only channel correction. Cash effect = effective channel BEFORE vs AFTER this correction:
    electronic -> electronic: none; cash -> electronic: CashMovement -amount; electronic -> cash: +amount.
    A correction that moves cash needs the open jornada AND a sale of that jornada (closed jornadas are
    never altered)."""
    reason = _clean(reason)
    if not reason:
        raise HTTPException(422, "Escribe el motivo de la corrección")
    try:
        session = _open_session(db, lock=True)
        sale = _lock_sale(db, sale_id)
        existing = db.scalar(select(PaymentCorrection).where(PaymentCorrection.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.payment_id != payment_id:
                raise HTTPException(409, "La clave de idempotencia ya se usó en otro pago")
            return existing
        returns = _returns(db, sale.id)
        _check_money_operation(db, sale, returns)
        if returns:
            raise HTTPException(409, RETURNS_BLOCK_CORRECTION_MESSAGE)
        payment = db.get(Payment, payment_id)
        if payment is None or payment.sale_id != sale.id:
            raise HTTPException(404, "Pago no encontrado en esta venta")
        target = db.get(PaymentChannel, to_channel_id)
        if target is None:
            raise HTTPException(404, "Canal de pago no encontrado")
        if not target.active:
            raise HTTPException(409, "No se puede usar un canal de pago inactivo")
        current = effective_channels(db, [payment])[payment.id]
        current_account = effective_account_ids(db, [payment])[payment.id]
        new_account = _correction_account(db, current, current_account, target, to_account_id)
        if current.id == target.id and new_account == current_account:
            raise HTTPException(409, f"El pago ya está registrado como {target.name}"
                                     + (" en esa cuenta" if new_account else ""))
        user = get_operator(db)
        delta = ZERO
        if current.affects_cash and not target.affects_cash:
            delta = -payment.amount
        elif target.affects_cash and not current.affects_cash:
            delta = payment.amount
        if delta:
            if session is None:
                raise HTTPException(409, detail={"code": "OPERATIONAL_SESSION_CLOSED", "message": CASH_CORRECTION_NO_SESSION})
            if _utc(sale.completed_at) < _utc(session.opened_at):
                raise HTTPException(409, CASH_CORRECTION_OLD_SALE)
        sequence = (db.scalar(select(func.max(PaymentCorrection.sequence))
                              .where(PaymentCorrection.payment_id == payment.id)) or 0) + 1
        correction = PaymentCorrection(
            sale_id=sale.id, payment_id=payment.id, sequence=sequence, from_channel_id=current.id,
            to_channel_id=target.id, from_account_id=current_account, to_account_id=new_account,
            amount=payment.amount, reason=reason[:500],
            cash_session_id=session.id if session else None, user_id=user.id,
            performed_by_text=_clean(performed_by_text), idempotency_key=idempotency_key, created_at=_now())
        if delta:
            movement = CashMovement(cash_session_id=session.id, movement_type=CashMovementType.PAYMENT_CORRECTION,
                                    amount=delta, reference_id=sale.id, user_id=user.id,
                                    notes=f"Corrección de pago {sale.number}: {current.name} → {target.name}")
            db.add(movement); db.flush()
            correction.cash_movement_id = movement.id
        db.add(correction); db.commit(); db.refresh(correction)
        return correction
    except Exception:
        db.rollback()
        raise


# ---- product exchanges ---------------------------------------------------------------------------------

def _settlement_of(difference: Decimal) -> str:
    return "EVEN" if difference == 0 else "CUSTOMER_PAYS" if difference > 0 else "STORE_REFUNDS"


def _exchange_out(exchange: SaleExchange, index: int, units: dict, consumed_qty: dict, accounts: dict | None = None) -> dict:
    money = exchange.money
    accounts = accounts or {}
    return {
        "id": exchange.id, "number": index, "reason_code": exchange.reason_code,
        "reason_label": EXCHANGE_REASONS.get(exchange.reason_code, exchange.reason_code),
        "reason_text": exchange.reason_text, "returned_credit": exchange.returned_credit,
        "replacement_total": exchange.replacement_total, "difference": exchange.difference,
        "settlement": exchange.settlement, "settlement_amount": abs(exchange.difference),
        "status": exchange.status, "created_at": exchange.created_at, "performed_by_text": exchange.performed_by_text,
        "returned": [{"sale_item_id": line.sale_item_id, "replacement_line_id": line.source_replacement_line_id,
                      "product_name": units.get(_line_key(line), {}).get("name"),
                      "origin": units.get(_line_key(line), {}).get("origin"),
                      "quantity": line.quantity, "unit_credit": line.unit_credit,
                      "line_credit": line.line_credit, "restock": line.restock} for line in exchange.returned],
        "replacements": [{"id": line.id, "product_id": line.product_id, "manual_kind": line.manual_kind,
                          "product_name": line.product_name, "quantity": line.quantity, "unit_price": line.unit_price,
                          "line_total": line.line_total, "category_code": line.category_code,
                          "later_consumed_quantity": consumed_qty.get((EXCHANGE_LINE, line.id), ZERO)}
                         for line in exchange.replacements],
        "money": None if money is None else {
            "id": money.id, "direction": money.direction, "channel_id": money.payment_channel_id,
            "channel_name": money.channel.name, "amount": money.amount, "affects_cash": money.affects_cash,
            "account_id": money.account_id, "account_name": accounts.get(money.account_id),
            "status": money.status, "received_amount": money.received_amount, "change_amount": money.change_amount,
            "confirmed_at": money.confirmed_at},
    }


def _plan_exchange(db: Session, sale: Sale, returns: list[SaleReturn], exchanges: list[SaleExchange],
                   return_lines: list[dict], replacement_lines: list[dict], *, lock_products: bool) -> dict:
    """Validates the exchange and computes it, without writing. Credit = the value the returned units were
    DELIVERED with (original sale line or earlier exchange line; never today's price); replacement = current
    catalog price (or the manual amount); difference = replacement - credit (signed). Granel settled by the
    scale is not a sale line, so it can never become POS credit."""
    from app.services.inventory import stock_expression
    if not return_lines:
        raise HTTPException(422, "Elige qué producto devuelve el cliente")
    if not replacement_lines:
        raise HTTPException(422, "Agrega el producto que se lleva el cliente")
    units = _units(sale, exchanges)
    consumed_qty, consumed_amount = _consumed(returns, exchanges)
    returned = _plan_units(units, consumed_qty, consumed_amount, return_lines, "cambiar")
    replacements = []
    for line in replacement_lines:
        quantity = Decimal(str(line.get("quantity", 1)))
        if quantity <= 0:
            raise HTTPException(422, "La cantidad del producto nuevo debe ser mayor que cero")
        if line.get("product_id"):
            product = db.get(Product, line["product_id"])
            if product is None:
                raise HTTPException(404, "Producto no encontrado")
            if not product.active:
                raise HTTPException(409, f"{product.name} está inactivo")
            replacements.append({"product": product, "manual_kind": None, "name": product.name,
                                 "category_code": product.operational_category.code if product.operational_category
                                 else "SIN_CLASIFICAR", "quantity": quantity, "unit_price": product.sale_price,
                                 "unit_cost": product.unit_cost,
                                 "line_total": (quantity * product.sale_price).quantize(CENT)})
            continue
        kind = (line.get("manual_kind") or "").upper()
        if kind == "GRANEL":
            raise HTTPException(422, GRANEL_REPLACEMENT_MESSAGE)
        if kind not in REPLACEMENT_MANUAL_CATEGORY:
            raise HTTPException(422, "Indica el producto (catálogo) o un producto manual Súper, Bulto u Otro")
        description = " ".join((line.get("description") or "").split())
        amount = Decimal(str(line.get("amount") or 0))
        if not description or amount <= 0:
            raise HTTPException(422, "El producto manual necesita descripción y valor")
        replacements.append({"product": None, "manual_kind": kind, "name": description[:180],
                             "category_code": REPLACEMENT_MANUAL_CATEGORY[kind], "quantity": quantity,
                             "unit_price": amount, "unit_cost": ZERO, "line_total": (quantity * amount).quantize(CENT)})
    # Stock of the delivered products (a unit restocked in this same exchange counts as available).
    needed: dict[UUID, Decimal] = defaultdict(lambda: ZERO)
    for line in replacements:
        if line["product"] is not None:
            needed[line["product"].id] += line["quantity"]
    if needed:
        if lock_products:
            db.scalars(select(Product).where(Product.id.in_(list(needed))).with_for_update(of=Product)).all()
        stocks = dict(db.execute(select(InventoryMovement.product_id, stock_expression())
                                 .where(InventoryMovement.product_id.in_(list(needed)))
                                 .group_by(InventoryMovement.product_id)).all())
        for line in returned:
            product_id = line["unit"]["product_id"]
            if line["restock"] and product_id in needed:
                stocks[product_id] = Decimal(stocks.get(product_id, 0)) + line["quantity"]
        for line in replacements:
            product = line["product"]
            if product is not None and Decimal(stocks.get(product.id, 0)) < needed[product.id]:
                raise HTTPException(409, f"Stock insuficiente para {product.name}")
    credit = sum((line["amount"] for line in returned), ZERO)
    total = sum((line["line_total"] for line in replacements), ZERO)
    return {"returned": returned, "replacements": replacements, "returned_credit": credit,
            "replacement_total": total, "difference": total - credit}


def _plan_out(plan: dict) -> dict:
    difference = plan["difference"]
    return {
        "returned_credit": plan["returned_credit"], "replacement_total": plan["replacement_total"],
        "difference": difference, "settlement": _settlement_of(difference), "settlement_amount": abs(difference),
        "returned": [{"source_type": line["unit"]["key"][0], "source_id": line["unit"]["key"][1],
                      "product_name": line["unit"]["name"], "origin": line["unit"]["origin"],
                      "quantity": line["quantity"], "unit_credit": line["unit"]["unit_price"],
                      "line_credit": line["amount"], "restock": line["restock"]} for line in plan["returned"]],
        "replacements": [{"product_id": line["product"].id if line["product"] else None,
                          "manual_kind": line["manual_kind"], "product_name": line["name"],
                          "quantity": line["quantity"], "unit_price": line["unit_price"], "line_total": line["line_total"]}
                         for line in plan["replacements"]],
    }


def _check_exchange_allowed(db: Session, sale: Sale, returns: list[SaleReturn]) -> None:
    if sale.status != SaleStatus.COMPLETED:
        raise HTTPException(409, NOT_COMPLETED_MESSAGE)
    if any(r.kind == "VOID" for r in returns):
        raise HTTPException(409, VOIDED_MESSAGE)
    message = invoice_block(db, sale, exchange=True)
    if message:
        raise HTTPException(409, detail={"code": "ELECTRONIC_INVOICE", "message": message})


def preview_exchange(db: Session, sale_id: UUID, *, return_lines: list[dict], replacement_lines: list[dict]) -> dict:
    """Same computation as the real exchange, no writes and no locks: what the summary step shows."""
    sale = db.get(Sale, sale_id)
    if sale is None:
        raise HTTPException(404, "Venta no encontrada")
    returns = _returns(db, sale.id)
    _check_exchange_allowed(db, sale, returns)
    return _plan_out(_plan_exchange(db, sale, returns, _exchanges(db, sale.id), return_lines, replacement_lines,
                                    lock_products=False))


def create_exchange(db: Session, sale_id: UUID, *, return_lines: list[dict], replacement_lines: list[dict],
                    reason_code: str, reason_text: str | None, settlement: dict | None, idempotency_key: str,
                    performed_by_text: str | None = None) -> SaleExchange:
    """PRODUCT EXCHANGE. One transaction, sale row locked: RETURN (+) per restocked returned unit,
    EXCHANGE_OUT (-) per delivered catalog product, and ONLY the signed difference moves money:
      EVEN           — nothing (no settlement accepted);
      CUSTOMER_PAYS  — `settlement` = how the customer pays it (cash -> CashMovement EXCHANGE +);
      STORE_REFUNDS  — `settlement` = how the store gives it back, chosen explicitly by the cashier (cash ->
                       CashMovement EXCHANGE_REFUND -, at once; electronic -> PENDING until confirmed).
    Without a settlement a cheaper replacement is refused: the cashier adds another product or chooses to
    give the difference back (no store credit, no voucher)."""
    if reason_code not in EXCHANGE_REASONS:
        raise HTTPException(422, "Motivo de cambio desconocido")
    reason_text = _clean(reason_text)
    if reason_code == "OTHER" and not reason_text:
        raise HTTPException(422, "Describe el motivo")
    try:
        session = _open_session(db, lock=True)
        sale = _lock_sale(db, sale_id)
        existing = db.scalar(select(SaleExchange).where(SaleExchange.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.sale_id != sale.id:
                raise HTTPException(409, "La clave de idempotencia ya se usó en otra venta")
            return existing
        if session is None:
            raise _session_closed()
        returns = _returns(db, sale.id)
        _check_exchange_allowed(db, sale, returns)
        plan = _plan_exchange(db, sale, returns, _exchanges(db, sale.id), return_lines, replacement_lines,
                              lock_products=True)
        difference = plan["difference"]
        kind = _settlement_of(difference)
        channel = None
        if kind == "EVEN" and settlement:
            raise HTTPException(422, "Sin diferencia por pagar: no se mueve dinero")
        if kind != "EVEN":
            if not settlement or not settlement.get("channel_id"):
                if kind == "CUSTOMER_PAYS":
                    raise HTTPException(422, f"Elige el medio de pago de la diferencia de {_pesos(difference)}")
                raise HTTPException(409, detail={"code": "EXCHANGE_CREDIT_REMAINING",
                                                 "message": CREDIT_REMAINING_MESSAGE.format(amount=_pesos(-difference))})
            channel = db.get(PaymentChannel, settlement["channel_id"])
            if channel is None:
                raise HTTPException(404, "Canal de pago no encontrado")
            if not channel.active:
                raise HTTPException(409, "No se puede usar un canal de pago inactivo")
            from app.services.business_accounts import destination_for
            settlement = {**settlement, "account_id": destination_for(db, channel, settlement.get("account_id"))}
        user = get_operator(db)
        exchange = SaleExchange(sale_id=sale.id, reason_code=reason_code, reason_text=reason_text,
                                returned_credit=plan["returned_credit"], replacement_total=plan["replacement_total"],
                                difference=difference, settlement=kind, status="COMPLETED",
                                cash_session_id=session.id, user_id=user.id,
                                performed_by_text=_clean(performed_by_text), idempotency_key=idempotency_key,
                                created_at=_now())
        db.add(exchange); db.flush()
        label = f"Cambio {sale.number}"
        for line in plan["returned"]:
            unit, movement_id = line["unit"], None
            if line["restock"]:
                movement = InventoryMovement(product_id=unit["product_id"], movement_type=InventoryMovementType.RETURN,
                                             quantity=line["quantity"], unit_cost=unit["unit_cost"],
                                             reference_id=exchange.id, reason=label, user_id=user.id)
                db.add(movement); db.flush()
                movement_id = movement.id
            source, source_id = unit["key"]
            exchange.returned.append(SaleExchangeReturnLine(
                sale_item_id=source_id if source == SALE_ITEM else None,
                source_replacement_line_id=source_id if source == EXCHANGE_LINE else None,
                quantity=line["quantity"], unit_credit=unit["unit_price"], line_credit=line["amount"],
                category_code=unit["category_code"], restock=line["restock"], inventory_movement_id=movement_id))
        for line in plan["replacements"]:
            movement_id = None
            product = line["product"]
            if product is not None:
                movement = InventoryMovement(product_id=product.id, movement_type=InventoryMovementType.EXCHANGE_OUT,
                                             quantity=-line["quantity"], unit_cost=line["unit_cost"],
                                             reference_id=exchange.id, reason=label, user_id=user.id)
                db.add(movement); db.flush()
                movement_id = movement.id
            exchange.replacements.append(SaleExchangeReplacementLine(
                product_id=product.id if product else None, manual_kind=line["manual_kind"], product_name=line["name"],
                category_code=line["category_code"], quantity=line["quantity"], unit_price=line["unit_price"],
                unit_cost=line["unit_cost"], line_total=line["line_total"], inventory_movement_id=movement_id))
        if channel is not None:
            exchange.money = _settle(db, sale, session, user, channel, settlement, difference, label)
        db.commit(); db.refresh(exchange)
        return exchange
    except Exception:
        db.rollback()
        raise


def _settle(db: Session, sale: Sale, session: CashSession, user, channel: PaymentChannel, settlement: dict,
            difference: Decimal, label: str) -> SaleExchangeSettlement:
    """The money of an exchange: only |difference|, one record, one cash movement at most."""
    from app.services.payment_methods import requires_card_type
    amount = abs(difference)
    if difference > 0:                                                    # CUSTOMER_PAYS
        card_type = settlement.get("card_type")
        if card_type and not requires_card_type(channel.payment_type):
            raise HTTPException(422, f"Débito/Crédito solo aplica a canales de tarjeta, no a {channel.name}")
        received = change = None
        if channel.affects_cash:
            received = Decimal(str(settlement.get("received_amount") or amount))
            if received < amount:
                raise HTTPException(400, "El dinero recibido es menor que la diferencia a pagar")
            change = received - amount
        money = SaleExchangeSettlement(direction="IN", payment_channel_id=channel.id, amount=amount,
                                       affects_cash=channel.affects_cash, status="COMPLETED",
                                       account_id=settlement.get("account_id"),
                                       received_amount=received, change_amount=change, card_type=card_type,
                                       reference=_clean(settlement.get("reference")), created_at=_now())
        if channel.affects_cash:
            movement = CashMovement(cash_session_id=session.id, movement_type=CashMovementType.EXCHANGE,
                                    amount=amount, reference_id=sale.id, user_id=user.id,
                                    notes=f"{label}: diferencia cobrada")
            db.add(movement); db.flush()
            money.cash_movement_id = movement.id
        return money
    money = SaleExchangeSettlement(direction="OUT", payment_channel_id=channel.id, amount=amount,     # STORE_REFUNDS
                                   affects_cash=channel.affects_cash, status="PENDING",
                                   account_id=settlement.get("account_id"),
                                   reference=_clean(settlement.get("reference")), created_at=_now())
    if channel.affects_cash:
        movement = CashMovement(cash_session_id=session.id, movement_type=CashMovementType.EXCHANGE_REFUND,
                                amount=-amount, reference_id=sale.id, user_id=user.id,
                                notes=f"{label}: diferencia devuelta")
        db.add(movement); db.flush()
        money.cash_movement_id = movement.id
        money.status = "COMPLETED"
        money.confirmed_at, money.confirmed_by = _now(), user.id
    return money


def confirm_exchange_refund(db: Session, sale_id: UUID, exchange_id: UUID,
                            performed_by_text: str | None = None, account_id: UUID | None = None) -> SaleExchangeSettlement:
    """The cashier confirms the difference of an exchange was given back electronically, outside the POS.
    Idempotent; only records the fact (no stock, no cash, no jornada needed)."""
    try:
        money = db.scalar(select(SaleExchangeSettlement).where(SaleExchangeSettlement.exchange_id == exchange_id)
                          .with_for_update(of=SaleExchangeSettlement))
        exchange = db.get(SaleExchange, exchange_id)
        if money is None or exchange is None or exchange.sale_id != sale_id or money.direction != "OUT":
            raise HTTPException(404, "Devolución de diferencia no encontrada")
        if money.status == "COMPLETED":
            return money
        _confirm_account(db, money, money.channel, account_id)
        money.status = "COMPLETED"
        money.confirmed_at = _now()
        money.confirmed_by = get_operator(db).id
        money.confirmed_by_text = _clean(performed_by_text)
        db.commit(); db.refresh(money)
        return money
    except Exception:
        db.rollback()
        raise


# ---- read models for reports -------------------------------------------------------------------------

def completed_refunds_by_channel(db: Session, start: datetime | None = None,
                                 end: datetime | None = None,
                                 cash_session_id: UUID | None = None) -> dict[UUID, Decimal]:
    """Amounts actually refunded, grouped by channel.

    Pending digital transfers are obligations, not money already returned.
    Cash refunds are completed atomically with their CashMovement.  This
    read-model helper keeps the original sale and refund records untouched.
    """
    conditions = [SaleRefund.affects_cash.is_(True) | (SaleRefund.status == "COMPLETED")]
    if cash_session_id is not None:
        conditions.append(SaleReturn.cash_session_id == cash_session_id)
    else:
        if start is not None:
            conditions.append(SaleReturn.created_at >= start)
        if end is not None:
            conditions.append(SaleReturn.created_at <= end)
    rows = db.execute(select(SaleRefund.channel_id, func.sum(SaleRefund.amount))
        .join(SaleReturn, SaleReturn.id == SaleRefund.return_id)
        .where(*conditions).group_by(SaleRefund.channel_id)).all()
    return {channel_id: Decimal(amount) for channel_id, amount in rows}

def returns_summary(db: Session, date_from: datetime, date_to: datetime) -> dict:
    """Money returns registered in [date_from, date_to] (a return belongs to the date / jornada it was made,
    not to the sale's). Cash refunds are allocated over the categories of the returned lines with the same
    proportional rule as the daily summary; granel lines never get cash (POS granel is paid
    electronically)."""
    from app.services.operations import _allocate
    from app.services.reporting_categories import reporting_category
    returns = db.scalars(select(SaleReturn).where(SaleReturn.created_at >= date_from,
                                                  SaleReturn.created_at <= date_to)).all()
    total = cash = electronic_done = electronic_pending = ZERO
    by_category: dict[str, Decimal] = defaultdict(lambda: ZERO)
    cash_by_category: dict[str, Decimal] = defaultdict(lambda: ZERO)
    voids = 0
    for sale_return in returns:
        total += sale_return.total_amount
        voids += sale_return.kind == "VOID"
        sale = db.get(Sale, sale_return.sale_id)
        items = {item.id: item for item in sale.items}
        replacements = {line.id: line for exchange in _exchanges(db, sale.id) for line in exchange.replacements}
        categories: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for line in sale_return.lines:
            item = items.get(line.sale_item_id) if line.sale_item_id else None
            replacement = replacements.get(line.source_replacement_line_id) if line.source_replacement_line_id else None
            code = reporting_category(
                line.category_code,
                manual_kind=item.manual_kind if item is not None else (replacement.manual_kind if replacement else None),
                sale_source=sale.source if item is not None else None)
            by_category[code] += line.amount
            if item is None or not _is_granel(sale, item):
                categories[code] += line.amount
        return_cash = ZERO
        for refund in sale_return.refunds:
            if refund.affects_cash:
                return_cash += refund.amount
            elif refund.status == "COMPLETED":
                electronic_done += refund.amount
            else:
                electronic_pending += refund.amount
        cash += return_cash
        for code, amount in _allocate(return_cash, dict(categories), sum(categories.values(), ZERO)).items():
            cash_by_category[code] += amount
    refunded_total = cash + electronic_done
    return {"returns_total": total, "refunded_total": refunded_total,
            "refunds_cash": cash, "refunds_electronic": electronic_done,
            "refunds_electronic_pending": electronic_pending, "return_count": len(returns), "void_count": voids,
            "by_category": dict(by_category), "cash_by_category": dict(cash_by_category)}


def exchanges_summary(db: Session, date_from: datetime, date_to: datetime) -> dict:
    """Product exchanges made in [date_from, date_to] (by exchange date / jornada). Credit and replacement
    totals are product movement, never a sale nor a refund; only the differences are money: collected from
    the customer (CUSTOMER_PAYS) or given back (STORE_REFUNDS). Cash differences are also allocated over
    categories for the block-1 asides: collected over the replacement lines, given back over the returned."""
    from app.services.operations import _allocate
    exchanges = db.scalars(select(SaleExchange).where(SaleExchange.created_at >= date_from,
                                                      SaleExchange.created_at <= date_to)).all()
    credit = replacement = collected = collected_cash = collected_electronic = ZERO
    refunded = refunded_cash = refunded_electronic = refunded_pending = ZERO
    counts = {"EVEN": 0, "CUSTOMER_PAYS": 0, "STORE_REFUNDS": 0}
    cash_by_category: dict[str, Decimal] = defaultdict(lambda: ZERO)
    refund_cash_by_category: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for exchange in exchanges:
        credit += exchange.returned_credit
        replacement += exchange.replacement_total
        counts[exchange.settlement] += 1
        money = exchange.money
        if money is None:
            continue
        if money.direction == "IN":
            collected += money.amount
            if money.affects_cash:
                collected_cash += money.amount
                lines = {line.category_code: ZERO for line in exchange.replacements}
                for line in exchange.replacements:
                    lines[line.category_code] += line.line_total
                for code, amount in _allocate(money.amount, lines, sum(lines.values(), ZERO)).items():
                    cash_by_category[code] += amount
            else:
                collected_electronic += money.amount
        else:
            refunded += money.amount
            if money.affects_cash:
                refunded_cash += money.amount
                lines = {line.category_code: ZERO for line in exchange.returned}
                for line in exchange.returned:
                    lines[line.category_code] += line.line_credit
                for code, amount in _allocate(money.amount, lines, sum(lines.values(), ZERO)).items():
                    refund_cash_by_category[code] += amount
            elif money.status == "COMPLETED":
                refunded_electronic += money.amount
            else:
                refunded_pending += money.amount
    return {"exchange_count": len(exchanges), "same_value_count": counts["EVEN"],
            "customer_pays_count": counts["CUSTOMER_PAYS"], "store_refunds_count": counts["STORE_REFUNDS"],
            "returned_credit": credit, "replacement_total": replacement,
            "difference_collected": collected, "difference_cash": collected_cash,
            "difference_electronic": collected_electronic,
            "difference_refunded": refunded, "refunded_cash": refunded_cash,
            "refunded_electronic": refunded_electronic, "refunded_electronic_pending": refunded_pending,
            "cash_by_category": dict(cash_by_category), "refund_cash_by_category": dict(refund_cash_by_category)}
