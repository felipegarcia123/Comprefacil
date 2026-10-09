from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    alegra_contact_id: str | None
    identification_type: str
    identification_number: str
    dv: str | None
    kind_of_person: str
    name: str
    first_name: str | None
    last_name: str | None
    email: str | None
    phone: str | None
    address: str | None
    active: bool
    last_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CustomerCreate(BaseModel):
    identification_type: str = Field(min_length=1, max_length=20)
    identification_number: str = Field(min_length=1, max_length=40)
    kind_of_person: Literal["PERSON_ENTITY", "LEGAL_ENTITY"] | None = None
    # LEGAL_ENTITY: razón social. PERSON_ENTITY: first_name + last_name (Alegra's nameObject).
    name: str | None = Field(default=None, max_length=200)
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=180, pattern=r"^$|^[^@\s]+@[^@\s]+\.[^@\s]+$")
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=240)


class CustomerUpdate(BaseModel):
    """Contact details only — the fiscal identity (document, name) is not editable."""
    email: str | None = Field(default=None, max_length=180, pattern=r"^$|^[^@\s]+@[^@\s]+\.[^@\s]+$")
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=240)


class CustomerActiveIn(BaseModel):
    active: bool


class CustomerCreateResult(BaseModel):
    customer: CustomerOut
    # EXISTING_LOCAL: already in the POS · LINKED_REMOTE: already in Alegra, now linked · CREATED: new in Alegra
    outcome: Literal["EXISTING_LOCAL", "LINKED_REMOTE", "CREATED"]


class CustomerSyncResult(BaseModel):
    created: int
    updated: int
    unchanged: int
    errors: int
    error_messages: list[str]
    total_remote: int
    finished_at: datetime | None


class CustomerSyncStatus(BaseModel):
    alegra_configured: bool
    total_customers: int
    last_synced_at: datetime | None
    final_consumer_resolved: bool
