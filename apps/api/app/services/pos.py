from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import CashMovementType, GranelSettlement, InventoryMovementType, ManualLineKind, SaleStatus
from app.models import (CashMovement, CashSession, Customer, InventoryMovement, OperationalCategory, Payment,
                        SaleExternalItem,
                        PaymentChannel, Product, Sale, SaleItem, User)
from app.schemas.pos import CheckoutIn, ManualItemIn, PaymentAllocationIn
from app.services.barcodes import find_product_by_barcode
from app.services.operational_session import require_open_session


def get_operator(db: Session) -> User:
    user = db.scalar(select(User).limit(1))
    if not user:
        raise HTTPException(500, "No existe un usuario operador")
    return user


def recalculate(sale: Sale) -> None:
    sale.total = sum((item.line_total for item in sale.items), Decimal("0"))


def create_sale(db: Session) -> Sale:
    require_open_session(db)
    user = get_operator(db)
    sale_id = uuid4()
    sale = Sale(id=sale_id, number=f"V-{sale_id.hex[:8].upper()}", created_by=user.id)
    db.add(sale); db.commit(); db.refresh(sale)
    return sale


def cancel_sale(db: Session, sale_id: UUID) -> Sale:
    """Discard an OPEN sale without charging it.

    Inventory is only discounted at checkout, so there is nothing to give back: no InventoryMovement, Payment or
    CashMovement is created or touched. The sale and its lines stay in the table (status CANCELLED) for traceability.
    Repeating the call on an already cancelled sale is a no-op that returns it; a COMPLETED sale can never be cancelled.
    It does not require an open jornada: cleaning up a tab must be possible at any time.
    """
    try:
        sale = db.scalar(select(Sale).where(Sale.id == sale_id).with_for_update())
        if not sale:
            raise HTTPException(404, "Venta no encontrada")
        if sale.status == SaleStatus.CANCELLED:
            return sale
        if sale.status != SaleStatus.OPEN:
            raise HTTPException(409, "No se puede cancelar una venta que ya fue cobrada")
        sale.status = SaleStatus.CANCELLED
        sale.cancelled_at = datetime.now(timezone.utc)
        sale.cancelled_by = get_operator(db).id
        db.commit(); db.refresh(sale)
        return sale
    except Exception:
        db.rollback()
        raise


def invoiceable_customer(db: Session, customer_id: UUID) -> Customer:
    """A customer a sale or an invoice may name: active, Alegra-linked, never the system's
    Consumidor final row (that one is chosen as FINAL_CONSUMER, not by id)."""
    customer = db.get(Customer, customer_id)
    if not customer or customer.is_final_consumer:
        raise HTTPException(404, "Cliente no encontrado")
    if not customer.active:
        raise HTTPException(409, "El cliente está inactivo")
    if not customer.alegra_contact_id:
        raise HTTPException(409, "El cliente no está vinculado a Alegra")
    return customer


def invoice_intent(db: Session, intent) -> tuple[bool, UUID | None]:
    """The "Factura electrónica" checkbox, validated as INPUT (a bad recipient is a 4xx and,
    called before any write, leaves no sale) and fixed on the sale inside its own transaction.
    No intent = no invoice, whatever customer the sale has. Never contacts Alegra: whether the
    Consumidor final contact is right is a fiscal check made after the commit.
    Returns (requires_electronic_invoice, invoice_customer_id); None recipient = Consumidor final."""
    if intent is None:
        return False, None
    from app.services.electronic_invoicing import config_status
    if not config_status()["configured"]:
        raise HTTPException(409, "La facturación electrónica no está configurada; cobra sin factura electrónica")
    if intent.recipient == "CUSTOMER":
        invoiceable_customer(db, intent.customer_id)
        return True, intent.customer_id
    return True, None


def set_sale_customer(db: Session, sale_id: UUID, customer_id: UUID | None) -> Sale:
    """Who the customer of this OPEN sale is. None = none. It does NOT decide whether the sale
    is invoiced (that is the checkout's electronic_invoice). Only Alegra-linked, active,
    non-system customers qualify. Pure metadata: no payment, inventory or cash effect."""
    try:
        sale = db.scalar(select(Sale).where(Sale.id == sale_id).with_for_update())
        if not sale or sale.status != SaleStatus.OPEN:
            raise HTTPException(404, "Venta abierta no encontrada")
        if customer_id is not None:
            invoiceable_customer(db, customer_id)
        sale.customer_id = customer_id
        db.commit(); db.refresh(sale)
        return sale
    except Exception:
        db.rollback()
        raise


