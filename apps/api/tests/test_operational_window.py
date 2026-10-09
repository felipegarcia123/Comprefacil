from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.domain.enums import PaymentGroup, PaymentMethod, SaleStatus
from app.models import CashSession, OperationalCategory, Payment, PaymentChannel, Sale, SaleItem, User
from app.services.operations import daily_summary, resolve_operational_window


@pytest.fixture()
def window_context():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(name="Cajero")
        supercat = OperationalCategory(code="SUPER", name="Super")
        cash = PaymentChannel(name="Efectivo", payment_type=PaymentMethod.CASH,
                              payment_group=PaymentGroup.CASH, affects_cash=True)
        db.add_all([user, supercat, cash]); db.commit()
        yield db, user, supercat, cash


def _sale(db, number, when, amount, category, channel, user):
    row = Sale(number=number, status=SaleStatus.COMPLETED, created_by=user.id,
              total=Decimal(amount), completed_at=when)
    db.add(row); db.flush()
    db.add(SaleItem(sale_id=row.id, operational_category_id=category.id, category_code=category.code,
                    product_name="Test", quantity=Decimal("1"), unit_price=Decimal(amount),
                    unit_cost=Decimal("0"), line_total=Decimal(amount)))
    db.add(Payment(sale_id=row.id, method=channel.payment_type, payment_channel_id=channel.id, amount=Decimal(amount)))
    db.commit()
    return row


def test_open_session_includes_operation_after_opened_at(window_context):
    db, user, supercat, cash = window_context
    opened_at = datetime(2026, 9, 20, 13, 0, tzinfo=timezone.utc)  # 08:00 Bogotá
    db.add(CashSession(opening_amount=Decimal("0"), opened_by=user.id, opened_at=opened_at)); db.commit()
    _sale(db, "A", datetime(2026, 9, 20, 14, 0, tzinfo=timezone.utc), "10000", supercat, cash, user)  # 09:00 Bogotá
    now = datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc)
    date_from, date_to, status = resolve_operational_window(db, now=now)
    assert status == "OPEN" and date_from == opened_at and date_to == now
    assert daily_summary(db, date_from, date_to, session_status=status).sales_total == Decimal("10000")


def test_operation_before_opened_at_is_excluded(window_context):
    db, user, supercat, cash = window_context
    opened_at = datetime(2026, 9, 20, 13, 0, tzinfo=timezone.utc)
    db.add(CashSession(opening_amount=Decimal("0"), opened_by=user.id, opened_at=opened_at)); db.commit()
    _sale(db, "BEFORE", datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc), "5000", supercat, cash, user)
    now = datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc)
    date_from, date_to, status = resolve_operational_window(db, now=now)
    assert daily_summary(db, date_from, date_to, session_status=status).sales_total == Decimal("0")


def test_session_crossing_midnight_keeps_all_operations(window_context):
    db, user, supercat, cash = window_context
    opened_at = datetime(2026, 9, 20, 22, 0, tzinfo=timezone.utc)  # 17:00 Bogotá, day 20
    db.add(CashSession(opening_amount=Decimal("0"), opened_by=user.id, opened_at=opened_at)); db.commit()
    _sale(db, "LATE", datetime(2026, 9, 20, 23, 0, tzinfo=timezone.utc), "3000", supercat, cash, user)
    _sale(db, "NEXTDAY", datetime(2026, 9, 21, 2, 0, tzinfo=timezone.utc), "7000", supercat, cash, user)  # after midnight Bogotá
    now = datetime(2026, 9, 21, 3, 0, tzinfo=timezone.utc)
    date_from, date_to, status = resolve_operational_window(db, now=now)
    assert status == "OPEN"
    assert daily_summary(db, date_from, date_to, session_status=status).sales_total == Decimal("10000")


def test_no_open_session_uses_last_closed_session_window(window_context):
    db, user, supercat, cash = window_context
    opened_at = datetime(2026, 9, 19, 13, 0, tzinfo=timezone.utc)
    closed_at = datetime(2026, 9, 19, 22, 0, tzinfo=timezone.utc)
    db.add(CashSession(opening_amount=Decimal("0"), opened_by=user.id, opened_at=opened_at,
                       closed_at=closed_at, closed_by=user.id, closing_expected=Decimal("0"),
                       closing_actual=Decimal("0"), difference=Decimal("0")))
    db.commit()
    _sale(db, "IN", datetime(2026, 9, 19, 14, 0, tzinfo=timezone.utc), "4000", supercat, cash, user)
    now = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)
    date_from, date_to, status = resolve_operational_window(db, now=now)
    assert status == "CLOSED" and date_from == opened_at and date_to == closed_at
    assert daily_summary(db, date_from, date_to, session_status=status).sales_total == Decimal("4000")


def test_no_session_ever_falls_back_to_calendar_day(window_context):
    db, _, _, _ = window_context
    now = datetime(2026, 9, 20, 14, 0, tzinfo=timezone.utc)
    _, _, status = resolve_operational_window(db, now=now)
    assert status == "NONE"
