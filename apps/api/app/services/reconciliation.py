"""Cuadre de jornada — RECONCILIATION layer, separate from the POS ledger (2026-10-05).

Three concepts are kept apart on purpose:
  1. the customer's bill (sale + granel settled by the scale)  -> pos.checkout
  2. the POS operational ledger (Sale/Payment/CashMovement)    -> unchanged; expected cash unchanged
  3. the day's reconciliation (this module)                    -> uses 2 + data from outside the POS

Only BLOCK 1 of the real closing procedure is known so far:

    EFECTIVO_BASCULA  = scale_total - scale_digital      (from the scale's own closing, per cash session)
  + cash of CIGARROS, MASCOTAS_CON_CODIGO, LICORES, MASCOTAS_SIN_CODIGO and BULTOS that is NOT granel
  = SUBTOTAL PARCIAL DEL CUADRE (block 1)

It is NOT expected cash, a difference, a surplus or a shortage: the rest of the procedure is not
defined yet, so nothing here changes CashSession.expected_cash or the closing of the cash session.
SUPER is deliberately not part of block 1. Nothing here writes payments, cash movements or sales.
"""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import PaymentMethod, SaleStatus
from app.models import CashSession, CashSessionScaleClosing, PaymentChannel, Sale, SaleExternalItem
from app.services.effective_payments import effective_channel_id_column, effective_channel_ids
from app.services.operations import _allocate
from app.services.reporting_categories import reporting_category

ZERO = Decimal("0")
# Order = how the business reads them. SUPER is intentionally absent (not part of block 1).
BLOCK1_CATEGORIES = ("CIGARROS", "MASCOTAS_CON_CODIGO", "LICORES", "MASCOTAS_SIN_CODIGO", "BULTOS")


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def resolve_session(db: Session, session_id: UUID | None = None) -> CashSession:
    """The given session, else the open one, else the last closed one (same rule as Resumen)."""
    if session_id is not None:
        session = db.get(CashSession, session_id)
    else:
        session = db.scalar(select(CashSession).where(CashSession.closed_at.is_(None))) or db.scalar(
            select(CashSession).where(CashSession.closed_at.is_not(None)).order_by(CashSession.closed_at.desc()).limit(1))
    if session is None:
        raise HTTPException(404, "No hay jornada para cuadrar")
    return session


def set_scale_closing(db: Session, session_id: UUID, scale_total: Decimal, scale_digital: Decimal) -> CashSessionScaleClosing:
    """Records what the scale reports at its closing for that cash session (upsert, survives refresh).
    Reconciliation data only. Its cash is derived; digital can never exceed the total."""
    if scale_total < 0 or scale_digital < 0:
        raise HTTPException(422, "Los valores de la báscula no pueden ser negativos")
    if scale_digital > scale_total:
        raise HTTPException(422, "Los pagos digitales de la báscula no pueden superar el total de la báscula")
    try:
        session = db.get(CashSession, session_id)
        if session is None:
            raise HTTPException(404, "Jornada no encontrada")
        row = db.scalar(select(CashSessionScaleClosing).where(CashSessionScaleClosing.cash_session_id == session_id)
                        .with_for_update())
        if row is None:
            row = CashSessionScaleClosing(cash_session_id=session_id)
            db.add(row)
        row.scale_total, row.scale_digital = scale_total, scale_digital
        db.commit(); db.refresh(row)
        return row
    except Exception:
        db.rollback()
        raise


def _is_granel(sale: Sale, line) -> bool:
    from app.services.electronic_invoicing import manual_kind
    return line.product_id is None and manual_kind(sale, line) == "GRANEL"


