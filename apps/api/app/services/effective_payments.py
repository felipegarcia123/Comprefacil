"""EFFECTIVE PAYMENT CHANNEL — the single place that knows how a payment's real channel is resolved.

A payment (`payments` row) is immutable: it keeps the channel it was registered with. Channel mistakes
are fixed with append-only `payment_corrections` (sequence 1..n per payment). The EFFECTIVE channel
is the `to_channel_id` of the latest correction, or the original channel when there is none.

Every report / summary / reconciliation / fiscal rule that needs "how was this really paid" must use
these helpers, never `Payment.payment_channel_id` or `payment.channel` directly (a test enforces it).
"""
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Payment, PaymentChannel, PaymentCorrection


def effective_channel_id_column():
    """SQL expression (correlated to Payment) with the effective channel id — use it in joins:
    `.join(PaymentChannel, PaymentChannel.id == effective_channel_id_column())`."""
    latest = (select(PaymentCorrection.to_channel_id)
              .where(PaymentCorrection.payment_id == Payment.id)
              .order_by(PaymentCorrection.sequence.desc()).limit(1)
              .correlate(Payment).scalar_subquery())
    return func.coalesce(latest, Payment.payment_channel_id)


def effective_channel_ids(db: Session, payments) -> dict[UUID, UUID]:
    """{payment.id: effective channel id} for already loaded payments (one query for the corrections)."""
    payments = list(payments)
    result = {p.id: p.payment_channel_id for p in payments}
    if not payments:
        return result
    rows = db.execute(select(PaymentCorrection.payment_id, PaymentCorrection.to_channel_id)
                      .where(PaymentCorrection.payment_id.in_(list(result)))
                      .order_by(PaymentCorrection.payment_id, PaymentCorrection.sequence)).all()
    for payment_id, to_channel in rows:              # ordered by sequence: the last one wins
        result[payment_id] = to_channel
    return result


def effective_channels(db: Session, payments) -> dict[UUID, PaymentChannel]:
    """{payment.id: effective PaymentChannel} for already loaded payments."""
    ids = effective_channel_ids(db, payments)
    channels = {c.id: c for c in db.scalars(select(PaymentChannel).where(PaymentChannel.id.in_(set(ids.values()))))}
    return {payment_id: channels[channel_id] for payment_id, channel_id in ids.items()}


def effective_account_ids(db: Session, payments) -> dict[UUID, UUID | None]:
    """{payment.id: effective destination account id} (0028): the latest correction's to_account_id, or the
    payment's own destination_account_id when it was never corrected. None = no tracked account."""
    payments = list(payments)
    result: dict[UUID, UUID | None] = {p.id: p.destination_account_id for p in payments}
    if not payments:
        return result
    rows = db.execute(select(PaymentCorrection.payment_id, PaymentCorrection.to_account_id)
                      .where(PaymentCorrection.payment_id.in_(list(result)))
                      .order_by(PaymentCorrection.payment_id, PaymentCorrection.sequence)).all()
    for payment_id, to_account in rows:              # ordered by sequence: the last one wins
        result[payment_id] = to_account
    return result


def corrections_by_payment(db: Session, payment_ids) -> dict[UUID, list[PaymentCorrection]]:
    """The full, ordered correction chain of each payment (audit / detail)."""
    chains: dict[UUID, list[PaymentCorrection]] = {pid: [] for pid in payment_ids}
    if not chains:
        return chains
    for correction in db.scalars(select(PaymentCorrection).where(PaymentCorrection.payment_id.in_(list(chains)))
                                 .order_by(PaymentCorrection.payment_id, PaymentCorrection.sequence)):
        chains[correction.payment_id].append(correction)
    return chains
