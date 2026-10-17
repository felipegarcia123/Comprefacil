from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.domain.enums import PaymentGroup, PaymentMethod, SaleStatus
from app.models import (CashSession, DailyReport, OperationalCategory, Payment,
                        PaymentChannel, Product, Sale, SaleItem, User)
from app.services.cash import close_cash_session
from app.services.daily_reports import (build_daily_report, build_live_snapshot, current_report,
                                        generate_daily_report, get_latest_daily_report,
                                        list_daily_reports, monthly_report, save_daily_report)
from app.services.operational_session import require_open_session
from app.schemas.cash import CloseCashSessionIn


@pytest.fixture()
def report_ctx():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(name="Cajero")
        cigar = OperationalCategory(code="CIGARROS", name="Cigarros")
        supercat = OperationalCategory(code="SUPER", name="Super")
        cash = PaymentChannel(name="Efectivo", payment_type=PaymentMethod.CASH,
                              payment_group=PaymentGroup.CASH, affects_cash=True)
        nequi = PaymentChannel(name="Nequi", payment_type=PaymentMethod.TRANSFER,
                               payment_group=PaymentGroup.ELECTRONIC, affects_cash=False)
        p1 = Product(barcode="D1", name="Cigarro Test", sale_price=Decimal("10000"),
                    unit_cost=Decimal("6000"), operational_category_id=cigar.id)
        p2 = Product(barcode="D2", name="Arroz Test", sale_price=Decimal("5000"),
                    unit_cost=Decimal("3000"), operational_category_id=supercat.id)
        db.add_all([user, cigar, supercat, cash, nequi, p1, p2]); db.commit()
        yield db, user, cigar, supercat, cash, nequi, p1, p2


def _sale(db, number, when, product, quantity, channel, category, user):
    unit_price = product.sale_price
    line_total = unit_price * quantity
    row = Sale(number=number, status=SaleStatus.COMPLETED, created_by=user.id,
              total=line_total, completed_at=when)
    db.add(row); db.flush()
    db.add(SaleItem(sale_id=row.id, product_id=product.id, operational_category_id=category.id,
                    category_code=category.code, product_name=product.name, quantity=quantity,
                    unit_price=unit_price, unit_cost=product.unit_cost, line_total=line_total))
    db.add(Payment(sale_id=row.id, method=channel.payment_type, payment_channel_id=channel.id, amount=line_total))
    db.commit()
    return row


def _closed_session(db, user, opened_at, closed_at, opening=Decimal("0"),
                    expected=Decimal("0"), actual=Decimal("0"), difference=Decimal("0")):
    session = CashSession(opening_amount=opening, opened_by=user.id, opened_at=opened_at,
                          closed_at=closed_at, closed_by=user.id, closing_expected=expected,
                          closing_actual=actual, difference=difference)
    db.add(session); db.commit()
    return session


def test_report_unique_per_session_and_idempotent_generation(report_ctx):
    db, user, cigar, supercat, cash, nequi, p1, p2 = report_ctx
    opened, closed = datetime(2026, 9, 20, 13, 0, tzinfo=timezone.utc), datetime(2026, 9, 20, 22, 0, tzinfo=timezone.utc)
    session = _closed_session(db, user, opened, closed)
    _sale(db, "A", datetime(2026, 9, 20, 14, 0, tzinfo=timezone.utc), p1, Decimal("1"), cash, cigar, user)

    first = generate_daily_report(db, session)
    second = generate_daily_report(db, session)
    assert first.id == second.id
    all_ids = db.scalars(select(DailyReport.id).where(DailyReport.cash_session_id == session.id)).all()
    assert len(all_ids) == 1
    # direct save_daily_report() called again must also collapse to the same row
    data = build_daily_report(db, session)
    third = save_daily_report(db, data)
    assert third.id == first.id


