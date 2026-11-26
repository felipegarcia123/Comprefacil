"""Opt-in notification center; actor-scoped writes never enter AI tools."""

from datetime import datetime, timezone
from hmac import compare_digest
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.api.business_agent import readonly_analytics_source
from app.core.config import settings
from app.models import BusinessReminder
from app.schemas.business_notifications import (ActionApproval, ActionIntent, CenterOut,
    ReminderCreate, ReminderDraft, ReminderOut)
from app.services.business_notifications import (NotificationActor, change_reminder,
    create_reminder, generate_alerts, issue_approval, list_pending, parse_actors,
    reminder_out, verify_approval)


router = APIRouter(prefix="/notifications", tags=["business-notifications"])


def require_notification_actor(authorization: str | None = Header(default=None)) -> NotificationActor:
    if not settings.business_notifications_enabled:
        raise HTTPException(404, "No disponible")
    if (not settings.business_notifications_business_id
            or len(settings.business_notifications_action_secret) < 32):
        raise HTTPException(503, "Notificaciones no configuradas")
    try:
        actors = parse_actors(settings.business_notifications_actors_json,
                              settings.business_notifications_business_id)
    except ValueError as exc:
        raise HTTPException(503, "Notificaciones no configuradas") from exc
    if any(compare_digest(actor.api_token, settings.business_agent_access_token) for actor in actors):
        raise HTTPException(503, "Los tokens personales deben ser distintos del token del agente")
    candidate = authorization.removeprefix("Bearer ") if authorization else ""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "No autorizado")
    for actor in actors:
        if compare_digest(candidate, actor.api_token):
            return actor
    raise HTTPException(401, "No autorizado")


def get_notification_reader(_: NotificationActor = Depends(require_notification_actor)):
    source = readonly_analytics_source()
    try:
        analytics = next(source)
        yield analytics.db
    finally:
        source.close()


def get_notification_writer(_: NotificationActor = Depends(require_notification_actor)):
    raw_url = settings.business_notifications_database_url
    if (not raw_url or raw_url in {settings.database_url, settings.business_agent_database_url}):
        raise HTTPException(503, "Base de recordatorios no configurada")
    try:
        if make_url(raw_url).drivername != "postgresql+psycopg":
            raise ValueError
        engine = create_engine(raw_url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
    except Exception as exc:
        raise HTTPException(503, "Base de recordatorios no configurada") from exc
    try:
        with Session(engine, autoflush=False, expire_on_commit=False) as db:
            db.execute(text("SET LOCAL statement_timeout = '5000ms'"))
            unsafe = db.scalar(text("""
                SELECT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)
                    OR has_database_privilege(current_user, current_database(), 'CREATE')
                    OR has_schema_privilege(current_user, 'public', 'CREATE')
                    OR NOT has_table_privilege(current_user, 'public.business_reminders', 'SELECT')
                    OR NOT has_table_privilege(current_user, 'public.business_reminders', 'INSERT')
                    OR NOT has_table_privilege(current_user, 'public.business_reminders', 'UPDATE')
                    OR has_table_privilege(current_user, 'public.business_reminders', 'DELETE')
                    OR has_table_privilege(current_user, 'public.business_reminders', 'TRUNCATE')
                    OR EXISTS (
                        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
                          AND ((c.relkind IN ('r', 'p') AND c.relname <> 'business_reminders'
                          AND (has_table_privilege(current_user, c.oid, 'INSERT')
                            OR has_table_privilege(current_user, c.oid, 'UPDATE')
                            OR has_table_privilege(current_user, c.oid, 'DELETE')
                            OR has_table_privilege(current_user, c.oid, 'TRUNCATE'))
                          OR (c.relkind = 'S' AND (has_sequence_privilege(current_user, c.oid, 'UPDATE')
                            OR has_sequence_privilege(current_user, c.oid, 'USAGE'))))))
            """))
            if unsafe:
                raise HTTPException(503, "El rol de recordatorios requiere permisos limitados")
            yield db
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "Base de recordatorios no disponible") from exc
    finally:
        engine.dispose()


@router.get("/center", response_model=CenterOut)
def center(actor: NotificationActor = Depends(require_notification_actor),
           reader: Session = Depends(get_notification_reader),
           writer: Session = Depends(get_notification_writer)):
    today = datetime.now(ZoneInfo("America/Bogota")).date()
    alerts, alerts_truncated = generate_alerts(reader, actor.business_id, today=today)
    reminders, reminders_truncated = list_pending(writer, actor)
    return CenterOut(as_of=today, alerts=alerts, reminders=reminders,
                     alerts_truncated=alerts_truncated,
                     reminders_truncated=reminders_truncated)


@router.post("/intent")
def approval_intent(body: ActionIntent,
                    actor: NotificationActor = Depends(require_notification_actor),
                    db: Session = Depends(get_notification_writer)):
    if body.action != "create":
        row = db.scalar(select(BusinessReminder).where(
            BusinessReminder.id == body.reminder_id,
            BusinessReminder.business_id == actor.business_id,
            BusinessReminder.user_id == actor.user_id,
            BusinessReminder.status == "PENDING"))
        if row is None:
            raise HTTPException(404, "Recordatorio pendiente no encontrado")
    token = issue_approval(actor, body, settings.business_notifications_action_secret)
    return {"approval_token": token, "expires_in_seconds": 300}


@router.post("/reminders", response_model=ReminderOut, status_code=201)
def new_reminder(body: ReminderCreate,
                 actor: NotificationActor = Depends(require_notification_actor),
                 db: Session = Depends(get_notification_writer)):
    draft = ReminderDraft(title=body.title, due_local=body.due_local,
                          idempotency_key=body.idempotency_key)
    verify_approval(actor, ActionIntent(action="create", draft=draft),
                    body.approval_token, settings.business_notifications_action_secret)
    return create_reminder(db, actor, draft)


@router.post("/reminders/{reminder_id}/{action}", response_model=ReminderOut)
def resolve_reminder(reminder_id: UUID, action: Literal["complete", "cancel"], body: ActionApproval,
                     actor: NotificationActor = Depends(require_notification_actor),
                     db: Session = Depends(get_notification_writer)):
    verify_approval(actor, ActionIntent(action=action, reminder_id=reminder_id),
                    body.approval_token, settings.business_notifications_action_secret)
    return change_reminder(db, actor, reminder_id, action)
