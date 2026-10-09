from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

from app.services.pricing import RoundingPolicy


class PricingConfigOut(BaseModel):
    commercial_rounding_policy: RoundingPolicy


class ApplySuggestedPriceIn(BaseModel):
    unit_cost: Decimal = Field(ge=0, max_digits=14, decimal_places=2, allow_inf_nan=False)
    target_margin_percent: Decimal = Field(gt=0, lt=100, allow_inf_nan=False)


class AppliedPriceOut(BaseModel):
    product_id: UUID
    previous_sale_price: Decimal
    sale_price: Decimal
    mathematical_price: Decimal
    target_margin_percent: Decimal
    resulting_margin_percent: Decimal | None
    rounding_policy: RoundingPolicy