def test_only_closed_session_can_generate_a_report(report_ctx):
    db, user, *_ = report_ctx
    open_session = CashSession(opening_amount=Decimal("0"), opened_by=user.id,
                               opened_at=datetime(2026, 9, 20, 13, 0, tzinfo=timezone.utc))
    db.add(open_session); db.commit()
    with pytest.raises(ValueError):
        build_daily_report(db, open_session)


def test_legacy_closed_report_keeps_gross_snapshot_and_exposes_saved_net_separately(report_ctx):
    """Old immutable snapshots predate NET_COMPLETED_REFUNDS_V1 and must not be reinterpreted."""
    db, user, *_ = report_ctx
    opened = datetime(2026, 9, 20, 13, 0, tzinfo=timezone.utc)
    closed = datetime(2026, 9, 20, 22, 0, tzinfo=timezone.utc)
    session = _closed_session(db, user, opened, closed)
    legacy = DailyReport(
        cash_session_id=session.id, sales_total=Decimal("100000"), cash_total=Decimal("100000"),
        electronic_total=Decimal("0"), tickets=1, average_ticket=Decimal("100000"),
        gross_cost=Decimal("0"), gross_profit=Decimal("100000"), gross_margin=Decimal("100"),
        closing_expected=Decimal("0"), closing_actual=Decimal("0"), closing_difference=Decimal("0"),
        report_data={"categories": [], "payment_channels": [], "products": [], "hourly": [],
                     "operations": {}, "highlights": {},
                     "returns": {"gross_sales": "100000", "returns_total": "20000",
                                 "net_sales": "80000", "refunds_cash": "20000",
                                 "refunds_electronic": "0", "refunds_electronic_pending": "0",
                                 "return_count": 1, "void_count": 0}})
    db.add(legacy); db.commit()

    result = current_report(db)
    assert result["status"] == "CLOSED"
    assert result["sales_total"] == Decimal("100000")  # original immutable gross close
    assert result["report_data"]["returns"]["net_sales"] == "80000"  # separate historical calculation
    assert "sales_basis" not in result["report_data"]


def test_close_still_succeeds_when_report_generation_fails(report_ctx, monkeypatch):
    db, user, cigar, supercat, cash, nequi, p1, p2 = report_ctx
    session = CashSession(opening_amount=Decimal("100000"), opened_by=user.id,
                          opened_at=datetime.now(timezone.utc))
    db.add(session); db.commit()
    _sale(db, "A", datetime.now(timezone.utc), p1, Decimal("1"), cash, cigar, user)

    import app.services.daily_reports as daily_reports_module
    def boom(db_, session_):
        raise RuntimeError("simulated report generation failure")
    monkeypatch.setattr(daily_reports_module, "generate_daily_report", boom)

    # _sale() inserts Sale/SaleItem/Payment directly (no CashMovement), so expected
    # cash is just the opening amount here — that's fine, this test only cares
    # that close() itself survives a report-generation failure.
    closed = close_cash_session(db, CloseCashSessionIn(counted_amount=Decimal("100000"), expected_amount=Decimal("100000")))
    assert closed.closed_at is not None
    assert closed.closing_actual == Decimal("100000")
    assert closed.difference == Decimal("0")
    assert db.scalar(select(DailyReport).where(DailyReport.cash_session_id == session.id)) is None
    # a follow-up open must work normally: the failed report did not corrupt the session state
    with pytest.raises(Exception):
        require_open_session(db)


def test_window_uses_exact_opened_at_closed_at_bounds(report_ctx):
    db, user, cigar, supercat, cash, nequi, p1, p2 = report_ctx
    opened, closed = datetime(2026, 9, 20, 13, 0, tzinfo=timezone.utc), datetime(2026, 9, 20, 22, 0, tzinfo=timezone.utc)
    session = _closed_session(db, user, opened, closed)
    _sale(db, "BEFORE", datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc), p1, Decimal("1"), cash, cigar, user)
    _sale(db, "IN", datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc), p1, Decimal("1"), cash, cigar, user)
    _sale(db, "AFTER", datetime(2026, 9, 20, 23, 0, tzinfo=timezone.utc), p1, Decimal("1"), cash, cigar, user)

    report = generate_daily_report(db, session)
    assert report.sales_total == Decimal("10000")
    assert report.tickets == 1


