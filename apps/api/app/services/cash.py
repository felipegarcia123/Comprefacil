from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import case, distinct, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.services.effective_payments import effective_channel_id_column, effective_channels
from app.domain.enums import CashMovementType, WithdrawalReason
from app.models import (BusinessAccount, BusinessAccountSnapshot, CashMovement, CashSession, InventoryValuationSnapshot, Payment, PaymentChannel, Sale, SaleExchange, SaleRefund, SaleReturn,
                        SupplierPayment, Purchase, Supplier)
from app.schemas.cash import (CashHistoryRow, CashMovementOut, CashSessionOut,
                              CashSummaryOut, ChannelCashSummary, FinancialSessionMovement, SupplierPaymentSummary,
                              CloseCashSessionIn)
from app.services.pos import get_operator
from app.services.post_sale import completed_refunds_by_channel
from app.services.operational_session import require_open_session

ZERO = Decimal("0")


def _session_out(session: CashSession) -> CashSessionOut:
    return CashSessionOut(
        id=session.id, status="OPEN" if session.closed_at is None else "CLOSED",
        opening_amount=session.opening_amount, opening_note=session.opening_note,
        opened_at=session.opened_at, opened_by=session.opened_by,
        closed_at=session.closed_at, closed_by=session.closed_by,
        closing_expected=session.closing_expected, closing_actual=session.closing_actual,
        difference=session.difference, closing_note=session.closing_note)


def current_session(db: Session, lock: bool = False) -> CashSession | None:
    stmt = select(CashSession).where(CashSession.closed_at.is_(None))
    if lock:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def expected_cash(db: Session, session: CashSession) -> Decimal:
    movements = db.scalar(select(func.coalesce(func.sum(CashMovement.amount), 0)).where(
        CashMovement.cash_session_id == session.id))
    return session.opening_amount + Decimal(movements)


def open_cash_session(db: Session, opening_amount: Decimal, note: str | None) -> CashSession:
    if current_session(db, lock=True):
        raise HTTPException(409, "Ya existe una sesión de caja abierta")
    session = CashSession(opening_amount=opening_amount, opening_note=note,
                          opened_at=datetime.now(timezone.utc),
                          opened_by=get_operator(db).id)
    try:
        db.add(session); db.commit(); db.refresh(session)
        return session
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Ya existe una sesión de caja abierta")


def add_manual_movement(db: Session, movement_type: CashMovementType,
                        amount: Decimal, notes: str,
                        withdrawal_reason: WithdrawalReason | None = None) -> CashMovement:
    session = require_open_session(db, lock=True)
    signed = amount
    if movement_type == CashMovementType.WITHDRAWAL:
        if amount > expected_cash(db, session):
            raise HTTPException(409, "El retiro supera el efectivo esperado en caja")
        signed = -amount
    if expected_cash(db, session) + signed < 0:
        raise HTTPException(409, "El movimiento dejaría un efectivo esperado negativo")
    movement = CashMovement(cash_session_id=session.id, movement_type=movement_type,
                            amount=signed, notes=notes, withdrawal_reason=withdrawal_reason,
                            user_id=get_operator(db).id)
    db.add(movement); db.commit(); db.refresh(movement)
    return movement


def close_cash_session(db: Session, body: CloseCashSessionIn) -> CashSession:
    try:
        session = current_session(db, lock=True)
        if not session:
            raise HTTPException(409, "No existe una sesión de caja abierta")
        actual_expected = expected_cash(db, session)
        if actual_expected != body.expected_amount:
            raise HTTPException(409, "El efectivo esperado cambió. Actualiza el resumen antes de cerrar")
        difference = body.counted_amount - actual_expected
        if difference != 0 and not body.note:
            raise HTTPException(422, "La observación es obligatoria cuando existe diferencia")
        session.closing_expected = actual_expected
        session.closing_actual = body.counted_amount
        session.difference = difference
        session.closing_note = body.note
        session.closed_by = get_operator(db).id
        session.closed_at = datetime.now(timezone.utc)
        # 0028, same transaction as the close (both or neither): the inventory photo at cost and, if typed,
        # the account balances. Neither changes expected cash, the difference above or Block 1.
        from app.services.business_accounts import register_balances
        from app.services.inventory_valuation import snapshot_for_session
        db.flush()
        snapshot_for_session(db, session)
        if body.account_balances:
            register_balances(db, session, [balance.model_dump() for balance in body.account_balances])
        if body.declared_patrimony is not None:
            from app.services.patrimony import declare
            declare(db, session, body.declared_patrimony.value, body.declared_patrimony.note,
                    body.declared_patrimony.performed_by_text)
        db.commit(); db.refresh(session)
        # The financial close above is already committed; report generation
        # must never affect it. See generate_report_after_close for details.
        from app.services.daily_reports import generate_report_after_close
        generate_report_after_close(db, session)
        return session
    except Exception:
        db.rollback()
        raise


