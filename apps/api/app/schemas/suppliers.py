from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SupplierBase(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    document: str | None = Field(default=None, max_length=40)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=180)
    address: str | None = Field(default=None, max_length=240)
    contact_name: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("name", "document", "phone", "email", "address", "contact_name", "notes")
    @classmethod
    def strip_text(cls, value: str | None):
        return value.strip() or None if value is not None else None


class SupplierCreate(SupplierBase):
    pass


class SupplierUpdate(SupplierBase):
    pass


class SupplierActiveIn(BaseModel):
    active: bool


class SupplierOut(SupplierBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    active: bool
    created_at: datetime


class SupplierPurchaseSummary(BaseModel):
    id: UUID
    status: str
    invoice_number: str | None
    purchased_at: datetime
    received_at: datetime | None
    total: Decimal


class SupplierDetailOut(SupplierOut):
    purchases: list[SupplierPurchaseSummary]
    total_purchased: Decimal
    last_purchase_at: datetime | None
