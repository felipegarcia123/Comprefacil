from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import GranelSettlement, ManualLineKind, PaymentMethod, SaleStatus


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    barcode: str | None
    name: str
    sale_price: Decimal
    unit_cost: Decimal
    stock: Decimal


class SaleItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    product_id: UUID | None
    product_name: str
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal
    manual_kind: str | None = None
    category_code: str | None = None


class SaleExternalItemOut(BaseModel):
    """Comida a granel settled by the scale: in the customer's bill, never a POS line/payment."""
    model_config = ConfigDict(from_attributes=True)
    kind: str
    settlement: str
    description: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal


class GranelSettlementIn(BaseModel):
    settlement: GranelSettlement


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    method: PaymentMethod | None
    payment_channel_id: UUID
    channel_name: str
    affects_cash: bool
    amount: Decimal
    received_amount: Decimal | None
    change_amount: Decimal | None
    reference: str | None
    card_type: str | None = None


class SaleCustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    identification_type: str
    identification_number: str
    dv: str | None
    email: str | None


class SaleCustomerIn(BaseModel):
    customer_id: UUID | None = None


class SaleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    number: str
    status: SaleStatus
    created_at: datetime
    completed_at: datetime | None
    cancelled_at: datetime | None = None
    total: Decimal
    source: str
    notes: str | None
    customer: SaleCustomerOut | None = None
    requires_electronic_invoice: bool = False
    invoice_customer_id: UUID | None = None
    granel_settlement: str | None = None
    items: list[SaleItemOut]
    external_items: list[SaleExternalItemOut] = []
    payments: list[PaymentOut]


class AddItemIn(BaseModel):
    """Exactly one of: `barcode` (scanner, unchanged) or `product_id` (picked by name —
    the only way to sell a product without barcode). Both run the same business logic."""
    barcode: str | None = Field(default=None, min_length=1, max_length=64)
    product_id: UUID | None = None
    quantity: Decimal = Field(default=Decimal("1"), gt=0)

    @model_validator(mode="after")
    def exactly_one_reference(self):
        if self.barcode is not None:
            self.barcode = self.barcode.strip() or None
        if (self.barcode is None) == (self.product_id is None):
            raise ValueError("Indica barcode o product_id (exactamente uno)")
        return self


class QuantityIn(BaseModel):
    quantity: Decimal = Field(gt=0)


class PaymentAllocationIn(BaseModel):
    channel_id: UUID
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2, allow_inf_nan=False)
    received_amount: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    reference: str | None = Field(default=None, max_length=120)
    # Card channels only (Datáfono): Débito/Crédito, for the invoice's payment method.
    card_type: Literal["DEBIT", "CREDIT"] | None = None
    # 0028: business account where this payment's money landed (never for cash).
    destination_account_id: UUID | None = None


class ManualItemIn(BaseModel):
    """"Producto manual": a line without catalog product added to the OPEN sale (never a checkout).
    amount = unit value; payment and electronic invoice are decided once, at COBRAR."""
    kind: ManualLineKind
    description: str = Field(min_length=1, max_length=180)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2, allow_inf_nan=False)
    quantity: Decimal = Field(default=Decimal("1"), gt=0, max_digits=12, decimal_places=3)

    @field_validator("description")
    @classmethod
    def strip_description(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("Escribe una descripción")
        return value


class ElectronicInvoiceIn(BaseModel):
    """The cashier ticked "Factura electrónica". Absent = no invoice (and the sale's customer,
    if any, does NOT trigger one). FINAL_CONSUMER = Consumidor final (222222222222, never typed);
    CUSTOMER = that Alegra-linked customer, independent of the sale's customer_id."""
    recipient: Literal["FINAL_CONSUMER", "CUSTOMER"]
    customer_id: UUID | None = None

    @model_validator(mode="after")
    def recipient_shape(self):
        if self.recipient == "CUSTOMER" and self.customer_id is None:
            raise ValueError("Factura a un cliente: falta customer_id")
        if self.recipient == "FINAL_CONSUMER" and self.customer_id is not None:
            raise ValueError("Consumidor final no lleva customer_id")
        return self


class CheckoutIn(BaseModel):
    method: PaymentMethod | None = None
    channel_id: UUID | None = None
    received_amount: Decimal | None = Field(default=None, ge=0)
    reference: str | None = Field(default=None, max_length=120)
    card_type: Literal["DEBIT", "CREDIT"] | None = None
    destination_account_id: UUID | None = None
    payments: list[PaymentAllocationIn] | None = Field(default=None, min_length=1)
    electronic_invoice: ElectronicInvoiceIn | None = None

    @model_validator(mode="after")
    def valid_payment_shape(self):
        choices = sum((self.payments is not None, self.channel_id is not None, self.method is not None))
        if choices != 1:
            raise ValueError("Indique payments, channel_id o method")
        if self.method == PaymentMethod.CASH and self.received_amount is None:
            raise ValueError("received_amount es obligatorio para efectivo")
        if self.payments:
            ids = [payment.channel_id for payment in self.payments]
            if len(ids) != len(set(ids)):
                raise ValueError("No repita el mismo canal de pago")
        return self


class OpenSessionIn(BaseModel):
    opening_amount: Decimal = Field(ge=0)


class CashSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    opening_amount: Decimal
    opened_at: datetime
    closed_at: datetime | None
