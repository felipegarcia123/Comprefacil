from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import CashMovementType, PaymentMethod, WithdrawalReason


class OpenCashSessionIn(BaseModel):
    opening_amount: Decimal = Field(ge=0, max_digits=14, decimal_places=2, allow_inf_nan=False)
    note: str | None = Field(default=None, max_length=500)

    @field_validator("note")
    @classmethod
    def clean_note(cls, value: str | None):
        return value.strip() or None if value is not None else None


class ManualCashMovementIn(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2, allow_inf_nan=False)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str):
        value = value.strip()
        if not value:
            raise ValueError("El motivo es obligatorio")
        return value


class WithdrawalIn(ManualCashMovementIn):
    withdrawal_reason: WithdrawalReason


class AdjustmentIn(BaseModel):
    amount_delta: Decimal = Field(max_digits=14, decimal_places=2, allow_inf_nan=False)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("amount_delta")
    @classmethod
    def non_zero(cls, value: Decimal):
        if value == 0:
            raise ValueError("El ajuste no puede ser cero")
        return value

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str):
        value = value.strip()
        if not value:
            raise ValueError("El motivo es obligatorio")
        return value


class AccountBalanceIn(BaseModel):
    """The balance the owner sees in a business account at the close (0028). Never a payment or a movement."""
    account_id: UUID
    observed_balance: Decimal = Field(ge=0, max_digits=16, decimal_places=2, allow_inf_nan=False)


class AccountBalancesIn(BaseModel):
    balances: list[AccountBalanceIn] = Field(min_length=1)
    note: str | None = Field(default=None, max_length=300)


class DeclaredPatrimonyIn(BaseModel):
    """VALOR DECLARADO POR EL DUEÑO: total value of the store's merchandise (optional, origin MANUAL)."""
    value: Decimal = Field(ge=0, max_digits=16, decimal_places=2, allow_inf_nan=False)
    note: str | None = Field(default=None, max_length=300)
    performed_by_text: str | None = Field(default=None, max_length=120)


class CloseCashSessionIn(BaseModel):
    counted_amount: Decimal = Field(ge=0, max_digits=14, decimal_places=2, allow_inf_nan=False)
    expected_amount: Decimal = Field(ge=0, max_digits=14, decimal_places=2, allow_inf_nan=False)
    note: str | None = Field(default=None, max_length=500)
    # 0028: optional; accounts left out stay "Conciliación de cuentas pendiente" and can be completed later.
    account_balances: list[AccountBalanceIn] | None = None
    # 0028: optional; never blocks the close (can be declared or corrected after it).
    declared_patrimony: DeclaredPatrimonyIn | None = None

    @field_validator("note")
    @classmethod
    def clean_note(cls, value: str | None):
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def note_for_difference(self):
        if self.counted_amount != self.expected_amount and not self.note:
            raise ValueError("La observación es obligatoria cuando existe diferencia")
        return self


class CashMovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    movement_type: CashMovementType
    amount: Decimal
    reference_id: UUID | None
    reference: str | None = None
    notes: str | None
    created_at: datetime
    user_id: UUID


class CashSessionOut(BaseModel):
    id: UUID
    status: str
    opening_amount: Decimal
    opening_note: str | None
    opened_at: datetime
    opened_by: UUID
    closed_at: datetime | None
    closed_by: UUID | None
    closing_expected: Decimal | None
    closing_actual: Decimal | None
    difference: Decimal | None
    closing_note: str | None


class ChannelCashSummary(BaseModel):
    channel_id: UUID
    channel_name: str
    payment_type: PaymentMethod
    amount: Decimal
    payment_count: int

class FinancialSessionMovement(BaseModel):
    id: UUID
    occurred_at: datetime
    type: str
    direction: str
    amount: Decimal
    payment_channel: str | None = None
    affects_cash: bool
    title: str
    subtitle: str | None = None
    reference_type: str
    reference_id: UUID | None = None
    # Only set for WITHDRAWAL rows — lets the UI show "Retiro · Casa" explicitly (section 7),
    # never inferred from free-text notes.
    withdrawal_reason: WithdrawalReason | None = None

