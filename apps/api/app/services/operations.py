from collections import defaultdict
from datetime import datetime, time, timezone
from decimal import Decimal, ROUND_DOWN
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.services.effective_payments import effective_channel_id_column
from app.domain.enums import (CashMovementType, ExpensePaymentSource,
                              InventoryMovementType, PaymentGroup, PaymentMethod,
                              SaleSource, SaleStatus, WithdrawalReason)
from app.models import (CashMovement, CashSession, Expense, InternalConsumption,
                        InventoryMovement, OperationalCategory, Payment,
                        PaymentChannel, Product, Sale, SaleExternalItem, SaleItem)
from app.schemas.operations import (CategorySummary, ChannelBreakdown,
                                    DailyOperationsSummary, ExchangesSummary, ReturnCategoryAmount, ReturnsSummary, ExpenseIn,
                                    InternalConsumptionIn, ManualSaleIn,
                                    OutgoingSummary)
from app.services.cash import current_session
from app.services.inventory import current_stock
from app.services.pos import get_operator, invoice_intent
from app.services.operational_session import require_open_session

ZERO = Decimal("0")
CENT = Decimal("0.01")


def _category(db: Session, code: str) -> OperationalCategory:
    category = db.scalar(select(OperationalCategory).where(OperationalCategory.code == code))
    if not category:
        raise HTTPException(500, f"No existe la categoría operativa {code}")
    return category


def create_manual_sale(db: Session, body: ManualSaleIn) -> Sale:
    require_open_session(db, lock=True)
    previous = db.scalar(select(Sale).where(Sale.idempotency_key == body.idempotency_key))
    if previous:
        return previous
    try:
        channel = db.scalar(select(PaymentChannel).where(
            PaymentChannel.id == body.channel_id).with_for_update())
        if not channel:
            raise HTTPException(404, "Canal de pago no encontrado")
        if not channel.active:
            raise HTTPException(409, "No se puede usar un canal de pago inactivo")
        if body.source == SaleSource.MANUAL_BULK and channel.affects_cash:
            raise HTTPException(409, "Las ventas en efectivo de comida a granel ya se registran en la báscula.")
        session = None
        if channel.affects_cash:
            session = current_session(db, lock=True)
            if not session:
                raise HTTPException(409, "Debes abrir la caja antes de recibir pagos en efectivo.")
        from app.services.business_accounts import destination_for
        destination = destination_for(db, channel, body.destination_account_id)
        category = _category(db, "MASCOTAS_SIN_CODIGO")
        user = get_operator(db)
        sale_id = uuid4()
        requires_invoice, invoice_customer_id = invoice_intent(db, body.electronic_invoice)  # input, before any write
        sale = Sale(
            id=sale_id, number=f"VM-{sale_id.hex[:8].upper()}", status=SaleStatus.COMPLETED,
            source=body.source, notes=body.notes, idempotency_key=body.idempotency_key,
            created_by=user.id, total=body.amount, completed_at=datetime.now(timezone.utc),
            requires_electronic_invoice=requires_invoice, invoice_customer_id=invoice_customer_id)
        sale.items.append(SaleItem(
            product_id=None, operational_category_id=category.id, category_code=category.code,
            product_name=body.description, quantity=Decimal("1"), unit_price=body.amount,
            unit_cost=ZERO, line_total=body.amount))
        sale.payments.append(Payment(
            method=channel.payment_type, payment_channel_id=channel.id, amount=body.amount,
            received_amount=body.amount if channel.affects_cash else None,
            change_amount=ZERO if channel.affects_cash else None, reference=body.reference,
            destination_account_id=destination))
        db.add(sale); db.flush()
        if channel.affects_cash:
            db.add(CashMovement(
                cash_session_id=session.id, movement_type=CashMovementType.SALE,
                amount=body.amount, reference_id=sale.id, notes=body.notes, user_id=user.id))
        # Invoiced only when ticked, like any sale, with their generic Alegra item (no stock).
        from app.services.electronic_invoicing import add_pending_invoice
        add_pending_invoice(db, sale)
        db.commit(); db.refresh(sale)
        return sale
    except IntegrityError:
        db.rollback()
        previous = db.scalar(select(Sale).where(Sale.idempotency_key == body.idempotency_key))
        if previous:
            return previous
        raise
    except Exception:
        db.rollback(); raise


