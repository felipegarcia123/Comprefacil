from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import ExpensePaymentSource, PaymentGroup, PaymentMethod, SaleSource
from app.schemas.pos import ElectronicInvoiceIn


class OperationalCategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    code: str
    name: str
    active: bool
    display_order: int


class ProductCategoryIn(BaseModel):
    category_id: UUID


class ManualSaleIn(BaseModel):
    source: SaleSource
    description: str = Field(min_length=1, max_length=240)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2, allow_inf_nan=False)
    channel_id: UUID
    received_amount: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    notes: str | None = Field(default=None, max_length=500)
    reference: str | None = Field(default=None, max_length=120)
    idempotency_key: str = Field(min_length=8, max_length=80)
    electronic_invoice: ElectronicInvoiceIn | None = None      # same opt-in as COBRAR; absent = no invoice
    destination_account_id: UUID | None = None                 # 0028: same rule as COBRAR (never for cash)

    @field_validator("description", "notes", "reference")
    @classmethod
    def strip_text(cls, value: str | None):
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def manual_source(self):
        if self.source not in (SaleSource.MANUAL_BULK, SaleSource.MANUAL_SACK):
            raise ValueError("El origen no corresponde a una venta manual")
        return self


class ExpenseIn(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2, allow_inf_nan=False)
    category: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=240)
    payment_source: ExpensePaymentSource
    payment_channel_id: UUID | None = None
    notes: str | None = Field(default=None, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=80)

    @field_validator("category", "description", "notes")
    @classmethod
    def strip_text(cls, value: str | None):
        return value.strip() or None if value is not None else None


class ExpenseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    amount: Decimal
    category: str
    description: str
    payment_source: ExpensePaymentSource
    payment_channel_id: UUID | None
    channel_name: str | None
    notes: str | None
    created_at: datetime
    created_by: UUID


class InternalConsumptionIn(BaseModel):
    product_id: UUID | None = None
    quantity: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=3)
    description: str = Field(min_length=1, max_length=240)
    estimated_value: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    notes: str | None = Field(default=None, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=80)

    @model_validator(mode="after")
    def valid_shape(self):
        if self.product_id is not None and self.quantity is None:
            raise ValueError("La cantidad es obligatoria para un producto de inventario")
        if self.product_id is None and self.estimated_value is None:
            raise ValueError("El valor estimado es obligatorio sin producto asociado")
        return self


class InternalConsumptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    product_id: UUID | None
    product_name: str | None
    description: str
    quantity: Decimal | None
    unit_cost: Decimal | None
    reference_sale_price: Decimal | None
    cost_total: Decimal
    sale_value_total: Decimal | None
    estimated_value: Decimal | None
    notes: str | None
    created_at: datetime
    created_by: UUID


class ChannelBreakdown(BaseModel):
    channel_id: UUID
    channel_name: str
    payment_group: PaymentGroup
    amount: Decimal


class CategorySummary(BaseModel):
    category_code: str
    category_name: str
    cash: Decimal
    electronic: Decimal
    other: Decimal
    total: Decimal
    channels: list[ChannelBreakdown]


class OutgoingSummary(BaseModel):
    expenses_total: Decimal
    expenses_cash: Decimal
    expenses_electronic: Decimal
    expenses_other: Decimal
    internal_consumption_cost: Decimal
    internal_consumption_estimated: Decimal
    home_withdrawals: Decimal
    other_withdrawals: Decimal


class ReturnCategoryAmount(BaseModel):
    category_code: str
    amount: Decimal


class ReturnsSummary(BaseModel):
    """Returns made in the window (by return date / jornada, never by the sale's).
    Net sales subtract only money effectively refunded; pending electronic refunds stay separate."""
    gross_sales: Decimal
    returns_total: Decimal
    refunded_total: Decimal = Decimal("0")
    net_sales: Decimal
    refunds_cash: Decimal
    refunds_electronic: Decimal
    refunds_electronic_pending: Decimal
    return_count: int
    void_count: int
    by_category: list[ReturnCategoryAmount] = []


class ExchangesSummary(BaseModel):
    """Product exchanges of the window: product movement, NOT sales nor refunds. Only the collected
    difference is money (cash / electronic)."""
    exchange_count: int
    same_value_count: int
    customer_pays_count: int = 0
    store_refunds_count: int = 0
    returned_credit: Decimal
    replacement_total: Decimal
    difference_collected: Decimal
    difference_cash: Decimal
    difference_electronic: Decimal
    # Differences GIVEN BACK to the customer (replacement worth less, by the cashier's choice).
    difference_refunded: Decimal = Decimal("0")
    refunded_cash: Decimal = Decimal("0")
    refunded_electronic: Decimal = Decimal("0")
    refunded_electronic_pending: Decimal = Decimal("0")


class DailyOperationsSummary(BaseModel):
    date_from: datetime
    date_to: datetime
    # OPEN: current cash session. CLOSED: last closed session (no session open
    # right now). NONE: no session has ever existed. CUSTOM: caller-provided range.
    session_status: str
    sales_total: Decimal
    # Comida a granel settled by the external scale in this window: informative only, NOT a POS sale,
    # payment or cash (never included in sales_total, categories or cash).
    external_scale_total: Decimal = Decimal("0")
    categories: list[CategorySummary]
    outgoing: OutgoingSummary
    returns: ReturnsSummary | None = None
    exchanges: ExchangesSummary | None = None