# "Producto manual" -> operational category of the line (reports: Súper / Mascotas sin código).
MANUAL_CATEGORY = {ManualLineKind.SUPER: "SUPER", ManualLineKind.GRANEL: "MASCOTAS_SIN_CODIGO",
                   ManualLineKind.BULTO: "MASCOTAS_SIN_CODIGO", ManualLineKind.PET_OTHER: "MASCOTAS_SIN_CODIGO"}


def add_manual_item(db: Session, sale_id: UUID, body: ManualItemIn) -> Sale:
    """Adds a line without catalog product to the OPEN sale and returns it: NOT a checkout.
    No Payment, no ElectronicInvoice, no InventoryMovement (no product, no stock). The line keeps
    its own snapshot (description, kind, category, quantity, price, total); every call adds a
    new line (two "dulce surtido $500" are two lines). Removed/edited like any line."""
    require_open_session(db)
    sale = db.scalar(select(Sale).where(Sale.id == sale_id).with_for_update())
    if not sale or sale.status != SaleStatus.OPEN:
        raise HTTPException(404, "Venta abierta no encontrada")
    code = MANUAL_CATEGORY[body.kind]
    category = db.scalar(select(OperationalCategory).where(OperationalCategory.code == code))
    if category is None:
        raise HTTPException(500, f"No existe la categoría operativa {code}")
    sale.items.append(SaleItem(
        product_id=None, manual_kind=body.kind.value, operational_category_id=category.id,
        category_code=category.code, product_name=body.description, quantity=body.quantity,
        unit_price=body.amount, unit_cost=Decimal("0"), line_total=body.quantity * body.amount))
    if body.kind == ManualLineKind.GRANEL and sale.granel_settlement is None:
        sale.granel_settlement = GranelSettlement.EXTERNAL_SCALE.value     # natural default, only once granel exists
    recalculate(sale); db.commit(); db.refresh(sale)
    return sale


GRANEL_CASH_MESSAGE = ("La comida a granel marcada como 'Pago electrónico' no se cobra en efectivo en el POS. "
                       "Si el cliente la paga en efectivo, márcala como 'Báscula / efectivo'.")
EXTERNAL_ONLY_MESSAGE = ("Esta venta solo tiene comida a granel registrada en la báscula: no hay nada que cobrar "
                         "en el POS. Cancela la venta.")


def granel_lines(sale: Sale) -> list[SaleItem]:
    return [item for item in sale.items if item.manual_kind == ManualLineKind.GRANEL.value]


def effective_granel_settlement(sale: Sale) -> str | None:
    """None while the sale has no GRANEL line; EXTERNAL_SCALE ("Báscula / efectivo") unless chosen otherwise."""
    if not granel_lines(sale):
        return None
    return sale.granel_settlement or GranelSettlement.EXTERNAL_SCALE.value


def external_scale_lines(sale: Sale) -> list[SaleItem]:
    """GRANEL lines the cashier marked "Báscula / efectivo": part of the customer's bill, never of the POS."""
    if effective_granel_settlement(sale) != GranelSettlement.EXTERNAL_SCALE.value:
        return []
    return granel_lines(sale)


def set_granel_settlement(db: Session, sale_id: UUID, settlement: GranelSettlement) -> Sale:
    """The checkout's "¿Cómo se registró la comida a granel?" choice, kept on the OPEN sale (survives
    refresh / tabs). Pure metadata of the open sale: no payment, cash or stock effect."""
    try:
        sale = db.scalar(select(Sale).where(Sale.id == sale_id).with_for_update())
        if not sale or sale.status != SaleStatus.OPEN:
            raise HTTPException(404, "Venta abierta no encontrada")
        sale.granel_settlement = settlement.value
        db.commit(); db.refresh(sale)
        return sale
    except Exception:
        db.rollback()
        raise


def check_granel_cash(sale: Sale, requested, channels, pos_total: Decimal) -> None:
    """GRANEL settled in the POS ("Pago electrónico") is never charged in POS cash: in a mixed cart the
    electronic payments must cover at least those granel lines (cash <= POS total - granel). GRANEL
    settled by the scale is outside the POS and never reaches this check. Input check (409)."""
    if effective_granel_settlement(sale) != GranelSettlement.POS.value:
        return
    granel = sum((item.line_total for item in granel_lines(sale)), Decimal("0"))
    if not granel:
        return
    cash = sum((a.amount for a in requested if channels[a.channel_id].affects_cash), Decimal("0"))
    if cash > pos_total - granel:
        raise HTTPException(409, GRANEL_CASH_MESSAGE)


