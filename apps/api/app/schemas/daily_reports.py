from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel


class DailyReportListItem(BaseModel):
    """Built explicitly in the API layer (opened_at/closed_at come from the
    related CashSession, not a DailyReport column), not via from_attributes."""
    id: UUID
    cash_session_id: UUID
    generated_at: datetime
    opened_at: datetime
    closed_at: datetime
    sales_total: Decimal
    cash_total: Decimal
    electronic_total: Decimal
    tickets: int
    average_ticket: Decimal
    gross_profit: Decimal
    gross_margin: Decimal
    closing_expected: Decimal
    closing_actual: Decimal
    closing_difference: Decimal


class DailyReportOut(DailyReportListItem):
    gross_cost: Decimal
    report_data: dict[str, Any]


class CurrentReportOut(BaseModel):
    """OPEN: live, never-persisted preview for the open CashSession (opened_at..now).
    CLOSED: the last DailyReport, verbatim. NONE: no journey has ever existed."""
    status: Literal["OPEN", "CLOSED", "NONE"]
    cash_session_id: UUID | None
    daily_report_id: UUID | None
    generated_at: datetime | None
    opened_at: datetime | None
    closed_at: datetime | None
    sales_total: Decimal
    cash_total: Decimal
    electronic_total: Decimal
    tickets: int
    average_ticket: Decimal
    gross_cost: Decimal
    gross_profit: Decimal
    gross_margin: Decimal
    closing_expected: Decimal | None
    closing_actual: Decimal | None
    closing_difference: Decimal | None
    report_data: dict[str, Any]


class MonthlyReportOut(BaseModel):
    year: int
    month: int
    days: int
    sales_total: Decimal
    tickets: int
    average_ticket: Decimal
    cash_total: Decimal
    electronic_total: Decimal
    gross_cost: Decimal
    gross_profit: Decimal
    gross_margin: Decimal
    categories: list[dict[str, Any]]
    payment_channels: list[dict[str, Any]]
    products: list[dict[str, Any]]
    hourly: list[dict[str, Any]]
    highlights: dict[str, Any]
    days_breakdown: list[dict[str, Any]]