def pos_cash_by_category(db: Session, start: datetime, end: datetime) -> tuple[dict[str, Decimal], Decimal, Decimal]:
    """POS CASH per operational category in [start, end], excluding granel.

    Per completed sale: cash = payments on channels that move the drawer (affects_cash). Granel sold
    in the POS can only be paid electronically (pos.check_granel_cash), so the electronic money covers
    the granel lines first; the cash is then spread over the remaining (non-granel) categories with the
    same proportional allocation as the daily summary. Granel settled by the scale is not a SaleItem and
    never enters. Returns (cash by category, POS granel total, cash that had to be put on granel — only
    possible for legacy data, reported, never added to a category)."""
    channels = {c.id: c for c in db.scalars(select(PaymentChannel))}
    sales = db.scalars(select(Sale).where(Sale.status == SaleStatus.COMPLETED, Sale.completed_at >= start,
                                          Sale.completed_at <= end)).all()
    by_category: dict[str, Decimal] = defaultdict(lambda: ZERO)
    granel_pos = granel_cash = ZERO
    for sale in sales:
        categories: dict[str, Decimal] = defaultdict(lambda: ZERO)
        granel = ZERO
        for line in sale.items:
            if _is_granel(sale, line):
                granel += line.line_total
            else:
                code = reporting_category(line.category_code, manual_kind=line.manual_kind,
                                          sale_source=sale.source)
                categories[code] += line.line_total
        cash = electronic = ZERO
        effective = effective_channel_ids(db, sale.payments)
        for payment in sale.payments:
            channel = channels.get(effective[payment.id])
            in_drawer = channel.affects_cash if channel else payment.method == PaymentMethod.CASH
            if in_drawer:
                cash += payment.amount
            else:
                electronic += payment.amount
        on_granel = max(granel - electronic, ZERO)          # legacy safety only
        granel_pos += granel
        granel_cash += min(on_granel, cash)
        rest = cash - min(on_granel, cash)
        for code, amount in _allocate(rest, dict(categories), sum(categories.values(), ZERO)).items():
            by_category[code] += amount
    return dict(by_category), granel_pos, granel_cash


def block1(db: Session, session: CashSession, now: datetime | None = None) -> dict:
    start = _utc(session.opened_at)
    end = _utc(session.closed_at) if session.closed_at else (now or datetime.now(timezone.utc))
    by_category, granel_pos, granel_cash = pos_cash_by_category(db, start, end)
    pos_cash = {code: by_category.get(code, ZERO) for code in BLOCK1_CATEGORIES}
    other_cash = sum(pos_cash.values(), ZERO)
    closing = db.scalar(select(CashSessionScaleClosing).where(CashSessionScaleClosing.cash_session_id == session.id))
    observed_external = sum((item.amount for item in db.scalars(
        select(SaleExternalItem).join(Sale, Sale.id == SaleExternalItem.sale_id)
        .where(Sale.status == SaleStatus.COMPLETED, Sale.completed_at >= start, Sale.completed_at <= end))), ZERO)
    # Post-sale (0027): cash refunds of block-1 categories made in this jornada. Shown APART only:
    # they are NOT subtracted from pos_cash_by_category nor from block1_subtotal (formula unchanged).
    from app.services.post_sale import returns_summary
    returned_cash = returns_summary(db, start, end)["cash_by_category"]
    returns_aside = {code: returned_cash.get(code, ZERO) for code in BLOCK1_CATEGORIES}
    # Product exchanges: a same-value exchange moves no money and is NOT a cash return. Only a difference
    # collected in cash is real drawer money: shown apart too, never added to / subtracted from block 1.
    from app.services.post_sale import exchanges_summary
    exchanged = exchanges_summary(db, start, end)
    exchange_aside = {code: exchanged["cash_by_category"].get(code, ZERO) for code in BLOCK1_CATEGORIES}
    exchange_refund_aside = {code: exchanged["refund_cash_by_category"].get(code, ZERO) for code in BLOCK1_CATEGORIES}
    scale = None
    if closing is not None:
        scale = {"total": closing.scale_total, "digital": closing.scale_digital, "cash": closing.scale_cash,
                 "updated_at": closing.updated_at}
    return {
        "cash_session_id": session.id, "session_status": "OPEN" if session.closed_at is None else "CLOSED",
        "date_from": start, "date_to": end,
        "scale": scale,
        "pos_cash_by_category": [{"category_code": code, "amount": pos_cash[code]} for code in BLOCK1_CATEGORIES],
        "pos_cash_subtotal": other_cash,
        # Only with the scale's closing registered: without it there is no block-1 figure (never guessed).
        "block1_subtotal": (scale["cash"] + other_cash) if scale else None,
        "returns_cash_by_category": [{"category_code": code, "amount": returns_aside[code]}
                                     for code in BLOCK1_CATEGORIES],
        "returns_cash_total": sum(returns_aside.values(), ZERO),
        "exchange_difference_cash_by_category": [{"category_code": code, "amount": exchange_aside[code]}
                                                 for code in BLOCK1_CATEGORIES],
        "exchange_difference_cash_total": sum(exchange_aside.values(), ZERO),
        "exchange_refund_cash_by_category": [{"category_code": code, "amount": exchange_refund_aside[code]}
                                             for code in BLOCK1_CATEGORIES],
        "exchange_refund_cash_total": sum(exchange_refund_aside.values(), ZERO),
        "observed": {"granel_external_scale": observed_external, "granel_pos": granel_pos,
                     "granel_pos_cash_legacy": granel_cash},
    }
