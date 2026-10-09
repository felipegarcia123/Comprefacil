from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import InventoryMovementType


class InventoryProductOut(BaseModel):
    id: UUID
    barcode: str | None
    name: str
    sale_price: Decimal
    unit_cost: Decimal
    last_purchase_cost: Decimal | None
    estimated_margin_percent: Decimal | None
    current_stock: Decimal
    supplier: str | None
    supplier_id: UUID | None
    category: str | None
    category_id: UUID | None
    minimum_stock: int
    unit: str
    active: bool
    description: str | None


class InventoryMovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    product_id: UUID
    product_name: str
    movement_type: InventoryMovementType
    quantity: Decimal
    unit_cost: Decimal | None
    reference_id: UUID | None
    reason: str | None
    created_at: datetime
    user_id: UUID | None


class InventoryProductDetailOut(InventoryProductOut):
    recent_movements: list[InventoryMovementOut]


class InventoryPageOut(BaseModel):
    """Server-side paginated listing: GET /inventory never returns the full catalog in one response."""
    items: list[InventoryProductOut]
    total: int
    page: int
    page_size: int
    total_pages: int


class InventoryAdjustmentIn(BaseModel):
    product_id: UUID
    quantity_delta: Decimal = Field(max_digits=12, decimal_places=3, allow_inf_nan=False)
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("El motivo debe tener al menos 3 caracteres")
        return value


class InventoryAdjustmentOut(BaseModel):
    movement: InventoryMovementOut
    current_stock: Decimal