def _movement_totals(db: Session, session_id: UUID) -> dict[CashMovementType, Decimal]:
    rows = db.execute(select(CashMovement.movement_type, func.sum(CashMovement.amount)).where(
        CashMovement.cash_session_id == session_id).group_by(CashMovement.movement_type)).all()
    return {kind: Decimal(amount) for kind, amount in rows}


def cash_summary(db: Session, session: CashSession) -> CashSummaryOut:
    totals = _movement_totals(db, session.id)
    cutoff = session.closed_at or datetime.now(timezone.utc)
    payment_rows = db.execute(
        select(PaymentChannel.id, PaymentChannel.name, PaymentChannel.payment_type,
               func.sum(Payment.amount), func.count(Payment.id))
        .join(Payment, effective_channel_id_column() == PaymentChannel.id)
        .join(Sale, Sale.id == Payment.sale_id)
        .where(Sale.completed_at >= session.opened_at, Sale.completed_at <= cutoff)
        .group_by(PaymentChannel.id).order_by(PaymentChannel.display_order)).all()
    refunds_by_channel = completed_refunds_by_channel(db, cash_session_id=session.id)
    total_sales = sum((Decimal(row[3]) - refunds_by_channel.get(row[0], ZERO) for row in payment_rows), ZERO)
    transaction_count = db.scalar(select(func.count(distinct(Payment.sale_id)))
        .join(Sale, Sale.id == Payment.sale_id).where(
            Sale.completed_at >= session.opened_at, Sale.completed_at <= cutoff)) or 0
    movement_rows = db.execute(
        select(CashMovement, Sale.number).outerjoin(Sale, Sale.id == CashMovement.reference_id)
        .where(CashMovement.cash_session_id == session.id)
        .order_by(CashMovement.created_at.desc()).limit(100)).all()
    movements = [CashMovementOut(
        id=movement.id, movement_type=movement.movement_type, amount=movement.amount,
        reference_id=movement.reference_id, reference=sale_number, notes=movement.notes,
        created_at=movement.created_at, user_id=movement.user_id)
        for movement, sale_number in movement_rows]
    cash_sales = totals.get(CashMovementType.SALE, ZERO)
    deposits = totals.get(CashMovementType.DEPOSIT, ZERO)
    withdrawals = totals.get(CashMovementType.WITHDRAWAL, ZERO)
    refunds = totals.get(CashMovementType.REFUND, ZERO)
    adjustments = totals.get(CashMovementType.ADJUSTMENT, ZERO)
    expenses = totals.get(CashMovementType.EXPENSE, ZERO)
    supplier_payments_cash = totals.get(CashMovementType.SUPPLIER_PAYMENT, ZERO)
    payment_corrections = totals.get(CashMovementType.PAYMENT_CORRECTION, ZERO)
    exchanges_cash = totals.get(CashMovementType.EXCHANGE, ZERO)
    exchange_refunds_cash = abs(totals.get(CashMovementType.EXCHANGE_REFUND, ZERO))
    supplier_rows = db.execute(
        select(PaymentChannel.id, PaymentChannel.name, PaymentChannel.payment_type,
               func.sum(SupplierPayment.amount), func.count(SupplierPayment.id))
        .join(SupplierPayment, SupplierPayment.payment_channel_id == PaymentChannel.id)
        .where(SupplierPayment.paid_at >= session.opened_at, SupplierPayment.paid_at <= cutoff)
        .group_by(PaymentChannel.id).order_by(PaymentChannel.display_order)).all()
    financial = _financial_movements(db, session, cutoff)
    return CashSummaryOut(
        session=_session_out(session), cash_sales=cash_sales, deposits=deposits,
        withdrawals=abs(withdrawals), refunds=abs(refunds), adjustments=adjustments,
        expenses=abs(expenses), supplier_payments_cash=abs(supplier_payments_cash),
        payment_corrections_cash=payment_corrections, exchanges_cash=exchanges_cash,
        exchange_refunds_cash=exchange_refunds_cash,
        expected_cash=session.opening_amount + sum(totals.values(), ZERO),
        total_sales=total_sales, transaction_count=transaction_count,
        payment_channels=[ChannelCashSummary(
            channel_id=row[0], channel_name=row[1], payment_type=row[2],
            amount=Decimal(row[3]) - refunds_by_channel.get(row[0], ZERO), payment_count=row[4]) for row in payment_rows],
        recent_movements=movements, financial_movements=financial,
        supplier_payments_total=sum((Decimal(row[3]) for row in supplier_rows), ZERO),
        supplier_payment_channels=[SupplierPaymentSummary(channel_id=row[0], channel_name=row[1],
            payment_type=row[2], amount=row[3], payment_count=row[4]) for row in supplier_rows])


