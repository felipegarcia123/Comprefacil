from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.enums import PaymentMethod, PurchasePaymentTerms, PurchaseStatus


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


class ScheduledOfferIn(BaseModel):
    product_id: UUID
    list_unit_cost: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2,
                                           allow_inf_nan=False)
    discount_per_unit: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2,
                                              allow_inf_nan=False)
    note: str | None = Field(default=None, max_length=500)


class SchedulePurchaseIn(BaseModel):
    expected_delivery_date: date
    payment_terms: PurchasePaymentTerms
    payment_due_date: date | None = None
    offer_notes: str | None = Field(default=None, max_length=2000)
    offers: list[ScheduledOfferIn] = Field(default_factory=list)


class PurchaseItemOut(BaseModel):
    id: UUID
    product_id: UUID
    product_name: str
    barcode: str | None
    quantity: Decimal
    unit_cost: Decimal
    line_total: Decimal
    ordered_quantity: Decimal | None = None
    ordered_unit_cost: Decimal | None = None
    offer_list_unit_cost: Decimal | None = None
    offer_discount_per_unit: Decimal | None = None
    offer_note: str | None = None
    difference_reason: str | None = None
    actual_unit_cost: Decimal | None = None
    quantity_difference: Decimal | None = None
    unit_cost_difference: Decimal | None = None
    amount_difference: Decimal | None = None

class SupplierPaymentIn(BaseModel):
    payment_channel_id: UUID
    reference: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=500)

class ReceivedItemIn(BaseModel):
    product_id: UUID
    received_quantity: Decimal = Field(ge=0, max_digits=12, decimal_places=3, allow_inf_nan=False)
    actual_unit_cost: Decimal | None = Field(default=None, ge=0, max_digits=14,
                                              decimal_places=2, allow_inf_nan=False)
    difference_reason: str | None = Field(default=None, max_length=500)

class ReceiveComparisonIn(BaseModel):
    request_id: UUID
    invoice_number: str | None = Field(default=None, max_length=80)
    items: list[ReceivedItemIn] = Field(min_length=1)

class ReceivePurchaseIn(SupplierPaymentIn):
    comparison: ReceiveComparisonIn | None = None

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
    scheduled_at: datetime | None = None
    expected_delivery_date: date | None = None
    payment_terms: PurchasePaymentTerms | None = None
    payment_due_date: date | None = None
    estimated_total: Decimal | None = None
    offer_notes: str | None = None
    paid_amount: Decimal = Decimal("0")
    outstanding_amount: Decimal | None = None
    payment: SupplierPaymentOut | None = None
    items: list[PurchaseItemOut]


class PurchaseAgendaItemOut(BaseModel):
    purchase_id: UUID
    supplier_id: UUID
    supplier_name: str
    expected_delivery_date: date
    estimated_total: Decimal
    payment_terms: PurchasePaymentTerms
    payment_due_date: date | None
    status: PurchaseStatus
    alert: str


class CommitmentDayOut(BaseModel):
    date: date
    estimated_cash: Decimal = Decimal("0")
    estimated_digital: Decimal = Decimal("0")
    estimated_credit: Decimal = Decimal("0")
    estimated_total: Decimal = Decimal("0")
    actual_outstanding_cash: Decimal = Decimal("0")
    actual_outstanding_digital: Decimal = Decimal("0")
    actual_outstanding_credit: Decimal = Decimal("0")
    actual_outstanding_unclassified: Decimal = Decimal("0")
    actual_outstanding_total: Decimal = Decimal("0")


class CommitmentsOut(BaseModel):
    date_from: date
    date_to: date
    days: list[CommitmentDayOut]
    estimated_total: Decimal
    actual_outstanding_total: Decimal
    overdue_as_of: date
    overdue_outstanding_cash: Decimal
    overdue_outstanding_digital: Decimal
    overdue_outstanding_credit: Decimal
    overdue_outstanding_unclassified: Decimal
    overdue_outstanding_total: Decimal
