"""Deterministic commercial alerts and scoped personal reminder operations."""

import base64
import hashlib
import hmac
import json
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.enums import PurchasePaymentTerms, PurchaseStatus
from app.models import BusinessReminder, Product, Purchase
from app.schemas.business_notifications import ActionIntent, AlertOut, ReminderDraft, ReminderOut
from app.services.inventory import stock_subquery


BOGOTA = ZoneInfo("America/Bogota")
MAX_ALERTS_PER_CATEGORY = 100
MAX_PENDING_REMINDERS = 100


@dataclass(frozen=True)
class NotificationActor:
    id: str
    business_id: str
    user_id: uuid.UUID
    api_token: str


def parse_actors(raw: str, business_id: str) -> list[NotificationActor]:
    try:
        values = json.loads(raw)
        if not isinstance(values, list) or not values or len(values) > 100:
            raise ValueError
        actors = []
        ids: set[str] = set()
        tokens: set[str] = set()
        for value in values:
            actor_id = value["id"]
            token = value["api_token"]
            if (not isinstance(actor_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", actor_id)
                    or not isinstance(token, str) or len(token) < 32
                    or value["business_id"] != business_id or actor_id in ids or token in tokens):
                raise ValueError
            actors.append(NotificationActor(actor_id, business_id, uuid.UUID(value["user_id"]), token))
            ids.add(actor_id)
            tokens.add(token)
        return actors
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Configuración de actores inválida") from exc


def _alert_id(business_id: str, category: str, entity: str) -> str:
    return hashlib.sha256(f"{business_id}:{category}:{entity}".encode()).hexdigest()[:24]


def generate_alerts(db: Session, business_id: str, *, today: date) -> tuple[list[AlertOut], bool]:
    """Compute a fresh snapshot; no LLM, scheduler, or alert writes."""
    alerts: dict[str, AlertOut] = {}
    truncated = False
    stock = stock_subquery()
    low = db.execute(select(Product.id, Product.name, stock.c.current_stock, Product.minimum_stock)
        .join(stock, stock.c.product_id == Product.id)
        .where(Product.active.is_(True), Product.minimum_stock > 0,
               stock.c.current_stock <= Product.minimum_stock)
        .order_by(stock.c.current_stock, Product.name, Product.id)
        .limit(MAX_ALERTS_PER_CATEGORY + 1)).all()
    truncated |= len(low) > MAX_ALERTS_PER_CATEGORY
    for product_id, name, quantity, minimum in low[:MAX_ALERTS_PER_CATEGORY]:
        key = _alert_id(business_id, "LOW_STOCK", str(product_id))
        alerts[key] = AlertOut(id=key, category="LOW_STOCK",
            priority="URGENT" if quantity <= 0 else "HIGH", title=f"Stock bajo: {name}",
            detail=f"Registrado: {quantity}; mínimo configurado: {minimum}.", href="/inventory")

    due = db.scalars(select(Purchase).where(
        Purchase.status == PurchaseStatus.RECEIVED,
        Purchase.payment_terms == PurchasePaymentTerms.CREDIT,
        Purchase.payment_due_date.is_not(None),
        Purchase.payment_due_date <= today + timedelta(days=3),
        ~Purchase.payment.has())
        .order_by(Purchase.payment_due_date, Purchase.id)
        .limit(MAX_ALERTS_PER_CATEGORY + 1)).all()
    truncated |= len(due) > MAX_ALERTS_PER_CATEGORY
    for purchase in due[:MAX_ALERTS_PER_CATEGORY]:
        overdue = purchase.payment_due_date < today
        category = "OVERDUE_OBLIGATION" if overdue else "SUPPLIER_DUE"
        key = _alert_id(business_id, category, str(purchase.id))
        alerts[key] = AlertOut(id=key, category=category,
            priority="URGENT" if overdue else "HIGH",
            title="Obligación vencida" if overdue else "Obligación próxima a vencer",
            detail=f"Compra con saldo pendiente; vencimiento registrado: {purchase.payment_due_date}.",
            href="/suppliers", due_date=purchase.payment_due_date)

    deliveries = db.scalars(select(Purchase).where(
        Purchase.status == PurchaseStatus.SCHEDULED,
        Purchase.expected_delivery_date.is_not(None),
        Purchase.expected_delivery_date <= today + timedelta(days=7))
        .order_by(Purchase.expected_delivery_date, Purchase.id)
        .limit(MAX_ALERTS_PER_CATEGORY + 1)).all()
    truncated |= len(deliveries) > MAX_ALERTS_PER_CATEGORY
    for purchase in deliveries[:MAX_ALERTS_PER_CATEGORY]:
        key = _alert_id(business_id, "PURCHASE_DELIVERY", str(purchase.id))
        alerts[key] = AlertOut(id=key, category="PURCHASE_DELIVERY",
            priority="HIGH" if purchase.expected_delivery_date <= today else "NORMAL",
            title="Entrega de compra programada",
            detail=f"Entrega registrada para {purchase.expected_delivery_date}.",
            href="/purchases", due_date=purchase.expected_delivery_date)
    rank = {"URGENT": 0, "HIGH": 1, "NORMAL": 2}
    return sorted(alerts.values(), key=lambda alert: (rank[alert.priority], alert.due_date or today,
                                                      alert.category, alert.id)), truncated


def due_in_bogota(draft: ReminderDraft, *, now: datetime | None = None) -> datetime:
    current = (now or datetime.now(timezone.utc)).astimezone(BOGOTA)
    local = draft.due_local
    if local.tzinfo is not None or local.second or local.microsecond:
        raise HTTPException(422, "Indica fecha y hora local de Bogotá, con precisión de minutos")
    due = local.replace(tzinfo=BOGOTA)
    if not current + timedelta(minutes=1) <= due <= current + timedelta(days=365):
        raise HTTPException(422, "El recordatorio debe estar entre un minuto y 365 días en el futuro")
    return due.astimezone(timezone.utc)


def reminder_out(row: BusinessReminder) -> ReminderOut:
    return ReminderOut(id=row.id, title=row.title, due_at=row.due_at, timezone=row.timezone,
                       status=row.status, created_at=row.created_at)


def list_pending(db: Session, actor: NotificationActor) -> tuple[list[ReminderOut], bool]:
    rows = db.scalars(select(BusinessReminder).where(
        BusinessReminder.business_id == actor.business_id,
        BusinessReminder.user_id == actor.user_id,
        BusinessReminder.status == "PENDING")
        .order_by(BusinessReminder.due_at, BusinessReminder.id)
        .limit(MAX_PENDING_REMINDERS + 1)).all()
    return [reminder_out(row) for row in rows[:MAX_PENDING_REMINDERS]], len(rows) > MAX_PENDING_REMINDERS


def create_reminder(db: Session, actor: NotificationActor, draft: ReminderDraft,
                    *, now: datetime | None = None) -> ReminderOut:
    title = draft.title.strip()
    if len(title) < 3:
        raise HTTPException(422, "Escribe un recordatorio de al menos 3 caracteres")
    due_at = due_in_bogota(draft, now=now)
    existing = db.scalar(select(BusinessReminder).where(
        BusinessReminder.business_id == actor.business_id,
        BusinessReminder.user_id == actor.user_id,
        BusinessReminder.idempotency_key == draft.idempotency_key))
    if existing:
        stored_due = existing.due_at if existing.due_at.tzinfo else existing.due_at.replace(tzinfo=timezone.utc)
        if existing.title != title or stored_due.astimezone(timezone.utc) != due_at:
            raise HTTPException(409, "La solicitud ya se usó para otro recordatorio")
        return reminder_out(existing)
    row = BusinessReminder(business_id=actor.business_id, user_id=actor.user_id,
        title=title, due_at=due_at, timezone="America/Bogota", status="PENDING",
        idempotency_key=draft.idempotency_key)
    db.add(row)
    try:
        db.commit()
        db.refresh(row)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "La solicitud ya se procesó; consulta tus recordatorios") from exc
    return reminder_out(row)


