from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.enums import PaymentMethod, PurchaseStatus


class PurchaseItemIn(BaseModel):
    product_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=3, allow_inf_nan=False)
    unit_cost: Decimal = Field(ge=0, max_digits=14, decimal_places=2, allow_inf_nan=False)


class PurchaseCreate(BaseModel):
    supplier_id: UUID
    invoice_number: str | None = Field(default=None, max_length=80)
    purchased_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=2000)
    items: list[PurchaseItemIn] = Field(min_length=1)


class PurchaseItemOut(BaseModel):
    id: UUID
    product_id: UUID
    product_name: str
    barcode: str | None
    quantity: Decimal
    unit_cost: Decimal
    line_total: Decimal

class SupplierPaymentIn(BaseModel):
    payment_channel_id: UUID
    reference: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=500)

class ReceivePurchaseIn(SupplierPaymentIn):
    pass

class SupplierPaymentOut(BaseModel):
    id: UUID
    purchase_id: UUID
    supplier_id: UUID
    amount: Decimal
    payment_channel_id: UUID
    channel_name: str
    payment_type: PaymentMethod
    affects_cash: bool
    reference: str | None
    notes: str | None
    paid_at: datetime


class PurchaseOut(BaseModel):
    id: UUID
    supplier_id: UUID
    supplier_name: str
    status: PurchaseStatus
    invoice_number: str | None
    purchased_at: datetime
    received_at: datetime | None
    notes: str | None
    subtotal: Decimal
    total: Decimal
    created_at: datetime
    created_by: UUID
    payment: SupplierPaymentOut | None = None
    items: list[PurchaseItemOut]
