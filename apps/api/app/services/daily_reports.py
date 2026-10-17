"""Daily report: build, save, get, and monthly aggregation.

The report is a frozen snapshot of a closed CashSession. It reuses the
existing category/channel proportional-allocation logic (daily_summary) and
the existing hour/ticket metrics (reporting._metrics/_time_expr) instead of
recomputing them — this module only adds what didn't exist yet: gross
cost/profit/margin, per-product cost, and top-level channel/hour breakdowns.

Kept deliberately in three layers so a future delivery channel (WhatsApp,
email, ...) can be added without touching KPI calculation:
  build_daily_report()  -> pure calculation, returns a dict
  save_daily_report()   -> persists it (idempotent per cash_session_id)
  get_daily_report(...)  / list_daily_reports() / monthly_report() -> reads
A future render_daily_report_text(data) belongs here later, untouched by
today's implementation.
"""
import logging
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.services.effective_payments import effective_channel_id_column
from app.domain.enums import CashMovementType, PurchaseStatus, SaleStatus
from app.models import (CashMovement, CashSession, DailyReport, Payment,
                        PaymentChannel, Sale, SaleItem, SupplierPayment)
from app.services.operations import daily_summary
from app.services.post_sale import completed_refunds_by_channel
from app.services.reporting import _metrics, _time_expr

ZERO = Decimal("0")
logger = logging.getLogger(__name__)
NET_SALES_BASIS = "NET_COMPLETED_REFUNDS_V1"


def _decimal(value) -> Decimal:
    return Decimal(value) if value is not None else ZERO


def _channel_breakdown(db: Session, start: datetime, end: datetime) -> list[dict]:
    rows = db.execute(select(
        PaymentChannel.id, PaymentChannel.name, PaymentChannel.payment_type,
        PaymentChannel.payment_group, func.sum(Payment.amount), func.count(Payment.id))
        .join(Payment, effective_channel_id_column() == PaymentChannel.id)
        .join(Sale, Sale.id == Payment.sale_id)
        .where(Sale.status == SaleStatus.COMPLETED, Sale.completed_at >= start, Sale.completed_at <= end)
        .group_by(PaymentChannel.id).order_by(PaymentChannel.name)).all()
    refunded = completed_refunds_by_channel(db, start, end)
    return [{"channel_id": str(channel_id), "channel_name": name, "payment_type": payment_type.value,
             "payment_group": payment_group.value,
             "amount": str(_decimal(amount) - refunded.get(channel_id, ZERO)), "transaction_count": count}
            for channel_id, name, payment_type, payment_group, amount, count in rows]


def _product_breakdown(db: Session, start: datetime, end: datetime) -> list[dict]:
    rows = db.execute(select(
        SaleItem.product_id, SaleItem.product_name, func.sum(SaleItem.quantity),
        func.sum(SaleItem.line_total), func.sum(SaleItem.unit_cost * SaleItem.quantity))
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(Sale.status == SaleStatus.COMPLETED, Sale.completed_at >= start, Sale.completed_at <= end,
               SaleItem.product_id.is_not(None))
        .group_by(SaleItem.product_id, SaleItem.product_name).order_by(SaleItem.product_name)).all()
    result = []
    for product_id, name, units, revenue, cost in rows:
        revenue, cost = _decimal(revenue), _decimal(cost)
        result.append({"product_id": str(product_id), "product_name": name, "units": str(_decimal(units)),
                       "revenue": str(revenue), "cost": str(cost), "gross_profit": str(revenue - cost)})
    return result


def _hourly_breakdown(db: Session, start: datetime, end: datetime) -> list[dict]:
    key = _time_expr(db, "hour")
    rows = db.execute(select(key, func.count(Sale.id), func.sum(Sale.total))
        .where(Sale.status == SaleStatus.COMPLETED, Sale.completed_at >= start, Sale.completed_at <= end)
        .group_by(key).order_by(key)).all()
    return [{"hour": str(hour), "tickets": int(tickets), "revenue": str(_decimal(revenue))}
            for hour, tickets, revenue in rows]


