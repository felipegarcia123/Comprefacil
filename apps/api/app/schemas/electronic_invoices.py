from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ElectronicInvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    # PENDING | BLOCKED | HELD | PROCESSING | DRAFT | SENT | ACCEPTED | DIAN_REJECTED | REJECTED | ERROR
    display_status: str = "PENDING"
    sale_id: UUID
    provider: str
    status: str
    provider_invoice_id: str | None
    provider_number: str | None
    provider_status: str | None
    legal_status: str | None
    cufe: str | None = None
    pdf_url: str | None
    xml_url: str | None
    alegra_contact_id: str | None
    customer_id: UUID | None = None
    customer_snapshot: dict[str, Any] | None = None
    requested_at: datetime
    issued_at: datetime | None
    last_synced_at: datetime | None
    error_code: str | None
    error_message: str | None
    needs_reconciliation: bool = False
    last_attempt_at: datetime | None = None
    attempt_count: int = 0
    provider_payload: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class IssueInvoiceIn(BaseModel):
    # Optional: when omitted the sale's customer (or Consumidor Final) is used.
    alegra_contact_id: str | None = Field(default=None, min_length=1, max_length=40)
    # Explicit opt-in: without this, no real DIAN document is produced.
    generate_stamp: bool = False


class ConfigStatusOut(BaseModel):
    enabled: bool
    configured: bool
    missing: list[str]
    auto_issue: bool = False
    auto_issue_stamp: bool = False


class PreflightLineOut(BaseModel):
    sale_item_id: str
    product_id: str | None
    product_name: str
    code: str  # MANUAL_LINE | UNMAPPED_PRODUCT
    reason: str


class PreflightRequirementOut(BaseModel):
    code: str  # CONFIG | SALE_NOT_COMPLETED | NO_ITEMS | PAYMENT_METHOD | CONTACT
    message: str


class PreflightOut(BaseModel):
    """READY = everything needed to POST is present. BLOCKED = nothing would be sent."""
    sale_id: UUID
    sale_number: str
    status: str
    lines: list[PreflightLineOut]
    requirements: list[PreflightRequirementOut]
    alegra_contact_id: str | None


class InvoicePreviewOut(BaseModel):
    """Exactly what would be POSTed to Alegra — nothing is sent."""
    sale_id: UUID
    sale_number: str
    ready: bool
    reasons: list[str]
    alegra_contact_id: str | None
    customer: dict[str, Any] | None
    generate_stamp: bool
    payload: dict[str, Any] | None


class AlegraAuditOut(BaseModel):
    enabled: bool
    configured: bool
    missing: list[str]
    company: dict[str, Any] | None = None
    number_templates: list[dict[str, Any]] = []
    recommended_number_template_id: str | None = None
    configured_number_template_valid: bool | None = None
    taxes: list[dict[str, Any]] = []
    sample_contacts: list[dict[str, Any]] = []
    sample_items: list[dict[str, Any]] = []
    recent_invoices: list[dict[str, Any]] = []
    error: str | None = None