class SupplierPaymentSummary(BaseModel):
    channel_id: UUID
    channel_name: str
    payment_type: PaymentMethod
    amount: Decimal
    payment_count: int


class CashSummaryOut(BaseModel):
    session: CashSessionOut
    cash_sales: Decimal
    deposits: Decimal
    withdrawals: Decimal
    refunds: Decimal
    adjustments: Decimal
    expenses: Decimal
    supplier_payments_cash: Decimal
    # Net cash effect of payment channel corrections (0027): + electronic->cash, - cash->electronic.
    payment_corrections_cash: Decimal = Decimal("0")
    # Product exchanges: the cash difference collected (a same-value exchange never moves cash).
    exchanges_cash: Decimal = Decimal("0")
    # Exchange differences given back in cash (positive figure; the movement itself is negative).
    exchange_refunds_cash: Decimal = Decimal("0")
    expected_cash: Decimal
    total_sales: Decimal
    transaction_count: int
    payment_channels: list[ChannelCashSummary]
    recent_movements: list[CashMovementOut]
    financial_movements: list[FinancialSessionMovement]
    supplier_payments_total: Decimal
    supplier_payment_channels: list[SupplierPaymentSummary]


class CashHistoryRow(BaseModel):
    session: CashSessionOut
    cash_sales: Decimal
    deposits: Decimal
    withdrawals: Decimal
    expected_cash: Decimal
    # 0028: the frozen inventory photo (None = "Sin snapshot patrimonial") and how many business accounts
    # have their closing balance registered (None while the jornada is open).
    inventory_value: Decimal | None = None          # POS registered value (diagnostic, NO VERIFICADA)
    declared_patrimony: Decimal | None = None       # owner's declared value (MANUAL), effective after corrections
    accounts_registered: int | None = None
    accounts_total: int | None = None


# ---- Cuadre de jornada: reconciliation data (never a payment, cash movement or sale) --------------------------

class ScaleClosingIn(BaseModel):
    """What the external scale reports at its own closing. Its cash is derived, never typed."""
    scale_total: Decimal = Field(ge=0, max_digits=14, decimal_places=2, allow_inf_nan=False)
    scale_digital: Decimal = Field(ge=0, max_digits=14, decimal_places=2, allow_inf_nan=False)

    @model_validator(mode="after")
    def digital_within_total(self):
        if self.scale_digital > self.scale_total:
            raise ValueError("Los pagos digitales de la báscula no pueden superar el total de la báscula")
        return self


class ScaleClosingOut(BaseModel):
    total: Decimal
    digital: Decimal
    cash: Decimal
    updated_at: datetime | None = None


class CategoryCashOut(BaseModel):
    category_code: str
    amount: Decimal


class ObservedGranelOut(BaseModel):
    granel_external_scale: Decimal
    granel_pos: Decimal
    granel_pos_cash_legacy: Decimal


class ReconciliationBlock1Out(BaseModel):
    """Block 1 of the day's closing ("Subtotal parcial del cuadre"): NOT expected cash nor a difference."""
    cash_session_id: UUID
    session_status: str
    date_from: datetime
    date_to: datetime
    scale: ScaleClosingOut | None
    pos_cash_by_category: list[CategoryCashOut]
    pos_cash_subtotal: Decimal
    block1_subtotal: Decimal | None
    observed: ObservedGranelOut
    # Post-sale (0027): cash refunds of block-1 categories in this jornada, shown APART. They are NOT
    # subtracted from pos_cash_by_category / block1_subtotal (the block-1 formula is unchanged).
    returns_cash_by_category: list[CategoryCashOut] = []
    returns_cash_total: Decimal = Decimal("0")
    # Product exchanges (0027): cash collected as the difference of an exchange, apart (not in the formula).
    exchange_difference_cash_by_category: list[CategoryCashOut] = []
    exchange_difference_cash_total: Decimal = Decimal("0")
    # Exchange differences GIVEN BACK in cash, apart too (never subtracted from block 1).
    exchange_refund_cash_by_category: list[CategoryCashOut] = []
    exchange_refund_cash_total: Decimal = Decimal("0")
