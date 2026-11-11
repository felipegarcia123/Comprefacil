from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ReceiptRegion(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class ReceiptAnalyzeRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=240)
    content_type: str = Field(min_length=1, max_length=100)
    content_base64: str = Field(min_length=1, max_length=20_000_000)
    vendor_hint: str | None = Field(default=None, max_length=120)
    region: ReceiptRegion | None = None


class ReceiptCandidate(BaseModel):
    product_id: str
    name: str
    barcode: str | None
    category: str | None
    unit: str
    current_stock: str
    score: float
    reasons: list[str]


class ReceiptLine(BaseModel):
    line_id: str
    original_text: str
    supplier_code: str | None = None
    interpreted_name: str | None = None
    brand: str | None = None
    variant: str | None = None
    size: str | None = None
    package_quantity: str | None = None
    units_per_package: str | None = None
    total_units: str | None = None
    unit_cost: str | None = None
    discount: str | None = None
    confidence: float = Field(ge=0, le=1)
    review_state: Literal["READY", "REVIEW_REQUIRED", "UNMATCHED"]
    region: ReceiptRegion | None = None
    candidates: list[ReceiptCandidate] = Field(default_factory=list)


class ReceiptAnalyzeResponse(BaseModel):
    status: Literal["READY_FOR_REVIEW", "REVIEW_REQUIRED"]
    adapter: str
    filename: str
    content_type: str
    sha256: str
    vendor: str | None
    region: ReceiptRegion | None
    lines: list[ReceiptLine]
    message: str


class CatalogDiagnostic(BaseModel):
    active_products: int
    with_barcode: int
    without_barcode: int
    duplicate_normalized_names: int
    ambiguous_name_examples: list[str]


class ReceiptEquivalenceCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=120)
    material_code: str = Field(min_length=1, max_length=64)
    product_id: str
    source_description: str | None = Field(default=None, max_length=240)
    source_brand: str | None = Field(default=None, max_length=120)
    source_variant: str | None = Field(default=None, max_length=120)
    source_size: str | None = Field(default=None, max_length=80)


class ReceiptEquivalenceReplace(ReceiptEquivalenceCreate):
    pass


class ReceiptEquivalenceActive(BaseModel):
    active: bool


class ReceiptEquivalenceOut(BaseModel):
    id: str
    provider: str
    material_code: str
    product_id: str
    product_name: str
    source_description: str | None
    source_brand: str | None
    source_variant: str | None
    source_size: str | None
    confirmation_method: str
    confirmed_by: str
    confirmed_at: str
    active: bool
    deactivated_at: str | None


class ReceiptPurchaseItem(BaseModel):
    product_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=3, allow_inf_nan=False)
    unit_cost: Decimal = Field(ge=0, max_digits=14, decimal_places=2, allow_inf_nan=False)


class ReceiptPurchaseConfirm(BaseModel):
    """Human-reviewed bridge into the existing Purchase DRAFT workflow."""
    confirmed: bool = False
    supplier_id: UUID
    invoice_number: str = Field(min_length=1, max_length=80)
    purchased_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=2000)
    source_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    items: list[ReceiptPurchaseItem] = Field(min_length=1)