def _highlights(categories: list[dict], channels: list[dict], products: list[dict], hourly: list[dict]) -> dict:
    def best(items, key):
        return max(items, key=lambda row: Decimal(row[key])) if items else None

    top_channel_revenue = best(channels, "amount")
    top_channel_tx = max(channels, key=lambda row: row["transaction_count"]) if channels else None
    peak_hour_tickets = max(hourly, key=lambda row: row["tickets"]) if hourly else None
    peak_hour_revenue = best(hourly, "revenue")
    top_product_units = best(products, "units")
    top_product_revenue = best(products, "revenue")
    top_product_profit = best(products, "gross_profit")
    top_category = best(categories, "total")
    return {
        "payment_channel_by_revenue": {"channel_id": top_channel_revenue["channel_id"],
            "channel_name": top_channel_revenue["channel_name"], "amount": top_channel_revenue["amount"]}
            if top_channel_revenue else None,
        "payment_channel_by_transactions": {"channel_id": top_channel_tx["channel_id"],
            "channel_name": top_channel_tx["channel_name"], "transaction_count": top_channel_tx["transaction_count"]}
            if top_channel_tx else None,
        "peak_hour_by_tickets": {"hour": peak_hour_tickets["hour"], "tickets": peak_hour_tickets["tickets"]}
            if peak_hour_tickets else None,
        "peak_hour_by_revenue": {"hour": peak_hour_revenue["hour"], "revenue": peak_hour_revenue["revenue"]}
            if peak_hour_revenue else None,
        "top_product_by_units": {"product_id": top_product_units["product_id"],
            "product_name": top_product_units["product_name"], "units": top_product_units["units"]}
            if top_product_units else None,
        "top_product_by_revenue": {"product_id": top_product_revenue["product_id"],
            "product_name": top_product_revenue["product_name"], "revenue": top_product_revenue["revenue"]}
            if top_product_revenue else None,
        "top_product_by_gross_profit": {"product_id": top_product_profit["product_id"],
            "product_name": top_product_profit["product_name"], "gross_profit": top_product_profit["gross_profit"]}
            if top_product_profit else None,
        "top_category_by_revenue": {"code": top_category["code"], "name": top_category["name"],
            "total": top_category["total"]} if top_category else None,
    }


def _calculate_window(db: Session, start: datetime, end: datetime) -> dict:
    """Pure calculation over an arbitrary [start, end]. Shared by the persisted
    DailyReport (build_daily_report, window = opened_at..closed_at) and the
    unpersisted live preview (build_live_snapshot, window = opened_at..now) —
    same categories/channels/rentabilidad/products/hourly/highlights logic
    either way, so the two never drift apart."""
    summary = daily_summary(db, start, end, session_status="CUSTOM")
    categories = [{"code": row.category_code, "name": row.category_name, "total": str(row.total),
                   "cash": str(row.cash), "electronic": str(row.electronic),
                   "channels": [{"channel_id": str(ch.channel_id), "channel_name": ch.channel_name,
                                "payment_group": ch.payment_group.value, "amount": str(ch.amount)}
                               for ch in row.channels]}
                  for row in summary.categories]

    sales_total, tickets, average_ticket, _units = _metrics(db, start, end)
    channels = _channel_breakdown(db, start, end)
    cash_total = sum((Decimal(c["amount"]) for c in channels if c["payment_group"] == "CASH"), ZERO)
    electronic_total = sum((Decimal(c["amount"]) for c in channels if c["payment_group"] == "ELECTRONIC"), ZERO)
    net_sales = sales_total - summary.returns.refunded_total

    line_cost = db.scalar(select(func.coalesce(func.sum(SaleItem.unit_cost * SaleItem.quantity), 0))
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(Sale.status == SaleStatus.COMPLETED, Sale.completed_at >= start, Sale.completed_at <= end))
    gross_cost = _decimal(line_cost)
    gross_profit = sales_total - gross_cost
    gross_margin = (gross_profit / sales_total * 100) if sales_total > 0 else ZERO

    products = _product_breakdown(db, start, end)
    hourly = _hourly_breakdown(db, start, end)

    deposits = _decimal(db.scalar(select(func.coalesce(func.sum(CashMovement.amount), 0)).where(
        CashMovement.movement_type == CashMovementType.DEPOSIT,
        CashMovement.created_at >= start, CashMovement.created_at <= end)))
    supplier_payments = _decimal(db.scalar(select(func.coalesce(func.sum(SupplierPayment.amount), 0)).where(
        SupplierPayment.paid_at >= start, SupplierPayment.paid_at <= end)))
    internal_consumption = summary.outgoing.internal_consumption_cost + summary.outgoing.internal_consumption_estimated

    operations = {
        "expenses": str(summary.outgoing.expenses_total),
        "supplier_payments": str(supplier_payments),
        "withdrawals_home": str(summary.outgoing.home_withdrawals),
        "withdrawals_other": str(summary.outgoing.other_withdrawals),
        "internal_consumption": str(internal_consumption),
        "deposits": str(deposits),
    }

    return {
        "sales_total": net_sales, "cash_total": cash_total, "electronic_total": electronic_total,
        "tickets": tickets, "average_ticket": average_ticket,
        "gross_cost": gross_cost, "gross_profit": gross_profit,
        "gross_margin": gross_margin.quantize(Decimal("0.01")),
        "report_data": {
            # DailyReport is immutable. This marker distinguishes snapshots
            # generated after net-sales accounting from historical snapshots
            # whose persisted sales_total is gross.
            "sales_basis": NET_SALES_BASIS,
            "categories": categories, "payment_channels": channels, "products": products,
            "hourly": hourly, "operations": operations,
            "highlights": _highlights(categories, channels, products, hourly),
            "returns": {key: (str(value) if isinstance(value, Decimal) else value)
                        for key, value in summary.returns.model_dump(exclude={"by_category"}).items()},
            "exchanges": {key: (str(value) if isinstance(value, Decimal) else value)
                          for key, value in summary.exchanges.model_dump().items()},
        },
    }