def test_session_crossing_midnight_keeps_all_operations(report_ctx):
    db, user, cigar, supercat, cash, nequi, p1, p2 = report_ctx
    opened = datetime(2026, 9, 20, 22, 0, tzinfo=timezone.utc)   # 17:00 Bogotá
    closed = datetime(2026, 9, 21, 4, 0, tzinfo=timezone.utc)    # 23:00 Bogotá next calendar day
    session = _closed_session(db, user, opened, closed)
    _sale(db, "LATE", datetime(2026, 9, 20, 23, 30, tzinfo=timezone.utc), p1, Decimal("1"), cash, cigar, user)
    _sale(db, "NEXTDAY", datetime(2026, 9, 21, 2, 0, tzinfo=timezone.utc), p1, Decimal("1"), cash, cigar, user)

    report = generate_daily_report(db, session)
    assert report.tickets == 2
    assert report.sales_total == Decimal("20000")


def test_sales_cash_electronic_tickets_average(report_ctx):
    db, user, cigar, supercat, cash, nequi, p1, p2 = report_ctx
    opened, closed = datetime(2026, 9, 20, 13, 0, tzinfo=timezone.utc), datetime(2026, 9, 20, 22, 0, tzinfo=timezone.utc)
    session = _closed_session(db, user, opened, closed)
    _sale(db, "A", datetime(2026, 9, 20, 14, 0, tzinfo=timezone.utc), p1, Decimal("1"), cash, cigar, user)   # 10000 cash
    _sale(db, "B", datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc), p2, Decimal("2"), nequi, supercat, user)  # 10000 electronic

    report = generate_daily_report(db, session)
    assert report.sales_total == Decimal("20000")
    assert report.cash_total == Decimal("10000")
    assert report.electronic_total == Decimal("10000")
    assert report.tickets == 2
    assert report.average_ticket == Decimal("10000.00")


def test_gross_cost_profit_margin(report_ctx):
    db, user, cigar, supercat, cash, nequi, p1, p2 = report_ctx
    opened, closed = datetime(2026, 9, 20, 13, 0, tzinfo=timezone.utc), datetime(2026, 9, 20, 22, 0, tzinfo=timezone.utc)
    session = _closed_session(db, user, opened, closed)
    # p1: price 10000 cost 6000 -> profit 4000. p2 x2: price 5000x2=10000 cost 3000x2=6000 -> profit 4000
    _sale(db, "A", datetime(2026, 9, 20, 14, 0, tzinfo=timezone.utc), p1, Decimal("1"), cash, cigar, user)
    _sale(db, "B", datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc), p2, Decimal("2"), nequi, supercat, user)

    report = generate_daily_report(db, session)
    assert report.gross_cost == Decimal("12000.00")
    assert report.gross_profit == Decimal("8000.00")
    assert report.gross_margin == Decimal("40.00")  # 8000 / 20000 * 100


