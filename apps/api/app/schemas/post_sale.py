from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class SaleNoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    performed_by_text: str | None = Field(default=None, max_length=120)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=80)


class _SourceLine(BaseModel):
    """Exactly one source: an original sale line or a unit delivered by an earlier exchange."""
    sale_item_id: UUID | None = None
    replacement_line_id: UUID | None = None

    @model_validator(mode="after")
    def one_source(self):
        if (self.sale_item_id is None) == (self.replacement_line_id is None):
            raise ValueError("Indica sale_item_id o replacement_line_id (exactamente uno)")
        return self


class ReturnLineIn(_SourceLine):
    quantity: Decimal = Field(gt=0)
    # True = the product physically goes back to stock (one InventoryMovement RETURN). Ignored on
    # manual lines (no catalog product, nothing to restock).
    restock: bool = True


class SaleReturnIn(BaseModel):
    lines: list[ReturnLineIn] = Field(min_length=1)
    reason_code: str = Field(min_length=1, max_length=40)
    reason_text: str | None = Field(default=None, max_length=500)
    # One of the sale's EFFECTIVE payment channels; optional when the sale was paid with only one.
    refund_channel_id: UUID | None = None
    # 0028: business account the electronic refund leaves from (default: where that money landed).
    refund_account_id: UUID | None = None
    performed_by_text: str | None = Field(default=None, max_length=120)
    idempotency_key: str = Field(min_length=8, max_length=80)


class SaleVoidIn(BaseModel):
    reason_code: str = Field(min_length=1, max_length=40)
    reason_text: str | None = Field(default=None, max_length=500)
    restock: bool = True
    performed_by_text: str | None = Field(default=None, max_length=120)
    idempotency_key: str = Field(min_length=8, max_length=80)


class RefundConfirmIn(BaseModel):
    performed_by_text: str | None = Field(default=None, max_length=120)
    # 0028: the account the money really left from, when it was not known yet.
    account_id: UUID | None = None


class PaymentCorrectionIn(BaseModel):
    to_channel_id: UUID
    # 0028: effective destination account after the correction (same channel + another account is allowed).
    to_account_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=500)
    performed_by_text: str | None = Field(default=None, max_length=120)
    idempotency_key: str = Field(min_length=8, max_length=80)


class ExchangeReturnLineIn(_SourceLine):
    quantity: Decimal = Field(gt=0)
    restock: bool = True


class ExchangeReplacementLineIn(BaseModel):
    """Catalog product (`product_id`, current price) or a manual line (`manual_kind` SUPER / BULTO /
    PET_OTHER + description + amount). Exactly one of product_id / manual_kind."""
    product_id: UUID | None = None
    manual_kind: str | None = Field(default=None, max_length=20)
    description: str | None = Field(default=None, max_length=180)
    amount: Decimal | None = Field(default=None, gt=0)
    quantity: Decimal = Field(default=Decimal("1"), gt=0)


class ExchangeSettlementIn(BaseModel):
    """How the DIFFERENCE moves: the customer pays it (replacement worth more) or the store gives it back
    (replacement worth less, only when the cashier chooses to). Never the whole credit."""
    channel_id: UUID
    received_amount: Decimal | None = Field(default=None, ge=0)
    card_type: str | None = Field(default=None, pattern="^(DEBIT|CREDIT)$")
    reference: str | None = Field(default=None, max_length=120)
    # 0028: business account the electronic difference entered (customer pays) or leaves from (store refunds).
    account_id: UUID | None = None


class SaleExchangePreviewIn(BaseModel):
    return_lines: list[ExchangeReturnLineIn] = Field(min_length=1)
    replacement_lines: list[ExchangeReplacementLineIn] = Field(min_length=1)


class SaleExchangeIn(SaleExchangePreviewIn):
    reason_code: str = Field(min_length=1, max_length=40)
    reason_text: str | None = Field(default=None, max_length=500)
    # Only with a difference: > 0 the customer pays it, < 0 the cashier explicitly gives it back.
    settlement: ExchangeSettlementIn | None = None
    performed_by_text: str | None = Field(default=None, max_length=120)
    idempotency_key: str = Field(min_length=8, max_length=80)