def build_daily_report(db: Session, session: CashSession) -> dict:
    """Pure calculation over [session.opened_at, session.closed_at]. Returns a
    plain dict ready for save_daily_report(); does not touch the database."""
    if session.closed_at is None:
        raise ValueError("Solo se puede generar el informe de una jornada cerrada")
    metrics = _calculate_window(db, session.opened_at, session.closed_at)
    return {
        "cash_session_id": session.id, **metrics,
        "closing_expected": session.closing_expected or ZERO,
        "closing_actual": session.closing_actual or ZERO,
        "closing_difference": session.difference or ZERO,
    }


def build_live_snapshot(db: Session, session: CashSession, now: datetime | None = None) -> dict:
    """Preview for an OPEN session, window = [opened_at, now]. Never persisted —
    same _calculate_window() as build_daily_report, so live and final numbers
    can never disagree on methodology, only on the instant they were taken."""
    now = now or datetime.now(timezone.utc)
    metrics = _calculate_window(db, session.opened_at, now)
    return {"cash_session_id": session.id, "opened_at": session.opened_at, "closed_at": now, **metrics}


def save_daily_report(db: Session, data: dict) -> DailyReport:
    """Idempotent per cash_session_id: if one already exists, return it untouched."""
    existing = db.scalar(select(DailyReport).where(DailyReport.cash_session_id == data["cash_session_id"]))
    if existing:
        return existing
    report = DailyReport(**data)
    try:
        db.add(report); db.commit(); db.refresh(report)
        return report
    except IntegrityError:
        db.rollback()
        return db.scalar(select(DailyReport).where(DailyReport.cash_session_id == data["cash_session_id"]))


def generate_daily_report(db: Session, session: CashSession) -> DailyReport:
    existing = db.scalar(select(DailyReport).where(DailyReport.cash_session_id == session.id))
    if existing:
        return existing
    return save_daily_report(db, build_daily_report(db, session))


def generate_report_after_close(db: Session, session: CashSession) -> None:
    """Called right after a successful close. Never raises: the financial
    close has already been committed and must not be affected by this."""
    try:
        generate_daily_report(db, session)
    except Exception:
        db.rollback()
        logger.exception("No fue posible generar el DailyReport de la sesión %s", session.id)


def get_daily_report(db: Session, report_id: UUID) -> DailyReport | None:
    return db.get(DailyReport, report_id)


def get_latest_daily_report(db: Session) -> DailyReport | None:
    return db.scalar(select(DailyReport).join(CashSession)
        .order_by(CashSession.opened_at.desc()).limit(1))