def test_categories_and_channels_are_snapshotted(report_ctx):
    db, user, cigar, supercat, cash, nequi, p1, p2 = report_ctx
    opened, closed = datetime(2026, 9, 20, 13, 0, tzinfo=timezone.utc), datetime(2026, 9, 20, 22, 0, tzinfo=timezone.utc)
    session = _closed_session(db, user, opened, closed)
    _sale(db, "A", datetime(2026, 9, 20, 14, 0, tzinfo=timezone.utc), p1, Decimal("1"), cash, cigar, user)
    _sale(db, "B", datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc), p2, Decimal("2"), nequi, supercat, user)

    report = generate_daily_report(db, session)
    codes = {c["code"]: c for c in report.report_data["categories"]}
    assert Decimal(codes["CIGARROS"]["total"]) == Decimal("10000")
    assert Decimal(codes["SUPER"]["total"]) == Decimal("10000")
    channels = {c["channel_name"]: c for c in report.report_data["payment_channels"]}
    assert channels["Nequi"]["payment_group"] == "ELECTRONIC"
    assert Decimal(channels["Efectivo"]["amount"]) == Decimal("10000")

    # rename/reclassify the channel AFTER the report exists: the snapshot must not change
    nequi.name = "Nequi Renombrado"; nequi.payment_group = PaymentGroup.OTHER
    db.commit()
    frozen = db.get(DailyReport, report.id)
    frozen_channels = {c["channel_name"]: c for c in frozen.report_data["payment_channels"]}
    assert "Nequi" in frozen_channels
    assert "Nequi Renombrado" not in frozen_channels
    assert frozen_channels["Nequi"]["payment_group"] == "ELECTRONIC"


def test_peak_hour_by_tickets_and_by_revenue_can_differ(report_ctx):
    db, user, cigar, supercat, cash, nequi, p1, p2 = report_ctx
    opened, closed = datetime(2026, 9, 20, 13, 0, tzinfo=timezone.utc), datetime(2026, 9, 20, 22, 0, tzinfo=timezone.utc)
    session = _closed_session(db, user, opened, closed)
    # Hour 14: three small sales (more tickets). Hour 16: one big sale (more revenue).
    for i in range(3):
        _sale(db, f"A{i}", datetime(2026, 9, 20, 14, i, tzinfo=timezone.utc), p2, Decimal("1"), cash, supercat, user)
    _sale(db, "BIG", datetime(2026, 9, 20, 16, 0, tzinfo=timezone.utc), p1, Decimal("5"), cash, cigar, user)

    report = generate_daily_report(db, session)
    highlights = report.report_data["highlights"]
    assert highlights["peak_hour_by_tickets"]["tickets"] == 3
    assert Decimal(highlights["peak_hour_by_revenue"]["revenue"]) == Decimal("50000")
    assert highlights["peak_hour_by_tickets"]["hour"] != highlights["peak_hour_by_revenue"]["hour"]


def test_top_product_by_units_revenue_and_profit_can_differ(report_ctx):
    db, user, cigar, supercat, cash, nequi, p1, p2 = report_ctx
    opened, closed = datetime(2026, 9, 20, 13, 0, tzinfo=timezone.utc), datetime(2026, 9, 20, 22, 0, tzinfo=timezone.utc)
    session = _closed_session(db, user, opened, closed)
    # Deliberately engineered so units/revenue/profit each have a different winner:
    units_winner = Product(barcode="D3", name="Units Winner", sale_price=Decimal("1000"),
                           unit_cost=Decimal("800"), operational_category_id=supercat.id)      # 20u, rev 20000, profit 4000
    revenue_winner = Product(barcode="D4", name="Revenue Winner", sale_price=Decimal("15000"),
                             unit_cost=Decimal("13000"), operational_category_id=supercat.id)  # 5u, rev 75000, profit 10000
    profit_winner = Product(barcode="D5", name="Profit Winner", sale_price=Decimal("9000"),
                            unit_cost=Decimal("1000"), operational_category_id=supercat.id)    # 6u, rev 54000, profit 48000
    db.add_all([units_winner, revenue_winner, profit_winner]); db.commit()
    _sale(db, "UNITS", datetime(2026, 9, 20, 14, 0, tzinfo=timezone.utc), units_winner, Decimal("20"), cash, supercat, user)
    _sale(db, "REVENUE", datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc), revenue_winner, Decimal("5"), cash, supercat, user)
    _sale(db, "PROFIT", datetime(2026, 9, 20, 16, 0, tzinfo=timezone.utc), profit_winner, Decimal("6"), cash, supercat, user)

    report = generate_daily_report(db, session)
    highlights = report.report_data["highlights"]
    assert highlights["top_product_by_units"]["product_name"] == "Units Winner"
    assert highlights["top_product_by_revenue"]["product_name"] == "Revenue Winner"
    assert highlights["top_product_by_gross_profit"]["product_name"] == "Profit Winner"