def create_expense(db: Session, body: ExpenseIn) -> Expense:
    require_open_session(db, lock=True)
    previous = db.scalar(select(Expense).where(Expense.idempotency_key == body.idempotency_key))
    if previous:
        return previous
    try:
        channel = None
        if body.payment_channel_id:
            channel = db.scalar(select(PaymentChannel).where(
                PaymentChannel.id == body.payment_channel_id).with_for_update())
            if not channel:
                raise HTTPException(404, "Canal de pago no encontrado")
            if not channel.active:
                raise HTTPException(409, "No se puede usar un canal de pago inactivo")
        if body.payment_source == ExpensePaymentSource.CASH and (not channel or not channel.affects_cash):
            raise HTTPException(422, "Selecciona un canal que afecte la caja física")
        if body.payment_source == ExpensePaymentSource.ELECTRONIC and (
                not channel or channel.affects_cash or channel.payment_group != PaymentGroup.ELECTRONIC):
            raise HTTPException(422, "Selecciona un canal electrónico")
        session = None
        if body.payment_source == ExpensePaymentSource.CASH:
            session = current_session(db, lock=True)
            if not session:
                raise HTTPException(409, "Debes abrir la caja antes de registrar un gasto en efectivo")
            from app.services.cash import expected_cash
            if body.amount > expected_cash(db, session):
                raise HTTPException(409, "El gasto supera el efectivo esperado en caja")
        user = get_operator(db)
        expense = Expense(amount=body.amount, category=body.category,
                          description=body.description, payment_source=body.payment_source,
                          payment_channel_id=channel.id if channel else None, notes=body.notes,
                          created_by=user.id, idempotency_key=body.idempotency_key)
        db.add(expense); db.flush()
        if session:
            db.add(CashMovement(
                cash_session_id=session.id, movement_type=CashMovementType.EXPENSE,
                amount=-body.amount, reference_id=expense.id,
                notes=f"{body.category}: {body.description}", user_id=user.id))
        db.commit(); db.refresh(expense)
        return expense
    except IntegrityError:
        db.rollback()
        previous = db.scalar(select(Expense).where(Expense.idempotency_key == body.idempotency_key))
        if previous:
            return previous
        raise
    except Exception:
        db.rollback(); raise


def create_internal_consumption(db: Session, body: InternalConsumptionIn) -> InternalConsumption:
    require_open_session(db, lock=True)
    previous = db.scalar(select(InternalConsumption).where(
        InternalConsumption.idempotency_key == body.idempotency_key))
    if previous:
        return previous
    try:
        user = get_operator(db)
        product = None
        cost_total = ZERO
        sale_total = None
        if body.product_id:
            product = db.scalar(select(Product).where(Product.id == body.product_id).with_for_update(of=Product))
            if not product:
                raise HTTPException(404, "Producto no encontrado")
            if current_stock(db, product.id) < body.quantity:
                raise HTTPException(409, "Stock insuficiente para el consumo interno")
            cost_total = product.unit_cost * body.quantity
            sale_total = product.sale_price * body.quantity
        consumption = InternalConsumption(
            product_id=product.id if product else None, description=body.description,
            quantity=body.quantity if product else None,
            unit_cost=product.unit_cost if product else None,
            reference_sale_price=product.sale_price if product else None,
            cost_total=cost_total, sale_value_total=sale_total,
            estimated_value=body.estimated_value if not product else None,
            notes=body.notes, created_by=user.id, idempotency_key=body.idempotency_key)
        db.add(consumption); db.flush()
        if product:
            db.add(InventoryMovement(
                product_id=product.id, movement_type=InventoryMovementType.INTERNAL_CONSUMPTION,
                quantity=-body.quantity, unit_cost=product.unit_cost,
                reference_id=consumption.id, reason=body.notes or body.description,
                user_id=user.id))
        db.commit(); db.refresh(consumption)
        return consumption
    except IntegrityError:
        db.rollback()
        previous = db.scalar(select(InternalConsumption).where(
            InternalConsumption.idempotency_key == body.idempotency_key))
        if previous:
            return previous
        raise
    except Exception:
        db.rollback(); raise