_EMPTY_REPORT_DATA = {"categories": [], "payment_channels": [], "products": [], "hourly": [],
                      "operations": {"expenses": "0", "supplier_payments": "0", "withdrawals_home": "0",
                                    "withdrawals_other": "0", "internal_consumption": "0", "deposits": "0"},
                      "highlights": _highlights([], [], [], [])}


def current_report(db: Session, now: datetime | None = None) -> dict:
    """GET /reports/current: OPEN session -> live, never-persisted preview.
    No open session -> the last DailyReport (CLOSED). Neither ever existed -> NONE."""
    open_session = db.scalar(select(CashSession).where(CashSession.closed_at.is_(None)))
    if open_session:
        snapshot = build_live_snapshot(db, open_session, now)
        return {"status": "OPEN", "cash_session_id": open_session.id, "daily_report_id": None,
                "generated_at": None, "closing_expected": None, "closing_actual": None,
                "closing_difference": None, **snapshot}

    report = get_latest_daily_report(db)
    if report:
        return {"status": "CLOSED", "cash_session_id": report.cash_session_id, "daily_report_id": report.id,
                "generated_at": report.generated_at, "opened_at": report.session.opened_at,
                "closed_at": report.session.closed_at, "sales_total": report.sales_total,
                "cash_total": report.cash_total, "electronic_total": report.electronic_total,
                "tickets": report.tickets, "average_ticket": report.average_ticket,
                "gross_cost": report.gross_cost, "gross_profit": report.gross_profit,
                "gross_margin": report.gross_margin, "closing_expected": report.closing_expected,
                "closing_actual": report.closing_actual, "closing_difference": report.closing_difference,
                "report_data": report.report_data}

    return {"status": "NONE", "cash_session_id": None, "daily_report_id": None, "generated_at": None,
            "opened_at": None, "closed_at": None, "sales_total": ZERO, "cash_total": ZERO,
            "electronic_total": ZERO, "tickets": 0, "average_ticket": ZERO, "gross_cost": ZERO,
            "gross_profit": ZERO, "gross_margin": ZERO, "closing_expected": None, "closing_actual": None,
            "closing_difference": None, "report_data": _EMPTY_REPORT_DATA}


def list_daily_reports(db: Session, year: int | None = None, month: int | None = None,
                       limit: int = 31, offset: int = 0) -> list[DailyReport]:
    stmt = select(DailyReport).join(CashSession)
    if year:
        stmt = stmt.where(func.extract("year", CashSession.opened_at) == year)
    if month:
        stmt = stmt.where(func.extract("month", CashSession.opened_at) == month)
    return db.scalars(stmt.order_by(CashSession.opened_at.desc()).limit(limit).offset(offset)).all()


def _merge_sum(rows: list[dict], key_field: str, amount_fields: list[str]) -> dict[str, dict]:
    merged: dict[str, dict] = {}
    for row in rows:
        key = row[key_field]
        bucket = merged.setdefault(key, dict(row))
        if bucket is not row:
            for field in amount_fields:
                bucket[field] = str(Decimal(bucket[field]) + Decimal(row[field]))
    return merged