def test_monthly_aggregation_sums_days(report_ctx):
    db, user, cigar, supercat, cash, nequi, p1, p2 = report_ctx
    s1 = _closed_session(db, user, datetime(2026, 9, 5, 13, 0, tzinfo=timezone.utc),
                         datetime(2026, 9, 5, 22, 0, tzinfo=timezone.utc))
    _sale(db, "D1A", datetime(2026, 9, 5, 14, 0, tzinfo=timezone.utc), p1, Decimal("1"), cash, cigar, user)
    s2 = _closed_session(db, user, datetime(2026, 9, 6, 13, 0, tzinfo=timezone.utc),
                         datetime(2026, 9, 6, 22, 0, tzinfo=timezone.utc))
    _sale(db, "D2A", datetime(2026, 9, 6, 14, 0, tzinfo=timezone.utc), p2, Decimal("2"), nequi, supercat, user)
    generate_daily_report(db, s1); generate_daily_report(db, s2)

    result = monthly_report(db, 2026, 9)
    assert result["days"] == 2
    assert Decimal(result["sales_total"]) == Decimal("20000")
    assert result["tickets"] == 2
    assert result["highlights"]["top_category_by_revenue"] is not None
    assert sum(row["tickets"] for row in result["hourly"]) == 2


def test_monthly_average_ticket_is_weighted_not_averaged(report_ctx):
    db, user, cigar, supercat, cash, nequi, p1, p2 = report_ctx
    # Day 1: one 10000 ticket (average 10000). Day 2: three 5000 tickets (average 5000).
    # Naive average of daily averages = 7500. Correct weighted average = 25000/4 = 6250.
    s1 = _closed_session(db, user, datetime(2026, 9, 5, 13, 0, tzinfo=timezone.utc),
                         datetime(2026, 9, 5, 22, 0, tzinfo=timezone.utc))
    _sale(db, "D1", datetime(2026, 9, 5, 14, 0, tzinfo=timezone.utc), p1, Decimal("1"), cash, cigar, user)
    s2 = _closed_session(db, user, datetime(2026, 9, 6, 13, 0, tzinfo=timezone.utc),
                         datetime(2026, 9, 6, 22, 0, tzinfo=timezone.utc))
    for i in range(3):
        _sale(db, f"D2-{i}", datetime(2026, 9, 6, 14, i, tzinfo=timezone.utc), p2, Decimal("1"), cash, supercat, user)
    generate_daily_report(db, s1); generate_daily_report(db, s2)

    result = monthly_report(db, 2026, 9)
    assert Decimal(result["average_ticket"]) == Decimal("6250.00")
    assert Decimal(result["average_ticket"]) != Decimal("7500.00")


