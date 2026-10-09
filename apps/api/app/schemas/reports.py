from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class Comparison(BaseModel):
    value: Decimal | None


class ReportPeriod(BaseModel):
    preset: str
    date_from: datetime
    date_to: datetime
    previous_from: datetime
    previous_to: datetime
    granularity: Literal["hour", "day"]


class ReportSummary(BaseModel):
    sales_total: Decimal
    tickets: int
    average_ticket: Decimal
    units: Decimal
    expenses: Decimal
    internal_consumption: Decimal
    sales_change: Decimal | None
    tickets_change: Decimal | None
    average_ticket_change: Decimal | None
    peak_tickets_label: str | None
    peak_tickets: int
    peak_sales_label: str | None
    peak_sales: Decimal
    leading_category: str | None
    leading_channel: str | None
    # Post-sale (0027): sales_total is GROSS; returns made in the period (by return date) and net.
    returns_total: Decimal = Decimal("0")
    net_sales: Decimal | None = None
    # Product exchanges: not sales; only their collected difference is money.
    exchange_count: int = 0
    exchange_difference: Decimal = Decimal("0")
    exchange_difference_refunded: Decimal = Decimal("0")


class TimePoint(BaseModel):
    key: str
    label: str
    sales: Decimal
    tickets: int
    average_ticket: Decimal


class ReportCategory(BaseModel):
    code: str
    name: str
    cash: Decimal
    electronic: Decimal
    other: Decimal
    total: Decimal
    percentage: Decimal


class ReportPaymentGroup(BaseModel):
    group: str
    amount: Decimal
    percentage: Decimal


class ReportChannel(BaseModel):
    channel_id: UUID
    name: str
    group: str
    amount: Decimal
    payment_count: int
    percentage: Decimal


class TopProduct(BaseModel):
    product_id: UUID
    name: str
    units: Decimal
    sales: Decimal


class DashboardReport(BaseModel):
    period: ReportPeriod
    summary: ReportSummary
    series: list[TimePoint]
    categories: list[ReportCategory]
    payment_groups: list[ReportPaymentGroup]
    payment_channels: list[ReportChannel]
    top_products: list[TopProduct]