def _allocate(amount: Decimal, category_amounts: dict[str, Decimal], total: Decimal) -> dict[str, Decimal]:
    if not category_amounts or total <= 0:
        return {}
    raw = {code: amount * value / total for code, value in category_amounts.items()}
    result = {code: value.quantize(CENT, rounding=ROUND_DOWN) for code, value in raw.items()}
    cents = int(((amount - sum(result.values(), ZERO)) / CENT).to_integral_value())
    order = sorted(raw, key=lambda code: (raw[code] - result[code], code), reverse=True)
    for code in order[:cents]:
        result[code] += CENT
    return result


def colombia_day_range(now: datetime | None = None) -> tuple[datetime, datetime]:
    zone = ZoneInfo("America/Bogota")
    local = (now or datetime.now(timezone.utc)).astimezone(zone)
    start = datetime.combine(local.date(), time.min, zone).astimezone(timezone.utc)
    end = datetime.combine(local.date(), time.max, zone).astimezone(timezone.utc)
    return start, end


def _utc(value: datetime) -> datetime:
    # SQLite can return naive datetimes for timezone(True) columns; every
    # timestamp in this codebase is UTC, so treat a naive value as UTC.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def resolve_operational_window(db: Session, now: datetime | None = None) -> tuple[datetime, datetime, str]:
    """The daily summary follows the cash session (the business's operating journey),
    not the calendar day: same journey, same numbers, in both Resumen and Caja."""
    now = now or datetime.now(timezone.utc)
    open_session = db.scalar(select(CashSession).where(CashSession.closed_at.is_(None)))
    if open_session:
        return _utc(open_session.opened_at), now, "OPEN"
    last_closed = db.scalar(select(CashSession).where(CashSession.closed_at.is_not(None))
                            .order_by(CashSession.closed_at.desc()).limit(1))
    if last_closed:
        return _utc(last_closed.opened_at), _utc(last_closed.closed_at), "CLOSED"
    start, end = colombia_day_range(now)
    return start, end, "NONE"