def add_item(db: Session, sale_id: UUID, barcode: str | None = None, quantity: Decimal = Decimal("1"), *,
             product_id: UUID | None = None) -> Sale:
    """Scanner (`barcode`, with the UPC/EAN rule) or manual pick (`product_id`): only the
    product resolution differs; stock, price, category and snapshot logic are shared."""
    if (barcode is None) == (product_id is None):
        raise HTTPException(422, "Indica barcode o product_id (exactamente uno)")
    require_open_session(db)
    sale = db.scalar(select(Sale).where(Sale.id == sale_id).with_for_update())
    if not sale or sale.status != SaleStatus.OPEN:
        raise HTTPException(404, "Venta abierta no encontrada")
    product = find_product_by_barcode(db, barcode) if barcode is not None else db.get(Product, product_id)
    if not product:
        raise HTTPException(404, "Producto no encontrado")
    if not product.active:
        raise HTTPException(409, "El producto está inactivo")
    item = next((item for item in sale.items if item.product_id == product.id), None)
    if item:
        item.quantity += quantity
        item.line_total = item.quantity * item.unit_price
    else:
        sale.items.append(SaleItem(
            product_id=product.id, product_name=product.name, quantity=quantity,
            operational_category_id=product.operational_category_id,
            category_code=(product.operational_category.code
                           if product.operational_category else "SIN_CLASIFICAR"),
            unit_price=product.sale_price, unit_cost=product.unit_cost,
            line_total=quantity * product.sale_price))
    recalculate(sale); db.commit(); db.refresh(sale)
    return sale


def _requested_payments(db: Session, sale: Sale, body: CheckoutIn, due: Decimal) -> list[PaymentAllocationIn]:
    """Payment allocations for what the POS itself collects (`due` = POS total, never the scale part)."""
    if body.payments is not None:
        return body.payments
    if body.channel_id is not None:
        return [PaymentAllocationIn(channel_id=body.channel_id, amount=due,
                received_amount=body.received_amount, reference=body.reference, card_type=body.card_type,
                destination_account_id=body.destination_account_id)]
    channel = db.scalar(select(PaymentChannel).where(
        PaymentChannel.payment_type == body.method, PaymentChannel.active.is_(True))
        .order_by(PaymentChannel.display_order).limit(1))
    if not channel:
        raise HTTPException(409, "No existe un canal activo para el método indicado")
    return [PaymentAllocationIn(channel_id=channel.id, amount=due,
            received_amount=body.received_amount, reference=body.reference,
            destination_account_id=body.destination_account_id)]


