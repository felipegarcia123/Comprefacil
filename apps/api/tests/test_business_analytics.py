"""Synthetic, isolated contract checks for the business analytics adapter."""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.domain.enums import (InventoryMovementType, PurchasePaymentTerms,
                              PurchaseStatus, SaleStatus)
from app.models import (CashSession, InventoryMovement, PaymentChannel, Product,
    Purchase, Sale, SaleItem, SaleRefund, SaleReturn, SaleReturnLine, Supplier, User)
from app.services.business_analytics import PosBusinessAnalytics
from app.domain.enums import PaymentMethod


@pytest.fixture()
def context():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(name="Test operator")
        supplier = Supplier(name="Synthetic supplier")
        channel = PaymentChannel(name="Test cash", payment_type=PaymentMethod.CASH, affects_cash=True)
        db.add_all([user, supplier, channel])
        db.flush()
        yield db, user, supplier, channel, PosBusinessAnalytics(db)
    engine.dispose()


def test_no_data_is_distinct_from_zero(context):
    _, _, _, _, analytics = context
    day = date(2026, 11, 24)
    assert analytics.sales_summary(day, day).state == "no_data"
    assert analytics.gross_profit(day, day).gross_profit is None
    assert analytics.top_products(day, day).state == "no_data"
    assert analytics.inventory_summary().state == "no_data"
    assert analytics.supplier_obligations(day, day).state == "no_data"
    assert analytics.purchase_agenda(day).state == "no_data"


def test_completed_zero_sale_is_data_not_absence(context):
    db, user, _, _, analytics = context
    product = Product(name="Zero price", sale_price=0, unit_cost=0)
    db.add(product)
    db.flush()
    _sale(db, user, product, number="ZERO",
          at=datetime(2026, 11, 25, 2, tzinfo=timezone.utc),
          amount=Decimal("0"), quantity=Decimal("1"), cost=Decimal("0"))
    result = analytics.sales_summary(date(2026, 11, 24), date(2026, 11, 24))
    assert result.state == "available" and result.completed_sales == 1
    assert result.net_sales == 0


def _sale(db, user, product, *, number, at, status=SaleStatus.COMPLETED,
          amount=Decimal("400"), quantity=Decimal("2"), cost=Decimal("100")):
    sale = Sale(number=number, status=status, created_by=user.id, total=amount,
                completed_at=at if status == SaleStatus.COMPLETED else None)
    db.add(sale)
    db.flush()
    item = SaleItem(sale_id=sale.id, product_id=product.id, product_name=product.name,
                    quantity=quantity, unit_price=amount / quantity, unit_cost=cost,
                    line_total=amount)
    db.add(item)
    db.flush()
    return sale, item


def test_completed_sales_margin_and_bogota_boundary(context):
    db, user, _, _, analytics = context
    product = Product(name="Rice", sale_price=200, unit_cost=100)
    db.add(product)
    db.flush()
    _sale(db, user, product, number="C1",
          at=datetime(2026, 11, 25, 2, tzinfo=timezone.utc))  # Nov 24, 21:00 Bogota
    _sale(db, user, product, number="C2",
          at=datetime(2026, 11, 25, 5, tzinfo=timezone.utc), amount=Decimal("100"),
          quantity=Decimal("1"), cost=Decimal("50"))  # Nov 25, 00:00 Bogota
    _sale(db, user, product, number="CANCEL",
          at=datetime(2026, 11, 25, 2, tzinfo=timezone.utc), status=SaleStatus.CANCELLED)
    db.flush()
    today = analytics.sales_summary(date(2026, 11, 24), date(2026, 11, 24))
    assert today.state == "available" and today.completed_sales == 1
    assert today.gross_sales == today.net_sales == 400
    profit = analytics.gross_profit(date(2026, 11, 24), date(2026, 11, 24))
    assert profit.state == "available" and profit.cost_of_goods == 200
    assert profit.gross_profit == 200 and profit.gross_margin_percent == 50
    top = analytics.top_products(date(2026, 11, 24), date(2026, 11, 24))
    assert len(top.items) == 1 and top.items[0].net_units == 2
    next_day = analytics.sales_summary(date(2026, 11, 25), date(2026, 11, 25))
    assert next_day.completed_sales == 1 and next_day.gross_sales == 100


def test_returns_are_separate_and_profit_becomes_unreliable(context):
    db, user, _, channel, analytics = context
    product = Product(name="Beans", sale_price=200, unit_cost=100)
    db.add(product)
    db.flush()
    sale, item = _sale(db, user, product, number="RETURN",
        at=datetime(2026, 11, 25, 2, tzinfo=timezone.utc))
    session = CashSession(opening_amount=0, opened_by=user.id)
    db.add(session)
    db.flush()
    returned = SaleReturn(sale_id=sale.id, kind="PARTIAL", reason_code="TEST",
        total_amount=Decimal("200"), cash_session_id=session.id, user_id=user.id,
        idempotency_key="synthetic-return",
        created_at=datetime(2026, 11, 25, 3, tzinfo=timezone.utc))
    db.add(returned)
    db.flush()
    db.add_all([
        SaleReturnLine(return_id=returned.id, sale_item_id=item.id, quantity=1,
                       unit_price=200, amount=200, category_code="TEST", restock=True),
        SaleRefund(return_id=returned.id, channel_id=channel.id, affects_cash=True,
                   amount=200, status="COMPLETED"),
    ])
    db.flush()
    day = date(2026, 11, 24)
    summary = analytics.sales_summary(day, day)
    assert summary.gross_sales == 400 and summary.returns_registered == 200
    assert summary.refunds_completed == 200 and summary.net_sales == 200
    assert analytics.gross_profit(day, day).state == "insufficient_data"
    assert analytics.gross_profit(day, day).gross_profit is None
    top = analytics.top_products(day, day)
    assert top.items[0].gross_units == 2 and top.items[0].returned_units == 1
    assert top.items[0].net_units == 1 and top.items[0].net_sales == 200


