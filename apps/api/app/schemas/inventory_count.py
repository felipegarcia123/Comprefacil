from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class CountRowOut(BaseModel):
    row_number: int
    source_barcode: str = ""
    # Stable identifier from the new template (products without barcode are counted by it).
    source_product_id: str | None = None
    product_id: UUID | None = None
    barcode: str | None = None
    name: str | None = None
    current_stock: Decimal | None = None
    physical_count: int | None = None
    delta: Decimal | None = None
    status: Literal["CHANGE", "UNCHANGED", "SKIPPED", "ERROR"]
    errors: list[str] = []


class CountSummaryOut(BaseModel):
    file_rows: int
    with_difference: int
    unchanged: int
    skipped_blank: int
    errors: int
    positive_units: Decimal
    negative_units: Decimal
    net_units: Decimal
    active_products_not_in_file: int


class CountPreviewOut(BaseModel):
    batch_id: UUID
    session_open: bool
    can_confirm: bool
    summary: CountSummaryOut
    rows: list[CountRowOut]


class CountConfirmRowIn(BaseModel):
    product_id: UUID
    expected_current_stock: Decimal = Field(allow_inf_nan=False)
    physical_count: int = Field(ge=0, le=1_000_000)


class CountConfirmIn(BaseModel):
    batch_id: UUID
    rows: list[CountConfirmRowIn] = Field(min_length=1, max_length=20_000)


class CountConfirmOut(BaseModel):
    batch_id: UUID
    batch_label: str
    reason: str
    movements_created: int
    positive_units: Decimal
    negative_units: Decimal
    net_units: Decimal