def checkout(db: Session, sale_id: UUID, body: CheckoutIn) -> Sale:
    from app.services.inventory import stock_expression
    try:
        session = require_open_session(db, lock=True)
        sale = db.scalar(select(Sale).where(Sale.id == sale_id).with_for_update())
        if not sale or sale.status != SaleStatus.OPEN:
            raise HTTPException(404, "Venta abierta no encontrada")
        if not sale.items:
            raise HTTPException(400, "No se puede cobrar una venta vacía")
        requires_invoice, invoice_customer_id = invoice_intent(db, body.electronic_invoice)  # input, before any write
        user = get_operator(db)
        # The customer's bill = POS part + granel already recorded by the scale ("Báscula / efectivo").
        # Only the POS part is ever paid/recorded here; the scale part only enters the change math.
        external_lines = external_scale_lines(sale)
        sale.granel_settlement = effective_granel_settlement(sale)   # NULL when no GRANEL line remains
        external = sum((item.line_total for item in external_lines), Decimal("0"))
        pos_total = sale.total - external
        if external and pos_total <= 0:
            raise HTTPException(409, EXTERNAL_ONLY_MESSAGE)
        requested = _requested_payments(db, sale, body, pos_total)
        channel_ids = [payment.channel_id for payment in requested]
        channels = {channel.id: channel for channel in db.scalars(
            select(PaymentChannel).where(PaymentChannel.id.in_(channel_ids)).with_for_update()).all()}
        if len(channels) != len(channel_ids):
            raise HTTPException(404, "Uno o más canales de pago no existen")
        if any(not channel.active for channel in channels.values()):
            raise HTTPException(409, "No se puede usar un canal de pago inactivo")
        allocated = sum((payment.amount for payment in requested), Decimal("0"))
        if allocated != pos_total:
            message = ("El total pagado es inferior al total de la venta" if allocated < pos_total
                       else "El total pagado supera el total de la venta")
            raise HTTPException(409, message)
        check_granel_cash(sale, requested, channels, pos_total)          # before any write

        cash_total = Decimal("0")
        scale_cash = external          # the scale part is paid in cash with the first cash payment (change math only)
        from app.services.payment_methods import requires_card_type
        from app.services.business_accounts import destination_for
        # WHERE each payment landed (0028), validated before any write: never inferred from the channel.
        destinations = {allocation.channel_id: destination_for(db, channels[allocation.channel_id],
                                                               allocation.destination_account_id)
                        for allocation in requested}
        for allocation in requested:
            channel = channels[allocation.channel_id]
            if allocation.card_type and not requires_card_type(channel.payment_type):
                raise HTTPException(422, f"Débito/Crédito solo aplica a canales de tarjeta, no a {channel.name}")
            received = allocation.received_amount if channel.affects_cash else None
            change = None
            if channel.affects_cash:
                if body.payments is None and received is None:
                    raise HTTPException(422, "El dinero recibido es obligatorio para efectivo")
                # Customer cash tendered covers the POS cash AND the scale part; only `amount` is POS cash.
                customer_due = allocation.amount + scale_cash
                received = received if received is not None else customer_due
                if received < customer_due:
                    raise HTTPException(400, "El dinero recibido es menor al pago en efectivo")
                change, scale_cash = received - customer_due, Decimal("0")
                cash_total += allocation.amount
            # amount = POS money (caja/reportes). received_amount = cash the customer handed over for the
            # whole bill (incl. the scale part); change_amount = change for that whole bill.
            db.add(Payment(
                sale_id=sale.id, method=channel.payment_type,
                payment_channel_id=channel.id, amount=allocation.amount,
                received_amount=received, change_amount=change,
                reference=allocation.reference, card_type=allocation.card_type,
                destination_account_id=destinations[allocation.channel_id]))

        product_ids = [item.product_id for item in sale.items if item.product_id is not None]
        db.scalars(select(Product).where(Product.id.in_(product_ids)).with_for_update(of=Product)).all()
        stocks = dict(db.execute(select(InventoryMovement.product_id, stock_expression())
                     .where(InventoryMovement.product_id.in_(product_ids))
                     .group_by(InventoryMovement.product_id)).all())
        for item in sale.items:
            if item.product_id is None:
                continue
            if stocks.get(item.product_id, Decimal("0")) < item.quantity:
                raise HTTPException(409, f"Stock insuficiente para {item.product_name}")
        for item in sale.items:
            if item.product_id is None:
                continue
            db.add(InventoryMovement(
                product_id=item.product_id, movement_type=InventoryMovementType.SALE,
                quantity=-item.quantity, unit_cost=item.unit_cost,
                reference_id=sale.id, user_id=user.id))
        if cash_total:
            db.add(CashMovement(
                cash_session_id=session.id, movement_type=CashMovementType.SALE,
                amount=cash_total, reference_id=sale.id, user_id=user.id))
        # Scale-settled granel leaves the POS lines: kept for audit / the customer's bill, never as a
        # POS sale line, so Sale.total, reports, cash and the invoice only see the POS part.
        for item in external_lines:
            sale.external_items.append(SaleExternalItem(
                kind=ManualLineKind.GRANEL.value, settlement=GranelSettlement.EXTERNAL_SCALE.value,
                description=item.product_name, quantity=item.quantity, unit_price=item.unit_price,
                amount=item.line_total, category_code=item.category_code))
            sale.items.remove(item)
        recalculate(sale)
        if sale.total != pos_total:                       # invariant: the POS records exactly its part
            raise HTTPException(500, "Descuadre interno entre total de la venta y parte POS")
        sale.status = SaleStatus.COMPLETED
        sale.completed_at = datetime.now(timezone.utc)
        sale.requires_electronic_invoice, sale.invoice_customer_id = requires_invoice, invoice_customer_id
        from app.services.electronic_invoicing import add_pending_invoice
        add_pending_invoice(db, sale)      # only if invoiced: local row, same transaction; Alegra after the commit
        db.commit(); db.refresh(sale)
        return sale
    except Exception:
        db.rollback()
        raise