def test_missing_cost_is_not_reported_as_zero_profit(context):
    db, user, _, _, analytics = context
    product = Product(name="Unknown cost", sale_price=200, unit_cost=0)
    db.add(product)
    db.flush()
    _sale(db, user, product, number="NO-COST",
          at=datetime(2026, 11, 25, 2, tzinfo=timezone.utc), cost=Decimal("0"))
    result = analytics.gross_profit(date(2026, 11, 24), date(2026, 11, 24))
    assert result.state == "insufficient_data" and result.gross_profit is None
    assert result.sales_with_unreliable_cost == 1


def test_inventory_uses_ledger_and_identifies_untracked_catalog(context):
    db, user, _, _, analytics = context
    low = Product(name="Low", sale_price=200, unit_cost=100, minimum_stock=5)
    zero = Product(name="Zero", sale_price=200, unit_cost=100, minimum_stock=2)
    unknown = Product(name="Untracked", sale_price=200, unit_cost=100, minimum_stock=2)
    db.add_all([low, zero, unknown])
    db.flush()
    db.add_all([
        InventoryMovement(product_id=low.id, movement_type=InventoryMovementType.INITIAL_STOCK,
                          quantity=3, unit_cost=100, user_id=user.id),
        InventoryMovement(product_id=zero.id, movement_type=InventoryMovementType.INITIAL_STOCK,
                          quantity=2, unit_cost=100, user_id=user.id),
        InventoryMovement(product_id=zero.id, movement_type=InventoryMovementType.SALE,
                          quantity=-2, unit_cost=100, user_id=user.id),
    ])
    db.flush()
    summary = analytics.inventory_summary()
    assert summary.active_products == 3 and summary.tracked_active_products == 2
    assert summary.untracked_active_products == 1 and summary.out_of_stock_products == 1
    assert summary.low_stock_products == 1 and summary.registered_stock_value == 300
    assert summary.valuation_verified is False
    low_stock = analytics.low_stock()
    assert low_stock.total == 1 and low_stock.items[0].name == "Low"
    assert low_stock.untracked_active_products == 1


def test_inventory_zero_valuation_with_unknown_cost_is_insufficient(context):
    db, user, _, _, analytics = context
    product = Product(name="Unknown cost stock", sale_price=200, unit_cost=0)
    db.add(product)
    db.flush()
    db.add(InventoryMovement(product_id=product.id,
        movement_type=InventoryMovementType.INITIAL_STOCK,
        quantity=3, unit_cost=0, user_id=user.id))
    db.flush()
    result = analytics.inventory_summary()
    assert result.registered_stock_value == 0
    assert result.value_state == "insufficient_data"
    assert result.products_missing_cost == 1


def test_commitments_and_agenda_reuse_purchase_rules_and_signal_cap(context):
    db, user, supplier, _, analytics = context
    scheduled = Purchase(supplier_id=supplier.id, created_by=user.id,
        status=PurchaseStatus.SCHEDULED, scheduled_at=datetime(2026, 11, 24, tzinfo=timezone.utc),
        expected_delivery_date=date(2026, 11, 26), payment_terms=PurchasePaymentTerms.CREDIT,
        payment_due_date=date(2026, 12, 1), estimated_total=Decimal("120"), total=0)
    received = Purchase(supplier_id=supplier.id, created_by=user.id,
        status=PurchaseStatus.RECEIVED, received_at=datetime(2026, 11, 23, tzinfo=timezone.utc),
        payment_terms=PurchasePaymentTerms.CREDIT, payment_due_date=date(2026, 11, 23),
        total=Decimal("80"))
    db.add_all([scheduled, received])
    db.flush()
    obligations = analytics.supplier_obligations(date(2026, 11, 24), date(2026, 12, 2))
    assert obligations.estimated_total == 120
    assert obligations.overdue_outstanding_total == 80
    agenda = analytics.purchase_agenda(date(2026, 11, 24), 30)
    assert agenda.state == "available" and agenda.total_matching == 1
    assert agenda.items[0].payment_due_date == date(2026, 12, 1)
    db.add_all([Purchase(supplier_id=supplier.id, created_by=user.id,
        status=PurchaseStatus.SCHEDULED, scheduled_at=datetime(2026, 11, 24, tzinfo=timezone.utc),
        expected_delivery_date=date(2026, 11, 27), payment_terms=PurchasePaymentTerms.CREDIT,
        payment_due_date=date(2026, 12, 1), estimated_total=Decimal("1"), total=0)
        for _ in range(200)])
    db.flush()
    capped = analytics.purchase_agenda(date(2026, 11, 24), 30)
    assert capped.total_matching == 201 and len(capped.items) == 200
    assert capped.truncated is True and capped.state == "insufficient_data"


def test_invalid_periods_and_limits(context):
    _, _, _, _, analytics = context
    with pytest.raises(ValueError):
        analytics.sales_summary(date(2026, 12, 1), date(2026, 11, 1))
    with pytest.raises(ValueError):
        analytics.top_products(date(2026, 11, 1), date(2026, 11, 1), limit=0)
    with pytest.raises(ValueError):
        analytics.low_stock(limit=101)
    with pytest.raises(ValueError):
        analytics.purchase_agenda(date(2026, 11, 1), horizon_days=366)