def daily_summary(db: Session, date_from: datetime, date_to: datetime,
                  session_status: str = "CUSTOM") -> DailyOperationsSummary:
    item_rows = db.execute(select(
        SaleItem.sale_id, Sale.total, SaleItem.category_code, func.sum(SaleItem.line_total))
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(Sale.status == SaleStatus.COMPLETED, Sale.completed_at >= date_from,
               Sale.completed_at <= date_to)
        .group_by(SaleItem.sale_id, Sale.total, SaleItem.category_code)).all()
    sale_categories: dict = defaultdict(dict); sale_totals = {}
    for sale_id, total, code, line_total in item_rows:
        sale_categories[sale_id][code or "SIN_CLASIFICAR"] = Decimal(line_total)
        sale_totals[sale_id] = Decimal(total)
    payment_rows = db.execute(select(
        Payment.sale_id, Payment.amount, PaymentChannel.id, PaymentChannel.name,
        PaymentChannel.payment_group).join(
            PaymentChannel, PaymentChannel.id == effective_channel_id_column())
        .join(Sale, Sale.id == Payment.sale_id)
        .where(Sale.status == SaleStatus.COMPLETED, Sale.completed_at >= date_from,
               Sale.completed_at <= date_to)).all()
    aggregate = defaultdict(lambda: {"CASH": ZERO, "ELECTRONIC": ZERO, "OTHER": ZERO,
                                     "total": ZERO, "channels": defaultdict(Decimal)})
    channel_meta = {}
    sales_total = ZERO
    for sale_id, amount, channel_id, channel_name, payment_group in payment_rows:
        amount = Decimal(amount); sales_total += amount
        for code, allocated in _allocate(amount, sale_categories.get(sale_id, {}), sale_totals.get(sale_id, ZERO)).items():
            aggregate[code][payment_group.value] += allocated
            aggregate[code]["total"] += allocated
            aggregate[code]["channels"][channel_id] += allocated
            channel_meta[channel_id] = (channel_name, payment_group)
    categories = {row.code: row for row in db.scalars(
        select(OperationalCategory).order_by(OperationalCategory.display_order)).all()}
    category_rows = [CategorySummary(
        category_code=code, category_name=categories[code].name if code in categories else code,
        cash=data["CASH"], electronic=data["ELECTRONIC"], other=data["OTHER"],
        total=data["total"], channels=[ChannelBreakdown(
            channel_id=channel_id, channel_name=channel_meta[channel_id][0],
            payment_group=channel_meta[channel_id][1], amount=amount)
            for channel_id, amount in data["channels"].items()])
        for code, data in sorted(aggregate.items(), key=lambda pair:
            categories[pair[0]].display_order if pair[0] in categories else 999)]
    expense_rows = dict(db.execute(select(Expense.payment_source, func.sum(Expense.amount)).where(
        Expense.created_at >= date_from, Expense.created_at <= date_to)
        .group_by(Expense.payment_source)).all())
    internal_cost, internal_estimated = db.execute(select(
        func.coalesce(func.sum(InternalConsumption.cost_total), 0),
        func.coalesce(func.sum(InternalConsumption.estimated_value), 0)).where(
            InternalConsumption.created_at >= date_from,
            InternalConsumption.created_at <= date_to)).one()
    total_withdrawals = abs(Decimal(db.scalar(select(func.coalesce(func.sum(CashMovement.amount), 0)).where(
        CashMovement.movement_type == CashMovementType.WITHDRAWAL,
        CashMovement.created_at >= date_from, CashMovement.created_at <= date_to))))
    home_withdrawals = abs(Decimal(db.scalar(select(func.coalesce(func.sum(CashMovement.amount), 0)).where(
        CashMovement.movement_type == CashMovementType.WITHDRAWAL,
        CashMovement.withdrawal_reason == WithdrawalReason.HOME,
        CashMovement.created_at >= date_from, CashMovement.created_at <= date_to))))
    cash_expenses = Decimal(expense_rows.get(ExpensePaymentSource.CASH, ZERO))
    electronic_expenses = Decimal(expense_rows.get(ExpensePaymentSource.ELECTRONIC, ZERO))
    other_expenses = Decimal(expense_rows.get(ExpensePaymentSource.OTHER, ZERO))
    external_scale_total = Decimal(db.scalar(
        select(func.coalesce(func.sum(SaleExternalItem.amount), 0)).join(Sale, Sale.id == SaleExternalItem.sale_id)
        .where(Sale.status == SaleStatus.COMPLETED, Sale.completed_at >= date_from, Sale.completed_at <= date_to)))
    from app.services.post_sale import exchanges_summary, returns_summary
    returned = returns_summary(db, date_from, date_to)
    exchanged = exchanges_summary(db, date_from, date_to)
    returns = ReturnsSummary(
        gross_sales=sales_total, returns_total=returned["returns_total"],
        refunded_total=returned["refunded_total"],
        net_sales=sales_total - returned["refunded_total"], refunds_cash=returned["refunds_cash"],
        refunds_electronic=returned["refunds_electronic"],
        refunds_electronic_pending=returned["refunds_electronic_pending"],
        return_count=returned["return_count"], void_count=returned["void_count"],
        by_category=[ReturnCategoryAmount(category_code=code, amount=amount)
                     for code, amount in sorted(returned["by_category"].items())])
    return DailyOperationsSummary(
        returns=returns,
        exchanges=ExchangesSummary(**{key: exchanged[key] for key in ExchangesSummary.model_fields}),
        date_from=date_from, date_to=date_to, session_status=session_status,
        sales_total=sales_total - returned["refunded_total"], external_scale_total=external_scale_total, categories=category_rows,
        outgoing=OutgoingSummary(
            expenses_total=cash_expenses + electronic_expenses + other_expenses,
            expenses_cash=cash_expenses, expenses_electronic=electronic_expenses,
            expenses_other=other_expenses, internal_consumption_cost=internal_cost,
            internal_consumption_estimated=internal_estimated,
            home_withdrawals=home_withdrawals,
            other_withdrawals=total_withdrawals - home_withdrawals))
