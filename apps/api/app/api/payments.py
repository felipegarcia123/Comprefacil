from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.services.effective_payments import effective_channel_id_column
from app.services.post_sale import completed_refunds_by_channel
from app.domain.enums import PaymentGroup, PaymentMethod
from app.models import BusinessAccount, Payment, PaymentChannel
from app.schemas.payments import (PaymentChannelActiveIn, PaymentChannelCreate,
                                  PaymentChannelOut, PaymentChannelUpdate,
                                  PaymentSummaryOut, PaymentSummaryRow)

router = APIRouter(tags=["payments"])


def _unique_name(db: Session, name: str, exclude: UUID | None = None):
    stmt = select(PaymentChannel).where(func.lower(PaymentChannel.name) == name.lower())
    if exclude:
        stmt = stmt.where(PaymentChannel.id != exclude)
    if db.scalar(stmt):
        raise HTTPException(409, "Ya existe un canal con ese nombre")


def _check_account(db: Session, channel: PaymentChannel):
    if channel.default_account_id is not None:
        account = db.get(BusinessAccount, channel.default_account_id)
        if account is None or not account.active:
            raise HTTPException(422, "La cuenta destino por defecto no existe o está inactiva")


def _apply(channel: PaymentChannel, body: PaymentChannelCreate | PaymentChannelUpdate):
    for key, value in body.model_dump().items():
        if key in ("alegra_payment_method", "default_account_id", "requires_destination_account") \
                and key not in body.model_fields_set:
            continue  # an older client that doesn't send it must not wipe the mapping
        setattr(channel, key, value)
    if channel.payment_type == PaymentMethod.CARD:
        channel.alegra_payment_method = None  # card channels: Débito/Crédito at checkout decides
    channel.affects_cash = body.payment_type == PaymentMethod.CASH
    if channel.affects_cash:                   # cash never lands in a business account
        channel.default_account_id, channel.requires_destination_account = None, False
    if body.payment_group is None:
        channel.payment_group = (PaymentGroup.CASH if channel.affects_cash
                                 else PaymentGroup.ELECTRONIC)


@router.get("/payment-channels", response_model=list[PaymentChannelOut])
def channels(active: bool | None = None, db: Session = Depends(get_db)):
    stmt = select(PaymentChannel)
    if active is not None:
        stmt = stmt.where(PaymentChannel.active == active)
    return db.scalars(stmt.order_by(PaymentChannel.display_order, PaymentChannel.name)).all()


@router.post("/payment-channels", response_model=PaymentChannelOut, status_code=201)
def create_channel(body: PaymentChannelCreate, db: Session = Depends(get_db)):
    _unique_name(db, body.name)
    channel = PaymentChannel()
    _apply(channel, body)
    _check_account(db, channel)
    db.add(channel); db.commit(); db.refresh(channel)
    return channel


@router.put("/payment-channels/{channel_id}", response_model=PaymentChannelOut)
def update_channel(channel_id: UUID, body: PaymentChannelUpdate, db: Session = Depends(get_db)):
    channel = db.get(PaymentChannel, channel_id)
    if not channel:
        raise HTTPException(404, "Canal de pago no encontrado")
    _unique_name(db, body.name, channel_id)
    _apply(channel, body)
    _check_account(db, channel)
    db.commit(); db.refresh(channel)
    return channel


@router.patch("/payment-channels/{channel_id}/active", response_model=PaymentChannelOut)
def activate_channel(channel_id: UUID, body: PaymentChannelActiveIn,
                     db: Session = Depends(get_db)):
    channel = db.get(PaymentChannel, channel_id)
    if not channel:
        raise HTTPException(404, "Canal de pago no encontrado")
    channel.active = body.active
    db.commit(); db.refresh(channel)
    return channel


@router.get("/payments/summary", response_model=PaymentSummaryOut)
def payment_summary(date_from: datetime | None = None, date_to: datetime | None = None,
                    db: Session = Depends(get_db)):
    stmt = (select(PaymentChannel.id, PaymentChannel.name, PaymentChannel.payment_type,
                   func.sum(Payment.amount), func.count(Payment.id))
            .join(Payment, effective_channel_id_column() == PaymentChannel.id))
    if date_from:
        stmt = stmt.where(Payment.created_at >= date_from)
    if date_to:
        stmt = stmt.where(Payment.created_at <= date_to)
    rows = db.execute(stmt.group_by(PaymentChannel.id).order_by(PaymentChannel.display_order)).all()
    refunded = completed_refunds_by_channel(db, date_from, date_to)
    total = sum((Decimal(row[3]) - refunded.get(row[0], Decimal("0")) for row in rows), Decimal("0"))
    return PaymentSummaryOut(total_sales=total, channels=[PaymentSummaryRow(
        channel_id=row[0], channel_name=row[1], payment_type=row[2],
        amount=Decimal(row[3]) - refunded.get(row[0], Decimal("0")),
        payment_count=row[4], percentage=((Decimal(row[3]) - refunded.get(row[0], Decimal("0"))) / total * 100).quantize(Decimal("0.01"))
        if total else Decimal("0")) for row in rows])
