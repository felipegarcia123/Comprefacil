"""Contracts for deterministic alerts and personal reminders."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class AlertOut(BaseModel):
    id: str
    category: Literal["LOW_STOCK", "SUPPLIER_DUE", "PURCHASE_DELIVERY", "OVERDUE_OBLIGATION"]
    priority: Literal["URGENT", "HIGH", "NORMAL"]
    title: str
    detail: str
    href: str
    due_date: date | None = None


class ReminderDraft(BaseModel):
    title: str = Field(min_length=3, max_length=240)
    due_local: datetime
    idempotency_key: UUID


class ReminderOut(BaseModel):
    id: UUID
    title: str
    due_at: datetime
    timezone: str
    status: Literal["PENDING", "COMPLETED", "CANCELLED"]
    created_at: datetime


class CenterOut(BaseModel):
    as_of: date
    alerts: list[AlertOut]
    reminders: list[ReminderOut]
    alerts_truncated: bool
    reminders_truncated: bool


class ActionIntent(BaseModel):
    action: Literal["create", "complete", "cancel"]
    draft: ReminderDraft | None = None
    reminder_id: UUID | None = None


class ActionApproval(BaseModel):
    approval_token: str = Field(min_length=40, max_length=2000)


class ReminderCreate(ReminderDraft, ActionApproval):
    pass