def _financial_movements(db: Session, session: CashSession, cutoff: datetime) -> list[FinancialSessionMovement]:
    """Read model: combines source records, never persists a second ledger."""
    result: list[FinancialSessionMovement] = []
    sale_rows = db.execute(select(Payment, Sale).join(Sale, Sale.id == Payment.sale_id)
        .where(Sale.completed_at >= session.opened_at, Sale.completed_at <= cutoff)).all()
    effective = effective_channels(db, [payment for payment, _ in sale_rows])
    for payment, sale in sale_rows:
        channel = effective[payment.id]
        result.append(FinancialSessionMovement(id=payment.id, occurred_at=payment.created_at,
            type="SALE", direction="IN", amount=payment.amount, payment_channel=channel.name,
            affects_cash=channel.affects_cash, title=f"Venta {sale.number}",
            subtitle=channel.name, reference_type="SALE", reference_id=sale.id))
    supplier_rows = db.execute(select(SupplierPayment, Purchase, Supplier)
        .join(Purchase, Purchase.id == SupplierPayment.purchase_id).join(Supplier, Supplier.id == Purchase.supplier_id)
        .where(SupplierPayment.paid_at >= session.opened_at, SupplierPayment.paid_at <= cutoff)).all()
    for supplier_payment, purchase, supplier in supplier_rows:
        result.append(FinancialSessionMovement(id=supplier_payment.id, occurred_at=supplier_payment.paid_at,
            type="SUPPLIER_PAYMENT", direction="OUT", amount=supplier_payment.amount,
            payment_channel=supplier_payment.channel.name, affects_cash=supplier_payment.channel.affects_cash,
            title=f"Pago proveedor - {supplier.name}", subtitle=f"Compra {purchase.invoice_number or str(purchase.id)[:8]}",
            reference_type="PURCHASE", reference_id=purchase.id))
    cash_rows = db.scalars(select(CashMovement).where(CashMovement.cash_session_id == session.id,
        CashMovement.movement_type != CashMovementType.SALE)).all()
    referenced = {m.reference_id for m in cash_rows if m.reference_id is not None}
    sale_numbers = set(db.scalars(select(Sale.id).where(Sale.id.in_(referenced)))) if referenced else set()
    for movement in cash_rows:
        # Supplier cash payments are represented by SupplierPayment above, preventing duplicate rows.
        if movement.movement_type in (CashMovementType.SUPPLIER_PAYMENT, CashMovementType.EXCHANGE,
                                      CashMovementType.EXCHANGE_REFUND):
            continue          # exchanges are listed below, once, with what was really exchanged
        labels = {CashMovementType.DEPOSIT: "Ingreso", CashMovementType.WITHDRAWAL: "Retiro",
                  CashMovementType.EXPENSE: "Gasto", CashMovementType.REFUND: "Reembolso",
                  CashMovementType.ADJUSTMENT: "Ajuste", CashMovementType.PAYMENT_CORRECTION: "Corrección de pago"}
        # Post-sale movements point at the sale, so the row opens its detail.
        post_sale = movement.movement_type in (CashMovementType.REFUND, CashMovementType.PAYMENT_CORRECTION) \
            and movement.reference_id in sale_numbers
        result.append(FinancialSessionMovement(id=movement.id, occurred_at=movement.created_at,
            type=movement.movement_type.value, direction="IN" if movement.amount >= 0 else "OUT",
            amount=abs(movement.amount), payment_channel="Efectivo", affects_cash=True,
            title=labels.get(movement.movement_type, movement.movement_type.value.title()), subtitle=movement.notes,
            reference_type="SALE" if post_sale else "CASH_MOVEMENT", reference_id=movement.reference_id,
            withdrawal_reason=movement.withdrawal_reason))
    # Electronic refunds of this jornada move no cash: listed for traceability (pending or confirmed).
    for refund, sale_number, sale_id in db.execute(
            select(SaleRefund, Sale.number, Sale.id).join(SaleReturn, SaleReturn.id == SaleRefund.return_id)
            .join(Sale, Sale.id == SaleReturn.sale_id)
            .where(SaleReturn.cash_session_id == session.id, SaleRefund.affects_cash.is_(False))).all():
        result.append(FinancialSessionMovement(id=refund.id, occurred_at=refund.created_at, type="REFUND",
            direction="OUT", amount=refund.amount, payment_channel=refund.channel.name, affects_cash=False,
            title="Reembolso", subtitle=(f"Pendiente devolver por {refund.channel.name} · {sale_number}"
                                         if refund.status == "PENDING" else f"Reembolsado por {refund.channel.name} · {sale_number}"),
            reference_type="SALE", reference_id=sale_id))
    # Product exchanges of this jornada: "Cambio de producto", never "Reembolso". Amount = only the money
    # that really moved (the difference), 0 for a same-value exchange.
    for exchange, sale_number, sale_id in db.execute(
            select(SaleExchange, Sale.number, Sale.id).join(Sale, Sale.id == SaleExchange.sale_id)
            .where(SaleExchange.cash_session_id == session.id)).all():
        money = exchange.money
        if money is None:
            detail = "Sin diferencia por pagar"
        elif money.direction == "IN":
            detail = "Diferencia cobrada"                                  # the channel is shown already
        else:
            detail = "Pendiente devolver la diferencia" if money.status == "PENDING" else "Diferencia devuelta"
        result.append(FinancialSessionMovement(id=exchange.id, occurred_at=exchange.created_at, type="EXCHANGE",
            direction="OUT" if money is not None and money.direction == "OUT" else "IN",
            amount=abs(exchange.difference), payment_channel=money.channel.name if money else None,
            affects_cash=bool(money and money.affects_cash),
            title=f"Cambio de producto · Venta {sale_number}", subtitle=detail,
            reference_type="SALE", reference_id=sale_id))
    return sorted(result, key=lambda item: item.occurred_at, reverse=True)[:100]


