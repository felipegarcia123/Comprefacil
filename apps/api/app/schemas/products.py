from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProductBaseIn(BaseModel):
    # Optional: products without EAN (eggs, loose items) are sold by name. Never a fake code.
    barcode: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=180)
    sale_price: Decimal = Field(gt=0, max_digits=14, decimal_places=2, allow_inf_nan=False)
    purchase_cost: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14,
                                   decimal_places=2, allow_inf_nan=False)
    category_id: UUID | None = None
    supplier_id: UUID | None = None
    minimum_stock: int = Field(default=0, ge=0)
    unit: str = Field(default="unidad", min_length=1, max_length=40)
    description: str | None = Field(default=None, max_length=500)
    active: bool = True

    @field_validator("barcode")
    @classmethod
    def optional_barcode(cls, value: str | None):
        return (value.strip() or None) if value is not None else None

    @field_validator("name", "unit")
    @classmethod
    def required_text(cls, value: str):
        value = value.strip()
        if not value:
            raise ValueError("El valor no puede estar vacío")
        return value

    @field_validator("description")
    @classmethod
    def optional_text(cls, value: str | None):
        return value.strip() or None if value is not None else None


class ProductCreate(ProductBaseIn):
    initial_stock: int = Field(default=0, ge=0)


class ProductUpdate(ProductBaseIn):
    pass


class ProductActiveIn(BaseModel):
    active: bool


class AlegraMappingIn(BaseModel):
    alegra_item_id: str | None = Field(default=None, max_length=40)
    alegra_tax_id: str | None = Field(default=None, max_length=40)

    @field_validator("alegra_item_id", "alegra_tax_id")
    @classmethod
    def strip_text(cls, value: str | None):
        return value.strip() or None if value is not None else None


class ProductCatalogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    barcode: str | None
    name: str
    sale_price: Decimal
    purchase_cost: Decimal
    category_id: UUID | None
    category: str | None
    supplier_id: UUID | None
    supplier: str | None
    minimum_stock: int
    unit: str
    active: bool
    description: str | None
    current_stock: Decimal
    last_purchase_cost: Decimal | None
    alegra_item_id: str | None
    alegra_tax_id: str | None
    created_at: datetime
    updated_at: datetime
