"""Isolated COMP-S2-005 PostgreSQL writer smoke test; never use on a real database."""

import os
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.api.business_notifications import get_notification_writer
from app.core.config import settings
from app.models import User
from app.schemas.business_notifications import ReminderDraft
from app.services.business_notifications import NotificationActor, create_reminder, list_pending


def main():
    admin_url = os.environ.get("DATABASE_URL", "")
    parsed = make_url(admin_url)
    if (os.environ.get("COMP_S2_005_PG_TEST") != "1" or parsed.host != "comps2005-db"
            or parsed.database != "comp_s2_005"):
        raise SystemExit("PostgreSQL sintético aislado requerido")
    engine = create_engine(admin_url)
    with engine.begin() as connection:
        exists = connection.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = 'comp_s2_005_notify_writer'"))
        if not exists:
            connection.execute(text("CREATE ROLE comp_s2_005_notify_writer LOGIN PASSWORD 'synthetic_writer_only'"))
        connection.execute(text("GRANT USAGE ON SCHEMA public TO comp_s2_005_notify_writer"))
        connection.execute(text("GRANT SELECT, INSERT, UPDATE ON business_reminders TO comp_s2_005_notify_writer"))
    with Session(engine) as db:
        user = User(name="Comerciante sintético")
        db.add(user); db.commit(); db.refresh(user)
        actor = NotificationActor("merchant", "synthetic-store", user.id, "synthetic-token-32-characters-long")
    settings.business_notifications_database_url = (
        "postgresql+psycopg://comp_s2_005_notify_writer:synthetic_writer_only"
        "@comps2005-db:5432/comp_s2_005")
    due = (datetime.now(ZoneInfo("America/Bogota")) + timedelta(days=2)).replace(
        hour=8, minute=0, second=0, microsecond=0, tzinfo=None)
    draft = ReminderDraft(title="Revisar pedido sintético", due_local=due, idempotency_key=uuid4())
    writer = get_notification_writer(actor)
    try:
        db = next(writer)
        created = create_reminder(db, actor, draft)
    finally:
        writer.close()
    reader = get_notification_writer(actor)
    try:
        db = next(reader)
        items, truncated = list_pending(db, actor)
        assert len(items) == 1 and items[0].id == created.id and not truncated
    finally:
        reader.close()
    engine.dispose()
    print("migration=0033 writer_scope=pass durable_reminder=pass")


if __name__ == "__main__":
    main()