def cash_history(db: Session, limit: int = 90) -> list[CashHistoryRow]:
    sessions = db.scalars(select(CashSession).order_by(CashSession.opened_at.desc()).limit(limit)).all()
    if not sessions:
        return []
    rows = db.execute(select(
        CashMovement.cash_session_id,
        func.sum(case((CashMovement.movement_type == CashMovementType.SALE, CashMovement.amount), else_=0)),
        func.sum(case((CashMovement.movement_type == CashMovementType.DEPOSIT, CashMovement.amount), else_=0)),
        func.sum(case((CashMovement.movement_type == CashMovementType.WITHDRAWAL, CashMovement.amount), else_=0)),
        func.sum(CashMovement.amount)).where(CashMovement.cash_session_id.in_([s.id for s in sessions]))
        .group_by(CashMovement.cash_session_id)).all()
    totals = {row[0]: row[1:] for row in rows}
    ids = [s.id for s in sessions]
    values = dict(db.execute(select(InventoryValuationSnapshot.cash_session_id, InventoryValuationSnapshot.inventory_value)
                             .where(InventoryValuationSnapshot.cash_session_id.in_(ids))).all())
    registered = dict(db.execute(select(BusinessAccountSnapshot.cash_session_id, func.count())
                                 .where(BusinessAccountSnapshot.cash_session_id.in_(ids))
                                 .group_by(BusinessAccountSnapshot.cash_session_id)).all())
    from app.models import PatrimonyDeclaration
    declared = {d.cash_session_id: d.effective_value for d in db.scalars(
        select(PatrimonyDeclaration).where(PatrimonyDeclaration.cash_session_id.in_(ids)))}
    active_accounts = db.scalar(select(func.count()).select_from(BusinessAccount).where(BusinessAccount.active.is_(True))) or 0
    result = []
    for session in sessions:
        sale, deposit, withdrawal, net = totals.get(session.id, (ZERO, ZERO, ZERO, ZERO))
        closed = session.closed_at is not None
        result.append(CashHistoryRow(
            session=_session_out(session), cash_sales=Decimal(sale), deposits=Decimal(deposit),
            withdrawals=abs(Decimal(withdrawal)), expected_cash=session.opening_amount + Decimal(net),
            inventory_value=values.get(session.id), declared_patrimony=declared.get(session.id),
            accounts_registered=registered.get(session.id, 0) if closed else None,
            accounts_total=active_accounts if closed else None))
    return result
