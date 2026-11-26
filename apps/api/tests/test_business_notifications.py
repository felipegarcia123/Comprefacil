"""Synthetic notification tests; no OpenAI calls or operational database."""

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.business_notifications import get_notification_reader, get_notification_writer
from app.core.config import settings
from app.core.db import Base
from app.domain.enums import InventoryMovementType, PurchasePaymentTerms, PurchaseStatus
from app.main import app
from app.models import BusinessReminder, InventoryMovement, Product, Purchase, Supplier, User
from app.schemas.business_notifications import ReminderDraft
from app.services.business_notifications import (NotificationActor, create_reminder,
    due_in_bogota, generate_alerts, list_pending, parse_actors)


BOGOTA = ZoneInfo("America/Bogota")


@pytest.fixture
def records():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    a, b = User(name="Comerciante A"), User(name="Comerciante B")
    supplier = Supplier(name="Proveedor sintético")
    low = Product(name="Arroz", sale_price=10, unit_cost=5, minimum_stock=2)
    untracked = Product(name="Sin movimientos", sale_price=10, unit_cost=5, minimum_stock=3)
    db.add_all([a, b, supplier, low, untracked]); db.flush()
    db.add(InventoryMovement(product_id=low.id, movement_type=InventoryMovementType.INITIAL_STOCK,
                             quantity=1, unit_cost=5, user_id=a.id))
    today = datetime.now(BOGOTA).date()

    def purchase(status, terms, due, delivery):
        row = Purchase(supplier_id=supplier.id, status=status, created_by=a.id,
            payment_terms=terms, payment_due_date=due, expected_delivery_date=delivery,
            scheduled_at=datetime.now(timezone.utc) if status == PurchaseStatus.SCHEDULED else None,
            received_at=datetime.now(timezone.utc) if status == PurchaseStatus.RECEIVED else None,
            estimated_total=Decimal("20"), total=Decimal("20"))
        db.add(row)
        return row

    purchase(PurchaseStatus.RECEIVED, PurchasePaymentTerms.CREDIT, today + timedelta(days=1), None)
    purchase(PurchaseStatus.RECEIVED, PurchasePaymentTerms.CREDIT, today - timedelta(days=1), None)
    purchase(PurchaseStatus.SCHEDULED, PurchasePaymentTerms.CASH_ON_DELIVERY, None,
             today + timedelta(days=2))
    purchase(PurchaseStatus.RECEIVED, PurchasePaymentTerms.CASH_ON_DELIVERY, None, None)
    db.flush()
    try:
        yield db, a, b, today
    finally:
        db.close(); engine.dispose()


def test_four_alert_categories_are_deterministic_and_deduplicated(records):
    db, _, _, today = records
    first, truncated = generate_alerts(db, "store-a", today=today)
    second, _ = generate_alerts(db, "store-a", today=today)
    assert not truncated
    assert [item.id for item in first] == [item.id for item in second]
    assert len({item.id for item in first}) == len(first) == 4
    assert {item.category for item in first} == {
        "LOW_STOCK", "SUPPLIER_DUE", "PURCHASE_DELIVERY", "OVERDUE_OBLIGATION"}
    assert all(item.href in {"/inventory", "/suppliers", "/purchases"} for item in first)
    assert len([item for item in first if item.category in {"SUPPLIER_DUE", "OVERDUE_OBLIGATION"}]) == 2
    assert all("Sin movimientos" not in item.title for item in first)


def test_reminder_time_bounds_timezone_and_actor_isolation(records):
    db, user_a, user_b, _ = records
    now = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
    due = datetime(2026, 11, 27, 8, 0)
    a = NotificationActor("a", "store-a", user_a.id, "a" * 40)
    b = NotificationActor("b", "store-a", user_b.id, "b" * 40)
    other_business = NotificationActor("c", "store-b", user_a.id, "c" * 40)
    draft = ReminderDraft(title="Revisar pedido", due_local=due, idempotency_key=uuid4())
    assert due_in_bogota(draft, now=now) == datetime(2026, 11, 27, 13, tzinfo=timezone.utc)
    created = create_reminder(db, a, draft, now=now)
    assert created.id == create_reminder(db, a, draft, now=now).id
    assert len(list_pending(db, a)[0]) == 1
    assert list_pending(db, b)[0] == []
    assert list_pending(db, other_business)[0] == []
    with pytest.raises(Exception):
        create_reminder(db, a, ReminderDraft(title="Otro contenido", due_local=due,
                                            idempotency_key=draft.idempotency_key), now=now)
    with pytest.raises(Exception):
        due_in_bogota(ReminderDraft(title="Pronto", due_local=datetime(2026, 11, 26, 7, 0),
                                   idempotency_key=uuid4()), now=now)
    with pytest.raises(Exception):
        due_in_bogota(ReminderDraft(title="Lejano", due_local=datetime(2028, 1, 1, 8, 0),
                                   idempotency_key=uuid4()), now=now)