def change_reminder(db: Session, actor: NotificationActor, reminder_id: uuid.UUID,
                    action: str, *, now: datetime | None = None) -> ReminderOut:
    row = db.scalar(select(BusinessReminder).where(
        BusinessReminder.id == reminder_id,
        BusinessReminder.business_id == actor.business_id,
        BusinessReminder.user_id == actor.user_id))
    if row is None:
        raise HTTPException(404, "Recordatorio no encontrado")
    status = "COMPLETED" if action == "complete" else "CANCELLED"
    if row.status == status:
        return reminder_out(row)
    if row.status != "PENDING":
        raise HTTPException(409, "El recordatorio ya fue resuelto")
    moment = now or datetime.now(timezone.utc)
    values = {"status": status, "updated_at": moment,
              "completed_at" if action == "complete" else "cancelled_at": moment}
    changed = db.execute(update(BusinessReminder).where(
        BusinessReminder.id == reminder_id,
        BusinessReminder.business_id == actor.business_id,
        BusinessReminder.user_id == actor.user_id,
        BusinessReminder.status == "PENDING").values(**values))
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "El recordatorio ya fue resuelto")
    db.commit()
    db.expire(row)
    return reminder_out(db.get(BusinessReminder, reminder_id))


def _intent_payload(actor: NotificationActor, intent: ActionIntent,
                    *, now: datetime | None = None) -> bytes:
    if intent.action == "create":
        if intent.draft is None or intent.reminder_id is not None:
            raise HTTPException(422, "Faltan los datos del recordatorio")
        due_in_bogota(intent.draft, now=now)
    elif intent.reminder_id is None or intent.draft is not None:
        raise HTTPException(422, "Falta el identificador del recordatorio")
    data = {"actor": actor.id, "business": actor.business_id, "user": str(actor.user_id),
            **intent.model_dump(mode="json")}
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def issue_approval(actor: NotificationActor, intent: ActionIntent, secret: str,
                   *, now: datetime | None = None) -> str:
    payload = _intent_payload(actor, intent, now=now)
    expires = int((now or datetime.now(timezone.utc)).timestamp()) + 300
    header = f"{expires}.{secrets.token_hex(12)}".encode()
    signature = hmac.new(secret.encode(), header + b"." + payload, hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(header).decode().rstrip("=") + "." + signature


def verify_approval(actor: NotificationActor, intent: ActionIntent, token: str, secret: str,
                    *, now: datetime | None = None) -> None:
    try:
        encoded, signature = token.split(".")
        header = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        expires_raw, _ = header.split(b".", 1)
        expires = int(expires_raw)
        if expires < int((now or datetime.now(timezone.utc)).timestamp()):
            raise ValueError
        expected = hmac.new(secret.encode(), header + b"." + _intent_payload(actor, intent, now=now),
                            hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
    except (ValueError, TypeError, UnicodeError) as exc:
        raise HTTPException(403, "La confirmación venció o no corresponde a esta acción") from exc
