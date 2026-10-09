from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import PaymentGroup, PaymentMethod


class PaymentChannelBase(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    payment_type: PaymentMethod
    payment_group: PaymentGroup | None = None
    display_order: int = Field(default=0, ge=0)
    description: str | None = Field(default=None, max_length=300)
    provider: str | None = Field(default=None, max_length=120)
    account_reference: str | None = Field(default=None, max_length=120)
    # Alegra paymentMethod for invoices; ignored for card channels (Débito/Crédito decides).
    alegra_payment_method: str | None = Field(default=None, max_length=40)
    # 0028: where this channel's money usually lands (a preselection at COBRAR, never forced) and whether an
    # electronic payment must say which business account received it. Ignored on cash channels.
    default_account_id: UUID | None = None
    requires_destination_account: bool = False

    @field_validator("alegra_payment_method")
    @classmethod
    def known_method(cls, value: str | None):
        from app.services.payment_methods import ALEGRA_PAYMENT_METHODS
        if value and value not in ALEGRA_PAYMENT_METHODS:
            raise ValueError("Medio de pago DIAN no reconocido")
        return value or None

    @field_validator("name", "description", "provider", "account_reference")
    @classmethod
    def strip_text(cls, value: str | None):
        return value.strip() or None if value is not None else None


class PaymentChannelCreate(PaymentChannelBase):
    pass


class PaymentChannelUpdate(PaymentChannelBase):
    pass


class PaymentChannelActiveIn(BaseModel):
    active: bool


class PaymentChannelOut(PaymentChannelBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    active: bool
    affects_cash: bool
    created_at: datetime
    updated_at: datetime


class PaymentSummaryRow(BaseModel):
    channel_id: UUID
    channel_name: str
    payment_type: PaymentMethod
    amount: Decimal
    payment_count: int
    percentage: Decimal


class PaymentSummaryOut(BaseModel):
    total_sales: Decimal
    channels: list[PaymentSummaryRow]
