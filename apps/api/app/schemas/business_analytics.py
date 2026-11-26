"""Source-neutral read contracts for the future business agent tools."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


DataState = Literal["available", "no_data", "insufficient_data"]


class Period(BaseModel):
    date_from: date
    date_to: date
    timezone: str = "America/Bogota"


class SalesSummary(BaseModel):
    source: str
    period: Period
    state: DataState
    completed_sales: int
    gross_sales: Decimal
    returns_registered: Decimal
    refunds_completed: Decimal
    refunds_pending: Decimal
    net_sales: Decimal
    return_count: int
    void_count: int


class GrossProfit(BaseModel):
    source: str
    period: Period
    state: DataState
    gross_revenue: Decimal
    cost_of_goods: Decimal | None
    gross_profit: Decimal | None
    gross_margin_percent: Decimal | None
    sales_with_unreliable_cost: int
    reasons: list[str]


class ProductSales(BaseModel):
    product_id: str
    name: str
    gross_units: Decimal
    returned_units: Decimal
    net_units: Decimal
    gross_sales: Decimal
    returned_sales: Decimal
    net_sales: Decimal


class TopProducts(BaseModel):
    source: str
    period: Period
    state: DataState
    items: list[ProductSales]
    total_products: int
    excluded_manual_lines: int
    reasons: list[str]


class StockItem(BaseModel):
    product_id: str
    name: str
    stock: Decimal
    minimum_stock: int


class LowStock(BaseModel):
    source: str
    state: DataState
    items: list[StockItem]
    total: int
    truncated: bool
    untracked_active_products: int


class InventorySummary(BaseModel):
    source: str
    state: DataState
    active_products: int
    tracked_active_products: int
    untracked_active_products: int
    positive_stock_products: int
    out_of_stock_products: int
    low_stock_products: int
    negative_stock_products: int
    registered_stock_value: Decimal
    value_state: DataState
    products_missing_cost: int
    valuation_verified: bool = False


class ObligationDay(BaseModel):
    date: date
    estimated_cash: Decimal
    estimated_digital: Decimal
    estimated_credit: Decimal
    estimated_total: Decimal
    actual_outstanding_cash: Decimal
    actual_outstanding_digital: Decimal
    actual_outstanding_credit: Decimal
    actual_outstanding_unclassified: Decimal
    actual_outstanding_total: Decimal


class SupplierObligations(BaseModel):
    source: str
    period: Period
    state: DataState
    days: list[ObligationDay]
    estimated_total: Decimal
    actual_outstanding_total: Decimal
    overdue_as_of: date
    overdue_outstanding_total: Decimal
    overdue_outstanding_cash: Decimal
    overdue_outstanding_digital: Decimal
    overdue_outstanding_credit: Decimal
    overdue_outstanding_unclassified: Decimal


class AgendaItem(BaseModel):
    purchase_id: str
    supplier_id: str
    supplier_name: str
    expected_delivery_date: date
    estimated_total: Decimal
    payment_terms: str
    payment_due_date: date | None
    alert: str


class PurchaseAgenda(BaseModel):
    source: str
    as_of: date
    horizon_days: int
    state: DataState
    items: list[AgendaItem]
    total_matching: int
    truncated: bool