def test_api_requires_actor_and_explicit_action_confirmation(records, monkeypatch):
    db, user_a, user_b, today = records
    import json
    actors = [
        {"id": "a", "business_id": "store-a", "user_id": str(user_a.id),
         "access_code": "code-a-32-characters-long-for-test", "api_token": "token-a-32-characters-long-for-test"},
        {"id": "b", "business_id": "store-a", "user_id": str(user_b.id),
         "access_code": "code-b-32-characters-long-for-test", "api_token": "token-b-32-characters-long-for-test"},
    ]
    monkeypatch.setattr(settings, "business_notifications_enabled", True)
    monkeypatch.setattr(settings, "business_notifications_business_id", "store-a")
    monkeypatch.setattr(settings, "business_notifications_actors_json", json.dumps(actors))
    monkeypatch.setattr(settings, "business_notifications_action_secret", "action-secret-32-characters-long-for-test")
    app.dependency_overrides[get_notification_reader] = lambda: db
    app.dependency_overrides[get_notification_writer] = lambda: db
    client = TestClient(app)
    base = "/api/v1/notifications"
    a = {"Authorization": "Bearer token-a-32-characters-long-for-test"}
    b = {"Authorization": "Bearer token-b-32-characters-long-for-test"}
    due_day = today + timedelta(days=2)
    due_local = datetime.combine(due_day, time(8)).isoformat(timespec="minutes")
    draft = {"title": "Revisar pedido", "due_local": due_local, "idempotency_key": str(uuid4())}
    try:
        monkeypatch.setattr(settings, "business_agent_access_token", actors[0]["api_token"])
        assert client.get(base + "/center", headers=a).status_code == 503
        monkeypatch.setattr(settings, "business_agent_access_token", "separate-agent-token-32-characters-for-test")
        assert client.get(base + "/center").status_code == 401
        assert client.get(base + "/center", headers={"Authorization": "Bearer " + "x" * 40}).status_code == 401
        assert len(client.get(base + "/center", headers=a).json()["alerts"]) == 4
        assert client.post(base + "/reminders", headers=a, json={**draft, "approval_token": "invalid"}).status_code == 422
        intent = client.post(base + "/intent", headers=a, json={"action": "create", "draft": draft})
        assert intent.status_code == 200
        approval = intent.json()["approval_token"]
        assert client.post(base + "/reminders", headers=a, json={**draft, "title": "Otro texto",
            "approval_token": approval}).status_code == 403
        created = client.post(base + "/reminders", headers=a,
                              json={**draft, "approval_token": approval})
        assert created.status_code == 201
        reminder_id = created.json()["id"]
        assert client.post(base + "/reminders", headers=a,
                           json={**draft, "approval_token": approval}).json()["id"] == reminder_id
        assert len(client.get(base + "/center", headers=a).json()["reminders"]) == 1
        assert client.get(base + "/center", headers=b).json()["reminders"] == []
        assert client.post(base + "/intent", headers=b, json={"action": "cancel",
            "reminder_id": reminder_id}).status_code == 404
        cancel = client.post(base + "/intent", headers=a, json={"action": "cancel",
            "reminder_id": reminder_id}).json()["approval_token"]
        assert client.post(base + f"/reminders/{reminder_id}/cancel", headers=a,
                           json={"approval_token": cancel}).json()["status"] == "CANCELLED"
        assert client.get(base + "/center", headers=a).json()["reminders"] == []
        second = {**draft, "idempotency_key": str(uuid4())}
        approval = client.post(base + "/intent", headers=a,
            json={"action": "create", "draft": second}).json()["approval_token"]
        second_id = client.post(base + "/reminders", headers=a,
            json={**second, "approval_token": approval}).json()["id"]
        complete = client.post(base + "/intent", headers=a, json={"action": "complete",
            "reminder_id": second_id}).json()["approval_token"]
        assert client.post(base + f"/reminders/{second_id}/complete", headers=a,
                           json={"approval_token": complete}).json()["status"] == "COMPLETED"
        assert db.scalar(select(BusinessReminder).where(BusinessReminder.id == UUID(reminder_id))).status == "CANCELLED"
        assert db.scalar(select(Purchase.total).limit(1)) == Decimal("20")
    finally:
        app.dependency_overrides.clear()


def test_actor_config_rejects_cross_business():
    import json
    value = [{"id": "a", "business_id": "store-b", "user_id": str(uuid4()), "api_token": "x" * 40}]
    with pytest.raises(ValueError):
        parse_actors(json.dumps(value), "store-a")
