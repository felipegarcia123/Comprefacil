"""Read-only, source-adaptable indicators for business intelligence tools.

The POS adapter delegates financial and inventory rules to existing services.
It does not open its own connection, commit, or call external providers.
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import PurchaseStatus, SaleStatus
from app.models import (InventoryMovement, Product, Purchase, Sale, SaleExchange,
                        SaleItem, SaleReturn, SaleReturnLine)
from app.schemas.business_analytics import (AgendaItem, GrossProfit, InventorySummary,
    LowStock, ObligationDay, Period, ProductSales, PurchaseAgenda, SalesSummary,
    StockItem, SupplierObligations, TopProducts)
from app.services.inventory import stock_subquery
from app.services.inventory_valuation import compute_valuation
from app.services.post_sale import returns_summary
from app.services.pricing import gross_margin_percent
from app.services.purchases import purchase_agenda, purchase_commitments
from app.services.reporting import _metrics


ZONE = ZoneInfo("America/Bogota")
SOURCE = "comprefacil_pos"
ZERO = Decimal("0")


class BusinessAnalyticsSource(Protocol):
    """Contract an agent tool can use without knowing the underlying POS."""

    def sales_summary(self, date_from: date, date_to: date) -> SalesSummary: ...
    def gross_profit(self, date_from: date, date_to: date) -> GrossProfit: ...
    def top_products(self, date_from: date, date_to: date, limit: int = 10) -> TopProducts: ...
    def low_stock(self, limit: int = 50) -> LowStock: ...
    def inventory_summary(self) -> InventorySummary: ...
    def supplier_obligations(self, date_from: date, date_to: date) -> SupplierObligations: ...
    def purchase_agenda(self, as_of: date, horizon_days: int = 30) -> PurchaseAgenda: ...


def _period(date_from: date, date_to: date) -> tuple[Period, datetime, datetime]:
    if date_to < date_from or (date_to - date_from).days > 366:
        raise ValueError("El periodo debe estar ordenado y no superar 366 dias")
    start = datetime.combine(date_from, time.min, ZONE).astimezone(timezone.utc)
    end_exclusive = datetime.combine(date_to + timedelta(days=1), time.min, ZONE).astimezone(timezone.utc)
    # Existing read models use inclusive end timestamps.
    return Period(date_from=date_from, date_to=date_to), start, end_exclusive - timedelta(microseconds=1)


class PosBusinessAnalytics:
    def __init__(self, db: Session):
        self.db = db

    def sales_summary(self, date_from: date, date_to: date) -> SalesSummary:
        period, start, end = _period(date_from, date_to)
        gross, tickets, _, _ = _metrics(self.db, start, end)
        returns = returns_summary(self.db, start, end)
        return SalesSummary(
            source=SOURCE, period=period,
            state="available" if tickets or returns["return_count"] else "no_data",
            completed_sales=tickets, gross_sales=gross,
            returns_registered=returns["returns_total"],
            refunds_completed=returns["refunded_total"],
            refunds_pending=returns["refunds_electronic_pending"],
            net_sales=gross - returns["refunded_total"],
            return_count=returns["return_count"], void_count=returns["void_count"],
        )

    def gross_profit(self, date_from: date, date_to: date) -> GrossProfit:
        period, start, end = _period(date_from, date_to)
        sales = self.db.scalars(select(Sale).where(
            Sale.status == SaleStatus.COMPLETED,
            Sale.completed_at >= start, Sale.completed_at <= end)).unique().all()
        returns_count = self.db.scalar(select(func.count(SaleReturn.id)).where(
            SaleReturn.created_at >= start, SaleReturn.created_at <= end)) or 0
        exchanges_count = self.db.scalar(select(func.count(SaleExchange.id)).where(
            SaleExchange.created_at >= start, SaleExchange.created_at <= end)) or 0
        revenue = sum((Decimal(sale.total) for sale in sales), ZERO)
        unreliable = 0
        cost = ZERO
        for sale in sales:
            if (not sale.items or any(item.product_id is None or item.unit_cost <= 0 for item in sale.items)
                    or sum((Decimal(item.line_total) for item in sale.items), ZERO) != sale.total):
                unreliable += 1
            else:
                cost += sum((Decimal(item.quantity) * item.unit_cost for item in sale.items), ZERO)
        reasons = []
        if returns_count:
            reasons.append("returns_in_period")
        if exchanges_count:
            reasons.append("exchanges_in_period")
        if unreliable:
            reasons.append("missing_or_unreliable_sale_cost")
        if not sales:
            state = "insufficient_data" if reasons else "no_data"
        else:
            state = "insufficient_data" if reasons or revenue <= 0 else "available"
            if revenue <= 0:
                reasons.append("non_positive_revenue")
        reliable = state == "available"
        return GrossProfit(
            source=SOURCE, period=period, state=state, gross_revenue=revenue,
            cost_of_goods=cost if reliable else None,
            gross_profit=revenue - cost if reliable else None,
            gross_margin_percent=gross_margin_percent(revenue, cost) if reliable else None,
            sales_with_unreliable_cost=unreliable, reasons=reasons,
        )

    def top_products(self, date_from: date, date_to: date, limit: int = 10) -> TopProducts:
        period, start, end = _period(date_from, date_to)
        if not 1 <= limit <= 100:
            raise ValueError("El limite debe estar entre 1 y 100")
        sold = self.db.execute(select(
            SaleItem.product_id, Product.name, func.sum(SaleItem.quantity), func.sum(SaleItem.line_total))
            .join(Sale, Sale.id == SaleItem.sale_id)
            .join(Product, Product.id == SaleItem.product_id)
            .where(Sale.status == SaleStatus.COMPLETED,
                   Sale.completed_at >= start, Sale.completed_at <= end)
            .group_by(SaleItem.product_id, Product.name)).all()
        returned = self.db.execute(select(
            SaleItem.product_id, Product.name, func.sum(SaleReturnLine.quantity), func.sum(SaleReturnLine.amount))
            .join(SaleReturn, SaleReturn.id == SaleReturnLine.return_id)
            .join(SaleItem, SaleItem.id == SaleReturnLine.sale_item_id)
            .join(Product, Product.id == SaleItem.product_id)
            .where(SaleReturn.created_at >= start, SaleReturn.created_at <= end,
                   SaleItem.product_id.is_not(None))
            .group_by(SaleItem.product_id, Product.name)).all()
        manual = self.db.scalar(select(func.count(SaleItem.id)).join(Sale, Sale.id == SaleItem.sale_id)
            .where(Sale.status == SaleStatus.COMPLETED, Sale.completed_at >= start,
                   Sale.completed_at <= end, SaleItem.product_id.is_(None))) or 0
        replacement_returns = self.db.scalar(select(func.count(SaleReturnLine.id))
            .join(SaleReturn, SaleReturn.id == SaleReturnLine.return_id)
            .where(SaleReturn.created_at >= start, SaleReturn.created_at <= end,
                   SaleReturnLine.source_replacement_line_id.is_not(None))) or 0
        rows: dict[str, ProductSales] = {}
        for product_id, name, units, amount in sold:
            key = str(product_id)
            rows[key] = ProductSales(product_id=key, name=name, gross_units=Decimal(units),
                returned_units=ZERO, net_units=Decimal(units), gross_sales=Decimal(amount),
                returned_sales=ZERO, net_sales=Decimal(amount))
        for product_id, name, units, amount in returned:
            key = str(product_id)
            row = rows.setdefault(key, ProductSales(product_id=key, name=name, gross_units=ZERO,
                returned_units=ZERO, net_units=ZERO, gross_sales=ZERO, returned_sales=ZERO, net_sales=ZERO))
            row.returned_units += Decimal(units)
            row.returned_sales += Decimal(amount)
            row.net_units = row.gross_units - row.returned_units
            row.net_sales = row.gross_sales - row.returned_sales
        items = sorted(rows.values(), key=lambda row: (-row.net_units, row.name, row.product_id))
        reasons = (["replacement_line_returns_not_attributed"] if replacement_returns else [])
        if manual:
            reasons.append("manual_lines_excluded")
        return TopProducts(source=SOURCE, period=period,
            state="insufficient_data" if replacement_returns else ("available" if items else "no_data"),
            items=items[:limit], total_products=len(items), excluded_manual_lines=manual, reasons=reasons)

    def low_stock(self, limit: int = 50) -> LowStock:
        if not 1 <= limit <= 100:
            raise ValueError("El limite debe estar entre 1 y 100")
        stocks = stock_subquery()
        condition = (Product.active.is_(True) & (stocks.c.current_stock > 0)
                     & (stocks.c.current_stock <= Product.minimum_stock))
        base = select(Product.id, Product.name, stocks.c.current_stock, Product.minimum_stock).join(
            stocks, stocks.c.product_id == Product.id).where(condition)
        total = self.db.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = self.db.execute(base.order_by(stocks.c.current_stock, Product.name, Product.id).limit(limit)).all()
        untracked = self.db.scalar(select(func.count(Product.id)).outerjoin(
            stocks, stocks.c.product_id == Product.id).where(
            Product.active.is_(True), stocks.c.product_id.is_(None))) or 0
        return LowStock(source=SOURCE, state="available" if total else ("insufficient_data" if untracked else "no_data"),
            items=[StockItem(product_id=str(pid), name=name, stock=Decimal(balance), minimum_stock=minimum)
                   for pid, name, balance, minimum in rows],
            total=total, truncated=total > len(rows), untracked_active_products=untracked)

    def inventory_summary(self) -> InventorySummary:
        stocks = stock_subquery()
        row = self.db.execute(select(
            func.count(Product.id),
            func.count(stocks.c.product_id),
            func.count(Product.id).filter(stocks.c.current_stock > 0),
            func.count(Product.id).filter(stocks.c.current_stock == 0),
            func.count(Product.id).filter((stocks.c.current_stock > 0) &
                                          (stocks.c.current_stock <= Product.minimum_stock)),
            func.count(Product.id).filter(stocks.c.current_stock < 0))
            .select_from(Product).outerjoin(stocks, stocks.c.product_id == Product.id)
            .where(Product.active.is_(True))).one()
        active, tracked, positive, out, low, negative = (int(value) for value in row)
        valuation = compute_valuation(self.db)
        return InventorySummary(source=SOURCE,
            state="no_data" if not active else ("insufficient_data" if not tracked else "available"),
            active_products=active, tracked_active_products=tracked,
            untracked_active_products=active - tracked,
            positive_stock_products=positive, out_of_stock_products=out,
            low_stock_products=low, negative_stock_products=negative,
            registered_stock_value=valuation["active_value"],
            value_state="no_data" if not positive else (
                "insufficient_data" if valuation["products_missing_cost"] else "available"),
            products_missing_cost=valuation["products_missing_cost"], valuation_verified=False)

    def supplier_obligations(self, date_from: date, date_to: date) -> SupplierObligations:
        period, _, _ = _period(date_from, date_to)
        data = purchase_commitments(self.db, date_from, date_to)
        return SupplierObligations(source=SOURCE, period=period,
            state="available" if data["days"] or data["overdue_outstanding_total"] else "no_data",
            days=[ObligationDay(**day) for day in data["days"]],
            estimated_total=data["estimated_total"],
            actual_outstanding_total=data["actual_outstanding_total"],
            overdue_as_of=data["overdue_as_of"],
            overdue_outstanding_total=data["overdue_outstanding_total"],
            overdue_outstanding_cash=data["overdue_outstanding_cash"],
            overdue_outstanding_digital=data["overdue_outstanding_digital"],
            overdue_outstanding_credit=data["overdue_outstanding_credit"],
            overdue_outstanding_unclassified=data["overdue_outstanding_unclassified"])

    def purchase_agenda(self, as_of: date, horizon_days: int = 30) -> PurchaseAgenda:
        if not 0 <= horizon_days <= 365:
            raise ValueError("El horizonte debe estar entre 0 y 365 dias")
        rows = purchase_agenda(self.db, as_of=as_of, horizon_days=horizon_days)
        total = self.db.scalar(select(func.count(Purchase.id)).where(
            Purchase.status == PurchaseStatus.SCHEDULED,
            Purchase.expected_delivery_date <= as_of + timedelta(days=horizon_days))) or 0
        truncated = total > len(rows)
        return PurchaseAgenda(source=SOURCE, as_of=as_of, horizon_days=horizon_days,
            state="insufficient_data" if truncated else ("available" if rows else "no_data"),
            items=[AgendaItem(purchase_id=str(row["purchase_id"]), supplier_id=str(row["supplier_id"]),
                supplier_name=row["supplier_name"], expected_delivery_date=row["expected_delivery_date"],
                estimated_total=row["estimated_total"], payment_terms=row["payment_terms"].value,
                payment_due_date=row["payment_due_date"], alert=row["alert"]) for row in rows],
            total_matching=total, truncated=truncated)