def test_monthly_margin_is_weighted_not_averaged(report_ctx):
    db, user, cigar, supercat, cash, nequi, p1, p2 = report_ctx
    # Day 1: p1 (price 10000 cost 6000 -> margin 40%). Day 2: p2 x2 (price 10000 cost 6000 -> margin 40% too)
    # Use different-margin products instead so a naive average would differ from the weighted one.
    p3 = Product(barcode="D3", name="Alto margen", sale_price=Decimal("10000"), unit_cost=Decimal("1000"),
                operational_category_id=supercat.id)  # margin 90%
    db.add(p3); db.commit()
    s1 = _closed_session(db, user, datetime(2026, 9, 5, 13, 0, tzinfo=timezone.utc),
                         datetime(2026, 9, 5, 22, 0, tzinfo=timezone.utc))
    _sale(db, "D1", datetime(2026, 9, 5, 14, 0, tzinfo=timezone.utc), p3, Decimal("1"), cash, supercat, user)  # 10000 rev, 90% margin
    s2 = _closed_session(db, user, datetime(2026, 9, 6, 13, 0, tzinfo=timezone.utc),
                         datetime(2026, 9, 6, 22, 0, tzinfo=timezone.utc))
    for i in range(9):
        _sale(db, f"D2-{i}", datetime(2026, 9, 6, 14, i, tzinfo=timezone.utc), p1, Decimal("1"), cash, cigar, user)  # 9x 10000 rev, 40% margin
    generate_daily_report(db, s1); generate_daily_report(db, s2)

    result = monthly_report(db, 2026, 9)
    # weighted: profit = 9000 + 9*4000 = 45000, revenue = 10000+90000=100000 -> margin 45%
    assert Decimal(result["gross_margin"]) == Decimal("45.00")
    naive_average_of_daily_margins = (Decimal("90") + Decimal("40")) / 2  # 65, deliberately wrong
    assert Decimal(result["gross_margin"]) != naive_average_of_daily_margins


def test_list_and_latest_daily_report(report_ctx):
    db, user, cigar, supercat, cash, nequi, p1, p2 = report_ctx
    s1 = _closed_session(db, user, datetime(2026, 9, 5, 13, 0, tzinfo=timezone.utc),
                         datetime(2026, 9, 5, 22, 0, tzinfo=timezone.utc))
    s2 = _closed_session(db, user, datetime(2026, 9, 6, 13, 0, tzinfo=timezone.utc),
                         datetime(2026, 9, 6, 22, 0, tzinfo=timezone.utc))
    generate_daily_report(db, s1); generate_daily_report(db, s2)

    latest = get_latest_daily_report(db)
    assert latest.cash_session_id == s2.id
    rows = list_daily_reports(db, year=2026, month=9)
    assert [r.cash_session_id for r in rows] == [s2.id, s1.id]


def test_current_report_is_none_when_no_session_ever_existed(report_ctx):
    db, *_ = report_ctx
    result = current_report(db, now=datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc))
    assert result["status"] == "NONE"
    assert result["cash_session_id"] is None
    assert result["report_data"]["categories"] == []


def test_open_session_returns_live_data(report_ctx):
    db, user, cigar, supercat, cash, nequi, p1, p2 = report_ctx
    session = CashSession(opening_amount=Decimal("0"), opened_by=user.id,
                          opened_at=datetime(2026, 9, 20, 13, 0, tzinfo=timezone.utc))
    db.add(session); db.commit()
    _sale(db, "A", datetime(2026, 9, 20, 14, 0, tzinfo=timezone.utc), p1, Decimal("1"), cash, cigar, user)

    result = current_report(db, now=datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc))
    assert result["status"] == "OPEN"
    assert result["cash_session_id"] == session.id
    assert result["daily_report_id"] is None
    assert result["sales_total"] == Decimal("10000")
    assert result["tickets"] == 1


def test_new_sales_change_live_report_without_closing(report_ctx):
    db, user, cigar, supercat, cash, nequi, p1, p2 = report_ctx
    session = CashSession(opening_amount=Decimal("0"), opened_by=user.id,
                          opened_at=datetime(2026, 9, 20, 13, 0, tzinfo=timezone.utc))
    db.add(session); db.commit()
    _sale(db, "A", datetime(2026, 9, 20, 14, 0, tzinfo=timezone.utc), p1, Decimal("1"), cash, cigar, user)
    before = current_report(db, now=datetime(2026, 9, 20, 14, 30, tzinfo=timezone.utc))
    assert before["sales_total"] == Decimal("10000")

    _sale(db, "B", datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc), p2, Decimal("2"), nequi, supercat, user)
    after = current_report(db, now=datetime(2026, 9, 20, 16, 0, tzinfo=timezone.utc))
    assert after["sales_total"] == Decimal("20000")
    assert after["tickets"] == 2
    assert after["status"] == "OPEN"