def monthly_report(db: Session, year: int, month: int) -> dict:
    reports = list_daily_reports(db, year=year, month=month, limit=366, offset=0)
    if not reports:
        return {"year": year, "month": month, "days": 0, "sales_total": "0", "tickets": 0,
                "average_ticket": "0", "cash_total": "0", "electronic_total": "0",
                "gross_cost": "0", "gross_profit": "0", "gross_margin": "0",
                "categories": [], "payment_channels": [], "products": [], "hourly": [],
                "highlights": _highlights([], [], [], []), "days_breakdown": []}

    sales_total = sum((r.sales_total for r in reports), ZERO)
    tickets = sum((r.tickets for r in reports), 0)
    cash_total = sum((r.cash_total for r in reports), ZERO)
    electronic_total = sum((r.electronic_total for r in reports), ZERO)
    gross_cost = sum((r.gross_cost for r in reports), ZERO)
    gross_profit = sum((r.gross_profit for r in reports), ZERO)
    # Ticket average and margin: computed from the summed totals, never averaged day-by-day.
    average_ticket = (sales_total / tickets).quantize(Decimal("0.01")) if tickets else ZERO
    gross_margin = (gross_profit / sales_total * 100).quantize(Decimal("0.01")) if sales_total else ZERO

    category_rows, channel_rows, product_rows, hourly_rows = [], [], [], []
    for report in reports:
        category_rows.extend(report.report_data.get("categories", []))
        channel_rows.extend(report.report_data.get("payment_channels", []))
        product_rows.extend(report.report_data.get("products", []))
        # Merge by hour-of-day (not the full "YYYY-MM-DD HH" key), so "horas
        # fuertes" reflects the month's pattern, not one entry per calendar hour.
        for row in report.report_data.get("hourly", []):
            hour_of_day = row["hour"].split(" ")[-1]
            hourly_rows.append({"hour": hour_of_day, "tickets": row["tickets"], "revenue": row["revenue"]})

    categories = defaultdict(lambda: {"cash": ZERO, "electronic": ZERO, "total": ZERO})
    names = {}
    for row in category_rows:
        names[row["code"]] = row["name"]
        bucket = categories[row["code"]]
        bucket["cash"] += Decimal(row["cash"]); bucket["electronic"] += Decimal(row["electronic"])
        bucket["total"] += Decimal(row["total"])
    categories_out = sorted([{"code": code, "name": names[code], "cash": str(v["cash"]),
                              "electronic": str(v["electronic"]), "total": str(v["total"])}
                             for code, v in categories.items()], key=lambda r: Decimal(r["total"]), reverse=True)

    channels = defaultdict(lambda: {"amount": ZERO, "transaction_count": 0})
    channel_names = {}
    for row in channel_rows:
        channel_names[row["channel_id"]] = row["channel_name"]
        bucket = channels[row["channel_id"]]
        bucket["amount"] += Decimal(row["amount"]); bucket["transaction_count"] += row["transaction_count"]
    channels_out = sorted([{"channel_id": cid, "channel_name": channel_names[cid], "amount": str(v["amount"]),
                            "transaction_count": v["transaction_count"]} for cid, v in channels.items()],
                          key=lambda r: Decimal(r["amount"]), reverse=True)

    products = defaultdict(lambda: {"units": ZERO, "revenue": ZERO, "cost": ZERO, "gross_profit": ZERO})
    product_names = {}
    for row in product_rows:
        product_names[row["product_id"]] = row["product_name"]
        bucket = products[row["product_id"]]
        bucket["units"] += Decimal(row["units"]); bucket["revenue"] += Decimal(row["revenue"])
        bucket["cost"] += Decimal(row["cost"]); bucket["gross_profit"] += Decimal(row["gross_profit"])
    products_out = sorted([{"product_id": pid, "product_name": product_names[pid], "units": str(v["units"]),
                            "revenue": str(v["revenue"]), "cost": str(v["cost"]),
                            "gross_profit": str(v["gross_profit"])} for pid, v in products.items()],
                          key=lambda r: Decimal(r["revenue"]), reverse=True)

    hourly = defaultdict(lambda: {"tickets": 0, "revenue": ZERO})
    for row in hourly_rows:
        bucket = hourly[row["hour"]]
        bucket["tickets"] += row["tickets"]; bucket["revenue"] += Decimal(row["revenue"])
    hourly_out = sorted([{"hour": hour, "tickets": v["tickets"], "revenue": str(v["revenue"])}
                         for hour, v in hourly.items()], key=lambda r: r["hour"])

    days_breakdown = [{"cash_session_id": str(r.cash_session_id), "daily_report_id": str(r.id),
                       "date": r.session.opened_at.date().isoformat(), "sales_total": str(r.sales_total),
                       "tickets": r.tickets} for r in reversed(reports)]

    return {"year": year, "month": month, "days": len(reports), "sales_total": str(sales_total),
            "tickets": tickets, "average_ticket": str(average_ticket), "cash_total": str(cash_total),
            "electronic_total": str(electronic_total), "gross_cost": str(gross_cost),
            "gross_profit": str(gross_profit), "gross_margin": str(gross_margin),
            "categories": categories_out, "payment_channels": channels_out, "products": products_out,
            "hourly": hourly_out, "highlights": _highlights(categories_out, channels_out, products_out, hourly_out),
            "days_breakdown": days_breakdown}
