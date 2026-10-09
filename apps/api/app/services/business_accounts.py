"""CUENTAS DEL NEGOCIO (migration 0028) — reconciliation of the digital accounts (Nequi, Davivienda, Nu).

At the close the owner types the balance they SEE in each app. The POS only compares it with what it knows:

    expected = previous observed balance + POS inflows - POS outflows + external IN - external OUT
    difference = observed - expected

It never changes a Payment, a sale, the cash drawer (CashSession / CashMovement), the expected cash or Block 1.

DESTINATION ACCOUNT != PAYMENT CHANNEL. The channel says HOW the customer paid (Nequi, Bre-B, Datáfono...);
`destination_account_id` says WHERE the money landed. Nothing infers one from the other: the channel only
preselects a configurable default at COBRAR.

What moves an account (each fact counted exactly once, in the window where it really happened):
  + payment of a COMPLETED sale with destination_account_id = A          (sale completed_at)
  + payment correction whose to_account is A / - whose from_account is A  (correction created_at)
  + exchange difference collected electronically into A (IN, COMPLETED)   (created_at)
  - electronic refund COMPLETED from A                                    (confirmed_at; PENDING never counts)
  - exchange difference given back electronically from A (OUT, COMPLETED) (confirmed_at)
  ± business_account_movements of A (explicit external movements)       (their cash session)
A payment counts with its ORIGINAL destination at the sale's moment; a later correction adds its own delta at
its own moment. That way a closed jornada's frozen numbers never change and nothing is counted twice.

WINDOW of a jornada S for account A: from the close of the jornada holding A's previous snapshot (or, for the
first one, the previous close / S's opening) up to S's close. Consecutive windows never overlap and leave no
gap, so an electronic refund confirmed at night (no jornada open) lands in the next jornada's window.

FRONTIER (no double counting with the cash drawer / expenses): supplier payments and expenses paid
electronically are NOT read here (they don't record the account). If one of them left an account, the owner
registers it as an explicit external movement; there is no automatic link and no accounting integration.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.enums import SaleStatus
from app.models import (BusinessAccount, BusinessAccountMovement, BusinessAccountSnapshot,
                        BusinessAccountSnapshotCorrection, CashSession, Payment, PaymentChannel, PaymentCorrection,
                        Sale, SaleExchangeSettlement, SaleRefund)
from app.services.pos import get_operator

ZERO = Decimal("0")
CENT = Decimal("0.01")
# DaviPlata has its own balance, completely separate from Davivienda: a fourth, independent account.
DEFAULT_ACCOUNTS = (("NEQUI", "Nequi", 10), ("DAVIVIENDA", "Davivienda", 20), ("NU", "Nu", 30),
                    ("DAVIPLATA", "DaviPlata", 40))
MOVEMENT_REASONS = {"OTHER_IN": "Otro ingreso", "OTHER_OUT": "Otra salida",
                    "TRANSFER_BETWEEN_ACCOUNTS": "Transferencia entre cuentas", "REVERSAL": "Reversión"}
LATER_SNAPSHOT_MESSAGE = ("{name} ya tiene un saldo registrado en una jornada posterior: este saldo ya no se "
                          "puede registrar ni corregir.")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _money(value) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(422, "Escribe un valor numérico válido")
    if not amount.is_finite():
        raise HTTPException(422, "Escribe un valor numérico válido")
    return amount.quantize(CENT)


def ensure_default_accounts(db: Session) -> None:
    """Idempotent seed of NEQUI / DAVIVIENDA / NU / DAVIPLATA (also done by migration 0028)."""
    existing = set(db.scalars(select(BusinessAccount.code)))
    for code, name, order in DEFAULT_ACCOUNTS:
        if code not in existing:
            db.add(BusinessAccount(code=code, name=name, display_order=order))
    db.flush()


def list_accounts(db: Session, active: bool | None = None) -> list[BusinessAccount]:
    statement = select(BusinessAccount)
    if active is not None:
        statement = statement.where(BusinessAccount.active.is_(active))
    return db.scalars(statement.order_by(BusinessAccount.display_order, BusinessAccount.name)).all()


def require_account(db: Session, account_id: UUID | None, *, label: str = "La cuenta") -> BusinessAccount:
    account = db.get(BusinessAccount, account_id) if account_id else None
    if account is None:
        raise HTTPException(404, f"{label} no existe")
    if not account.active:
        raise HTTPException(409, f"{account.name} está inactiva")
    return account


def destination_for(db: Session, channel: PaymentChannel, account_id: UUID | None) -> UUID | None:
    """Validates WHERE an electronic payment / difference landed. Cash never lands in an account; a channel
    that requires a destination must say it; others may (the cashier knows better than the default)."""
    if channel.affects_cash:
        if account_id is not None:
            raise HTTPException(422, "El efectivo no llega a una cuenta: no indiques cuenta destino")
        return None
    if account_id is None:
        if channel.requires_destination_account:
            raise HTTPException(422, f"Elige a qué cuenta llegó el dinero de {channel.name}")
        return None
    return require_account(db, account_id, label="La cuenta destino").id


# ---- windows and flows --------------------------------------------------------------------------------

def _later_snapshot(db: Session, account_id: UUID, session: CashSession) -> BusinessAccountSnapshot | None:
    if session.closed_at is None:
        return None
    return db.scalar(select(BusinessAccountSnapshot).join(CashSession, CashSession.id == BusinessAccountSnapshot.cash_session_id)
                     .where(BusinessAccountSnapshot.business_account_id == account_id,
                            CashSession.closed_at > session.closed_at).limit(1))


def _previous_snapshot(db: Session, account_id: UUID, session: CashSession) -> BusinessAccountSnapshot | None:
    statement = (select(BusinessAccountSnapshot).join(CashSession, CashSession.id == BusinessAccountSnapshot.cash_session_id)
                 .where(BusinessAccountSnapshot.business_account_id == account_id,
                        BusinessAccountSnapshot.cash_session_id != session.id))
    if session.closed_at is not None:
        statement = statement.where(CashSession.closed_at < session.closed_at)
    return db.scalar(statement.order_by(CashSession.closed_at.desc()).limit(1))


def window_for(db: Session, account_id: UUID, session: CashSession, now: datetime | None = None):
    """(previous snapshot, start, end, cash session ids) of the reconciliation of `account_id` at `session`."""
    end = _utc(session.closed_at) or (now or _now())
    previous = _previous_snapshot(db, account_id, session)
    if previous is not None:
        start = _utc(db.get(CashSession, previous.cash_session_id).closed_at)
    else:
        before = db.scalar(select(CashSession.closed_at).where(CashSession.closed_at.is_not(None),
                                                               CashSession.id != session.id,
                                                               CashSession.closed_at < end)
                           .order_by(CashSession.closed_at.desc()).limit(1))
        start = _utc(before) if before is not None else _utc(session.opened_at)
    session_ids = set(db.scalars(select(CashSession.id).where(CashSession.closed_at.is_not(None),
                                                              CashSession.closed_at > start,
                                                              CashSession.closed_at <= end)))
    session_ids.add(session.id)
    return previous, start, end, session_ids


def _sum(db: Session, statement) -> Decimal:
    return Decimal(db.scalar(statement) or 0)


def account_flows(db: Session, account_id: UUID, start: datetime, end: datetime, session_ids: set[UUID]) -> dict:
    """Every fact that moved money in/out of the account in (start, end] — see the module docstring."""
    payments = _sum(db, select(func.sum(Payment.amount)).join(Sale, Sale.id == Payment.sale_id).where(
        Payment.destination_account_id == account_id, Sale.status == SaleStatus.COMPLETED,
        Sale.completed_at > start, Sale.completed_at <= end))
    correction_in = _sum(db, select(func.sum(PaymentCorrection.amount)).where(
        PaymentCorrection.to_account_id == account_id,
        PaymentCorrection.created_at > start, PaymentCorrection.created_at <= end))
    correction_out = _sum(db, select(func.sum(PaymentCorrection.amount)).where(
        PaymentCorrection.from_account_id == account_id,
        PaymentCorrection.created_at > start, PaymentCorrection.created_at <= end))
    exchange_in = _sum(db, select(func.sum(SaleExchangeSettlement.amount)).where(
        SaleExchangeSettlement.account_id == account_id, SaleExchangeSettlement.direction == "IN",
        SaleExchangeSettlement.status == "COMPLETED",
        SaleExchangeSettlement.created_at > start, SaleExchangeSettlement.created_at <= end))
    exchange_out = _sum(db, select(func.sum(SaleExchangeSettlement.amount)).where(
        SaleExchangeSettlement.account_id == account_id, SaleExchangeSettlement.direction == "OUT",
        SaleExchangeSettlement.status == "COMPLETED",
        SaleExchangeSettlement.confirmed_at > start, SaleExchangeSettlement.confirmed_at <= end))
    refunds_out = _sum(db, select(func.sum(SaleRefund.amount)).where(
        SaleRefund.account_id == account_id, SaleRefund.status == "COMPLETED",
        SaleRefund.confirmed_at > start, SaleRefund.confirmed_at <= end))
    # Informative only: electronic money promised but not sent yet (never subtracted).
    pending = (_sum(db, select(func.sum(SaleRefund.amount)).where(
        SaleRefund.account_id == account_id, SaleRefund.status == "PENDING", SaleRefund.created_at <= end))
        + _sum(db, select(func.sum(SaleExchangeSettlement.amount)).where(
            SaleExchangeSettlement.account_id == account_id, SaleExchangeSettlement.status == "PENDING",
            SaleExchangeSettlement.created_at <= end)))
    external = dict(db.execute(select(BusinessAccountMovement.direction, func.sum(BusinessAccountMovement.amount))
                               .where(BusinessAccountMovement.business_account_id == account_id,
                                      BusinessAccountMovement.cash_session_id.in_(session_ids))
                               .group_by(BusinessAccountMovement.direction)).all())
    return {
        "pos_inflows": payments + correction_in + exchange_in,
        "pos_outflows": refunds_out + exchange_out + correction_out,
        "registered_external_inflows": Decimal(external.get("IN") or 0),
        "registered_external_outflows": Decimal(external.get("OUT") or 0),
        "breakdown": {"payments": payments, "corrections_in": correction_in, "corrections_out": correction_out,
                      "exchange_differences_in": exchange_in, "exchange_differences_out": exchange_out,
                      "refunds_out": refunds_out},
        "pending_outflows": pending,
    }


def _expected(previous_observed: Decimal, flows: dict) -> Decimal:
    return (previous_observed + flows["pos_inflows"] - flows["pos_outflows"]
            + flows["registered_external_inflows"] - flows["registered_external_outflows"])


# ---- reconciliation read model ------------------------------------------------------------------------

def _snapshot_out(snapshot: BusinessAccountSnapshot) -> dict:
    observed = snapshot.effective_observed
    difference = None if snapshot.expected_balance is None else observed - snapshot.expected_balance
    return {
        "snapshot_id": snapshot.id, "is_baseline": snapshot.is_baseline,
        "observed_balance": observed, "original_observed_balance": snapshot.observed_balance,
        "previous_observed_balance": snapshot.previous_observed_balance,
        "pos_inflows": snapshot.pos_inflows, "pos_outflows": snapshot.pos_outflows,
        "registered_external_inflows": snapshot.registered_external_inflows,
        "registered_external_outflows": snapshot.registered_external_outflows,
        "expected_balance": snapshot.expected_balance, "difference": difference,
        "window_start": snapshot.window_start, "window_end": snapshot.window_end, "captured_at": snapshot.captured_at,
        "note": snapshot.note,
        "corrections": [{"id": c.id, "old_observed_balance": c.old_observed_balance,
                         "new_observed_balance": c.new_observed_balance, "reason": c.reason, "created_at": c.created_at}
                        for c in snapshot.corrections],
    }


def account_reconciliation(db: Session, session: CashSession, now: datetime | None = None) -> dict:
    """Per account: the frozen snapshot of that jornada (CAPTURED), or the live preview of what it would be
    (PENDING; BASELINE when there is no previous balance), or LOCKED when a later jornada already has one."""
    snapshots = {s.business_account_id: s for s in db.scalars(
        select(BusinessAccountSnapshot).where(BusinessAccountSnapshot.cash_session_id == session.id))}
    accounts = [a for a in list_accounts(db) if a.active or a.id in snapshots]
    rows = []
    for account in accounts:
        base = {"account_id": account.id, "code": account.code, "name": account.name}
        snapshot = snapshots.get(account.id)
        previous, start, end, session_ids = window_for(db, account.id, session, now)
        flows = account_flows(db, account.id, start, end, session_ids)
        if snapshot is not None:
            later = _later_snapshot(db, account.id, session)
            rows.append({**base, "status": "CAPTURED", **_snapshot_out(snapshot), "breakdown": flows["breakdown"],
                         "pending_outflows": flows["pending_outflows"], "can_correct": later is None})
            continue
        locked = _later_snapshot(db, account.id, session) is not None
        previous_observed = previous.effective_observed if previous else None
        rows.append({**base, "status": "LOCKED" if locked else "PENDING", "is_baseline": previous is None,
                     "snapshot_id": None, "observed_balance": None, "difference": None,
                     "previous_observed_balance": previous_observed,
                     "expected_balance": None if previous is None else _expected(previous_observed, flows),
                     "window_start": start, "window_end": end,
                     **{k: flows[k] for k in ("pos_inflows", "pos_outflows", "registered_external_inflows",
                                              "registered_external_outflows", "breakdown", "pending_outflows")},
                     "corrections": [], "can_correct": False})
    captured = sum(r["status"] == "CAPTURED" for r in rows)
    status = "COMPLETE" if rows and captured == len(rows) else "PARTIAL" if captured else "PENDING"
    unclassified = unclassified_operations(db, session, now)
    return {"cash_session_id": session.id, "session_status": "OPEN" if session.closed_at is None else "CLOSED",
            "accounts": rows, "status": status, "unclassified": unclassified,
            # Never "verified" while electronic money of the period has no destination account.
            "verified": status == "COMPLETE" and unclassified["count"] == 0}


def unclassified_operations(db: Session, session: CashSession, now: datetime | None = None) -> dict:
    """Electronic money of the jornada's window that no account can be credited / debited with: a payment
    (effective channel electronic) without destination account, or a COMPLETED electronic refund / exchange
    difference without account. Never attributed to any account by guessing (Davivienda != DaviPlata)."""
    from app.services.effective_payments import effective_account_ids, effective_channels
    end = _utc(session.closed_at) or (now or _now())
    before = db.scalar(select(CashSession.closed_at).where(CashSession.closed_at.is_not(None), CashSession.id != session.id,
                                                           CashSession.closed_at < end)
                       .order_by(CashSession.closed_at.desc()).limit(1))
    start = _utc(before) if before is not None else _utc(session.opened_at)
    items = []
    rows = db.execute(select(Payment, Sale.number).join(Sale, Sale.id == Payment.sale_id).where(
        Sale.status == SaleStatus.COMPLETED, Sale.completed_at > start, Sale.completed_at <= end)).all()
    payments = [payment for payment, _ in rows]
    channels, accounts = effective_channels(db, payments), effective_account_ids(db, payments)
    for payment, number in rows:
        if not channels[payment.id].affects_cash and accounts[payment.id] is None:
            items.append({"kind": "PAYMENT", "label": f"Pago {channels[payment.id].name} · Venta {number}",
                          "amount": payment.amount})
    for refund in db.scalars(select(SaleRefund).where(SaleRefund.status == "COMPLETED", SaleRefund.affects_cash.is_(False),
                                                      SaleRefund.account_id.is_(None),
                                                      SaleRefund.confirmed_at > start, SaleRefund.confirmed_at <= end)):
        items.append({"kind": "REFUND", "label": f"Reembolso por {refund.channel.name}", "amount": refund.amount})
    for money in db.scalars(select(SaleExchangeSettlement).where(
            SaleExchangeSettlement.status == "COMPLETED", SaleExchangeSettlement.affects_cash.is_(False),
            SaleExchangeSettlement.account_id.is_(None))):
        moment = _utc(money.created_at if money.direction == "IN" else money.confirmed_at)
        if moment and start < moment <= end:
            items.append({"kind": "EXCHANGE", "amount": money.amount,
                          "label": f"Diferencia de cambio {'cobrada' if money.direction == 'IN' else 'devuelta'} por {money.channel.name}"})
    return {"count": len(items), "amount": sum((i["amount"] for i in items), ZERO), "items": items[:30],
            "window_start": start, "window_end": end}


# ---- snapshots (balances typed at the close) ----------------------------------------------------------

def register_balances(db: Session, session: CashSession, balances: list[dict], *, note: str | None = None) -> list[BusinessAccountSnapshot]:
    """Freezes the balances the owner saw for a CLOSED jornada (inside the close transaction or later).
    Each account at most once per jornada (unique), never if a later jornada already has its balance.
    Does not commit: the caller owns the transaction."""
    if session.closed_at is None:
        raise HTTPException(409, "Los saldos de las cuentas se registran al cerrar la jornada")
    seen: set[UUID] = set()
    user = get_operator(db)
    created = []
    for item in balances:
        account = require_account(db, item["account_id"])
        if account.id in seen:
            raise HTTPException(422, f"{account.name} aparece dos veces")
        seen.add(account.id)
        observed = _money(item["observed_balance"])
        if observed < 0:
            raise HTTPException(422, f"El saldo de {account.name} no puede ser negativo")
        if db.scalar(select(BusinessAccountSnapshot).where(BusinessAccountSnapshot.cash_session_id == session.id,
                                                          BusinessAccountSnapshot.business_account_id == account.id)):
            raise HTTPException(409, f"El saldo de {account.name} de esta jornada ya está registrado: usa "
                                     "\"Corregir saldo registrado\"")
        if _later_snapshot(db, account.id, session) is not None:
            raise HTTPException(409, LATER_SNAPSHOT_MESSAGE.format(name=account.name))
        previous, start, end, session_ids = window_for(db, account.id, session)
        flows = account_flows(db, account.id, start, end, session_ids)
        previous_observed = previous.effective_observed if previous else None
        expected = None if previous is None else _expected(previous_observed, flows)
        snapshot = BusinessAccountSnapshot(
            cash_session_id=session.id, business_account_id=account.id, observed_balance=observed,
            is_baseline=previous is None, previous_snapshot_id=previous.id if previous else None,
            previous_observed_balance=previous_observed,
            pos_inflows=flows["pos_inflows"], pos_outflows=flows["pos_outflows"],
            registered_external_inflows=flows["registered_external_inflows"],
            registered_external_outflows=flows["registered_external_outflows"],
            expected_balance=expected, difference=None if expected is None else observed - expected,
            window_start=start, window_end=end, note=(note or "").strip()[:300] or None,
            captured_at=_now(), user_id=user.id)
        db.add(snapshot); created.append(snapshot)
    db.flush()
    return created


def register_balances_after_close(db: Session, session_id: UUID, balances: list[dict], note: str | None = None) -> CashSession:
    """PUT .../account-balances: complete a pending reconciliation after the close."""
    try:
        session = db.scalar(select(CashSession).where(CashSession.id == session_id).with_for_update())
        if session is None:
            raise HTTPException(404, "Jornada no encontrada")
        if not balances:
            raise HTTPException(422, "Escribe al menos un saldo")
        register_balances(db, session, balances, note=note)
        db.commit()
        return session
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Ese saldo ya fue registrado")
    except Exception:
        db.rollback()
        raise


def correct_snapshot(db: Session, snapshot_id: UUID, new_value, reason: str, idempotency_key: str) -> BusinessAccountSnapshot:
    """"Corregir saldo registrado": an audited correction (old / new / reason / when). The snapshot row is
    never edited. Only the latest balance of an account can be corrected (a later one already used it)."""
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise HTTPException(422, "Escribe el motivo de la corrección")
    try:
        existing = db.scalar(select(BusinessAccountSnapshotCorrection)
                             .where(BusinessAccountSnapshotCorrection.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.snapshot_id != snapshot_id:
                raise HTTPException(409, "La clave de idempotencia ya se usó en otro saldo")
            return db.get(BusinessAccountSnapshot, snapshot_id)
        snapshot = db.scalar(select(BusinessAccountSnapshot).where(BusinessAccountSnapshot.id == snapshot_id)
                             .with_for_update())
        if snapshot is None:
            raise HTTPException(404, "Saldo registrado no encontrado")
        session = db.get(CashSession, snapshot.cash_session_id)
        account = db.get(BusinessAccount, snapshot.business_account_id)
        if _later_snapshot(db, snapshot.business_account_id, session) is not None:
            raise HTTPException(409, LATER_SNAPSHOT_MESSAGE.format(name=account.name))
        value = _money(new_value)
        if value < 0:
            raise HTTPException(422, "El saldo no puede ser negativo")
        current = snapshot.effective_observed
        if value == current:
            raise HTTPException(422, "El saldo corregido es igual al registrado")
        db.add(BusinessAccountSnapshotCorrection(snapshot_id=snapshot.id, old_observed_balance=current,
                                                 new_observed_balance=value, reason=reason[:300],
                                                 user_id=get_operator(db).id, idempotency_key=idempotency_key,
                                                 created_at=_now()))
        db.commit()
        db.expire(snapshot)
        return db.get(BusinessAccountSnapshot, snapshot_id)
    except Exception:
        db.rollback()
        raise


# ---- explicit external movements ----------------------------------------------------------------------

def _movement_session(db: Session) -> CashSession:
    """External movements belong to the open jornada; with none open, to the last closed one (completing its
    reconciliation). The jornada fixes the window they are counted in."""
    session = db.scalar(select(CashSession).where(CashSession.closed_at.is_(None)).with_for_update())
    if session is None:
        session = db.scalar(select(CashSession).where(CashSession.closed_at.is_not(None))
                            .order_by(CashSession.closed_at.desc()).limit(1).with_for_update())
    if session is None:
        raise HTTPException(409, "No hay jornada para registrar el movimiento")
    return session


def _check_open_window(db: Session, session: CashSession, account: BusinessAccount) -> None:
    if db.scalar(select(BusinessAccountSnapshot).where(BusinessAccountSnapshot.cash_session_id == session.id,
                                                      BusinessAccountSnapshot.business_account_id == account.id)):
        raise HTTPException(409, f"El saldo de {account.name} de esta jornada ya se registró: abre la caja para "
                                 "registrar el movimiento en la siguiente jornada")


def _movement_out(m: BusinessAccountMovement, names: dict) -> dict:
    return {"id": m.id, "account_id": m.business_account_id, "account_name": names.get(m.business_account_id),
            "cash_session_id": m.cash_session_id, "direction": m.direction, "amount": m.amount, "reason": m.reason,
            "reason_label": MOVEMENT_REASONS.get(m.reason, m.reason), "note": m.note,
            "transfer_group_id": m.transfer_group_id, "reversal_of_id": m.reversal_of_id, "created_at": m.created_at}


def create_movement(db: Session, *, reason: str, account_id: UUID, amount, note: str | None,
                    to_account_id: UUID | None, idempotency_key: str) -> list[BusinessAccountMovement]:
    """OTHER_IN / OTHER_OUT on one account, or TRANSFER_BETWEEN_ACCOUNTS = OUT of `account_id` + IN of
    `to_account_id`, atomically and linked (net zero for the business: never income)."""
    if reason not in ("OTHER_IN", "OTHER_OUT", "TRANSFER_BETWEEN_ACCOUNTS"):
        raise HTTPException(422, "Tipo de movimiento desconocido")
    note = (note or "").strip()
    if reason != "TRANSFER_BETWEEN_ACCOUNTS" and len(note) < 3:
        raise HTTPException(422, "Describe el movimiento")
    value = _money(amount)
    if value <= 0:
        raise HTTPException(422, "El valor debe ser mayor que cero")
    try:
        existing = db.scalars(select(BusinessAccountMovement).where(
            BusinessAccountMovement.idempotency_key.in_([idempotency_key, f"{idempotency_key}:in"]))).all()
        if existing:
            return sorted(existing, key=lambda m: m.direction != "OUT")
        session = _movement_session(db)
        source = require_account(db, account_id)
        user = get_operator(db)
        common = {"cash_session_id": session.id, "amount": value, "note": note[:300] or None, "user_id": user.id,
                  "created_at": _now()}
        if reason == "TRANSFER_BETWEEN_ACCOUNTS":
            target = require_account(db, to_account_id, label="La cuenta destino")
            if target.id == source.id:
                raise HTTPException(422, "La transferencia necesita dos cuentas distintas")
            for account in (source, target):
                _check_open_window(db, session, account)
            group = uuid4()
            rows = [BusinessAccountMovement(business_account_id=source.id, direction="OUT", reason=reason,
                                            transfer_group_id=group, idempotency_key=idempotency_key, **common),
                    BusinessAccountMovement(business_account_id=target.id, direction="IN", reason=reason,
                                            transfer_group_id=group, idempotency_key=f"{idempotency_key}:in", **common)]
        else:
            if to_account_id is not None:
                raise HTTPException(422, "Solo una transferencia lleva cuenta destino")
            _check_open_window(db, session, source)
            rows = [BusinessAccountMovement(business_account_id=source.id, reason=reason,
                                            direction="IN" if reason == "OTHER_IN" else "OUT",
                                            idempotency_key=idempotency_key, **common)]
        db.add_all(rows); db.commit()
        return rows
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "El movimiento ya fue registrado")
    except Exception:
        db.rollback()
        raise


def reverse_movement(db: Session, movement_id: UUID, reason: str, idempotency_key: str) -> list[BusinessAccountMovement]:
    """Undo a mistaken external movement with opposite REVERSAL row(s) (a transfer: both legs). The original
    stays; each movement can be reversed once (unique reversal_of_id); a reversal is never reversed."""
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise HTTPException(422, "Escribe el motivo de la reversión")
    try:
        existing = db.scalars(select(BusinessAccountMovement).where(
            BusinessAccountMovement.idempotency_key.like(f"{idempotency_key}%"))).all()
        if existing:
            return existing
        original = db.get(BusinessAccountMovement, movement_id)
        if original is None:
            raise HTTPException(404, "Movimiento no encontrado")
        if original.reason == "REVERSAL":
            raise HTTPException(409, "Una reversión no se puede revertir")
        legs = ([original] if original.transfer_group_id is None else db.scalars(
            select(BusinessAccountMovement).where(BusinessAccountMovement.transfer_group_id == original.transfer_group_id,
                                                  BusinessAccountMovement.reason == "TRANSFER_BETWEEN_ACCOUNTS")).all())
        if db.scalar(select(BusinessAccountMovement).where(
                BusinessAccountMovement.reversal_of_id.in_([leg.id for leg in legs]))):
            raise HTTPException(409, "Este movimiento ya fue revertido")
        session = _movement_session(db)
        user = get_operator(db)
        group = uuid4() if original.transfer_group_id else None
        rows = []
        for index, leg in enumerate(legs):
            account = db.get(BusinessAccount, leg.business_account_id)
            _check_open_window(db, session, account)
            rows.append(BusinessAccountMovement(
                business_account_id=leg.business_account_id, cash_session_id=session.id,
                direction="OUT" if leg.direction == "IN" else "IN", amount=leg.amount, reason="REVERSAL",
                note=reason[:300], transfer_group_id=group, reversal_of_id=leg.id,
                idempotency_key=f"{idempotency_key}:{index}", user_id=user.id, created_at=_now()))
        db.add_all(rows); db.commit()
        return rows
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Este movimiento ya fue revertido")
    except Exception:
        db.rollback()
        raise


def list_movements(db: Session, session_id: UUID | None = None, limit: int = 200) -> list[dict]:
    names = {a.id: a.name for a in list_accounts(db)}
    statement = select(BusinessAccountMovement)
    if session_id is not None:
        statement = statement.where(BusinessAccountMovement.cash_session_id == session_id)
    reversed_ids = set(db.scalars(select(BusinessAccountMovement.reversal_of_id)
                                  .where(BusinessAccountMovement.reversal_of_id.is_not(None))))
    return [{**_movement_out(m, names), "reversed": m.id in reversed_ids}
            for m in db.scalars(statement.order_by(BusinessAccountMovement.created_at.desc()).limit(limit))]


def movements_out(db: Session, rows: list[BusinessAccountMovement]) -> list[dict]:
    names = {a.id: a.name for a in list_accounts(db)}
    return [{**_movement_out(m, names), "reversed": False} for m in rows]


# ---- history (Informes) -------------------------------------------------------------------------------

def accounts_history(db: Session, limit: int = 60) -> dict:
    """Last `limit` closed jornadas (newest first) with each account's frozen balance, expected and
    difference; jornadas without a balance say so (never 0). Balances are not summed with cash: they are
    balances, not profit."""
    accounts = list_accounts(db)
    sessions = db.scalars(select(CashSession).where(CashSession.closed_at.is_not(None))
                          .order_by(CashSession.closed_at.desc()).limit(limit)).all()
    snapshots = {(s.cash_session_id, s.business_account_id): s for s in db.scalars(
        select(BusinessAccountSnapshot).where(BusinessAccountSnapshot.cash_session_id.in_([s.id for s in sessions])))}
    rows = []
    for session in sessions:
        cells = []
        for account in accounts:
            snapshot = snapshots.get((session.id, account.id))
            cells.append({"account_id": account.id, "status": "CAPTURED" if snapshot else "NONE",
                          **(_snapshot_out(snapshot) if snapshot else {})})
        rows.append({"cash_session_id": session.id, "opened_at": session.opened_at, "closed_at": session.closed_at,
                     "accounts": cells})
    latest = {}
    for account in accounts:
        last = db.scalar(select(BusinessAccountSnapshot).join(CashSession, CashSession.id == BusinessAccountSnapshot.cash_session_id)
                         .where(BusinessAccountSnapshot.business_account_id == account.id)
                         .order_by(CashSession.closed_at.desc()).limit(1))
        latest[str(account.id)] = None if last is None else {**_snapshot_out(last), "cash_session_id": last.cash_session_id}
    return {"accounts": [{"id": a.id, "code": a.code, "name": a.name, "active": a.active} for a in accounts],
            "latest": latest, "sessions": rows}