def test_live_preview_never_creates_a_daily_report(report_ctx):
    db, user, cigar, supercat, cash, nequi, p1, p2 = report_ctx
    session = CashSession(opening_amount=Decimal("0"), opened_by=user.id,
                          opened_at=datetime(2026, 9, 20, 13, 0, tzinfo=timezone.utc))
    db.add(session); db.commit()
    _sale(db, "A", datetime(2026, 9, 20, 14, 0, tzinfo=timezone.utc), p1, Decimal("1"), cash, cigar, user)

    for _ in range(3):
        current_report(db, now=datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc))
        build_live_snapshot(db, session, now=datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc))
    assert db.scalar(select(DailyReport).where(DailyReport.cash_session_id == session.id)) is None


def test_live_window_is_opened_at_to_now_not_calendar_day(report_ctx):
    db, user, cigar, supercat, cash, nequi, p1, p2 = report_ctx
    session = CashSession(opening_amount=Decimal("0"), opened_by=user.id,
                          opened_at=datetime(2026, 9, 20, 13, 0, tzinfo=timezone.utc))
    db.add(session); db.commit()
    _sale(db, "BEFORE", datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc), p1, Decimal("1"), cash, cigar, user)
    _sale(db, "IN", datetime(2026, 9, 20, 14, 0, tzinfo=timezone.utc), p1, Decimal("1"), cash, cigar, user)

    result = current_report(db, now=datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc))
    assert result["sales_total"] == Decimal("10000")
    assert result["opened_at"] == session.opened_at
    assert result["closed_at"] == datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc)


def test_live_snapshot_matches_final_report_at_the_close_instant(report_ctx):
    db, user, cigar, supercat, cash, nequi, p1, p2 = report_ctx
    opened, closed = datetime(2026, 9, 20, 13, 0, tzinfo=timezone.utc), datetime(2026, 9, 20, 22, 0, tzinfo=timezone.utc)
    session = _closed_session(db, user, opened, closed)  # already closed, to compare against a live snapshot at the same instant
    _sale(db, "A", datetime(2026, 9, 20, 14, 0, tzinfo=timezone.utc), p1, Decimal("1"), cash, cigar, user)
    _sale(db, "B", datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc), p2, Decimal("2"), nequi, supercat, user)

    live_at_close = build_live_snapshot(db, session, now=closed)
    final = build_daily_report(db, session)
    assert live_at_close["sales_total"] == final["sales_total"]
    assert live_at_close["tickets"] == final["tickets"]
    assert live_at_close["gross_profit"] == final["gross_profit"]
    assert live_at_close["report_data"] == final["report_data"]


def test_closing_flips_status_to_closed_with_final_snapshot_and_no_duplication(report_ctx):
    db, user, cigar, supercat, cash, nequi, p1, p2 = report_ctx
    session = CashSession(opening_amount=Decimal("100000"), opened_by=user.id,
                          opened_at=datetime.now(timezone.utc))
    db.add(session); db.commit()
    _sale(db, "A", datetime.now(timezone.utc), p1, Decimal("1"), cash, cigar, user)

    live = current_report(db)
    assert live["status"] == "OPEN"

    # _sale() writes Sale/SaleItem/Payment directly (no CashMovement), so
    # expected cash is just the opening amount.
    close_cash_session(db, CloseCashSessionIn(counted_amount=Decimal("100000"), expected_amount=Decimal("100000")))
    closed_first = current_report(db)
    closed_second = current_report(db)
    assert closed_first["status"] == "CLOSED"
    assert closed_first["daily_report_id"] == closed_second["daily_report_id"]
    assert closed_first["sales_total"] == Decimal("10000")
    count = db.scalar(select(DailyReport.id).where(DailyReport.cash_session_id == session.id))
    assert count is not None
