from typing import Any, Literal

from pydantic import BaseModel, Field


class ProductImportRequest(BaseModel):
    filename: str = Field(max_length=255)
    content: str
    mapping: dict[str, str]
    delimiter: Literal["auto", ",", ";"] = "auto"


class ProductImportRow(BaseModel):
    row_number: int
    parsed_data: dict[str, Any]
    status: Literal["READY", "WARNING", "ERROR"]
    warnings: list[str]
    errors: list[str]


class ProductImportPreview(BaseModel):
    filename: str
    delimiter: str
    headers: list[str]
    total_rows: int
    ready_count: int
    warning_count: int
    error_count: int
    rows: list[ProductImportRow]


class ProductImportResult(BaseModel):
    imported_count: int
    skipped_count: int
    warning_count: int
    initial_stock_total: int
    rows: list[ProductImportRow]
