"""Physical audit (store walk-through). Local only: never writes inventory or Alegra."""
import csv
import io
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.services import physical_audit as audits
from app.services.inventory_count import build_template

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
router = APIRouter(prefix="/physical-audits", tags=["physical audit"])
CSV_FIELDS = ["product_id", "barcode", "name", "category", "unit", "system_stock", "quantity", "counted",
              "difference", "alegra_item_id", "active", "last_sale_at"]
TO_CREATE_FIELDS = ["barcode", "status", "scan_count", "first_scanned_at", "last_scanned_at"]


class ScanIn(BaseModel):
    barcode: str | None = Field(default=None, min_length=1, max_length=64)
    product_id: UUID | None = None

    @model_validator(mode="after")
    def exactly_one(self):
        if (self.barcode is None) == (self.product_id is None):
            raise ValueError("Indica barcode o product_id (exactamente uno)")
        return self


class QuantityIn(BaseModel):
    # Whole units: the physical-count flow that may later take these numbers accepts integers only.
    quantity: int = Field(ge=0, le=1_000_000)


def _out(db: Session, audit_id: UUID) -> dict:
    return audits.audit_out(db, audits.get_audit(db, audit_id))


@router.get("")
def list_audits(db: Session = Depends(get_db)):
    return audits.list_audits(db)


@router.get("/current")
def current(db: Session = Depends(get_db)):
    audit = audits.current_audit(db)
    return audits.audit_out(db, audit) if audit else None


@router.post("", status_code=201)
def start(db: Session = Depends(get_db)):
    return audits.audit_out(db, audits.start_audit(db))


@router.get("/{audit_id}")
def detail(audit_id: UUID, db: Session = Depends(get_db)):
    return _out(db, audit_id)


@router.post("/{audit_id}/scan")
def scan(audit_id: UUID, body: ScanIn, db: Session = Depends(get_db)):
    found = audits.scan(db, audit_id, body.barcode, body.product_id)
    if isinstance(found, audits.PhysicalAuditUnknownCode):      # recorded as "por crear", never a new Product
        return {"item_id": None, "unknown_code": {"barcode": found.barcode, "scan_count": found.scan_count},
                **_out(db, audit_id)}
    return {"item_id": found.id, "unknown_code": None, **_out(db, audit_id)}


@router.put("/{audit_id}/items/{item_id}")
def set_quantity(audit_id: UUID, item_id: UUID, body: QuantityIn, db: Session = Depends(get_db)):
    audits.set_quantity(db, audit_id, item_id, Decimal(body.quantity))
    return _out(db, audit_id)


@router.delete("/{audit_id}/items/{item_id}")
def remove(audit_id: UUID, item_id: UUID, db: Session = Depends(get_db)):
    audits.remove_item(db, audit_id, item_id)
    return _out(db, audit_id)


@router.post("/{audit_id}/finish")
def finish(audit_id: UUID, db: Session = Depends(get_db)):
    audits.finish(db, audit_id)
    return _out(db, audit_id)


@router.post("/{audit_id}/reopen")
def reopen(audit_id: UUID, db: Session = Depends(get_db)):
    audits.reopen(db, audit_id)
    return _out(db, audit_id)


@router.get("/{audit_id}/report")
def report(audit_id: UUID, limit: int = Query(default=100, ge=1, le=1000), db: Session = Depends(get_db)):
    return audits.report(db, audits.get_audit(db, audit_id), limit)


def _cell(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return "" if value is None else value


@router.get("/{audit_id}/report.csv")
def report_csv(audit_id: UUID, section: str = Query(pattern="^(" + "|".join(audits.SECTIONS) + ")$"),
               db: Session = Depends(get_db)):
    rows = audits.report_sections(db, audits.get_audit(db, audit_id))[section]
    buffer = io.StringIO()
    fields = TO_CREATE_FIELDS if section == "to_create" else CSV_FIELDS
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows({k: _cell(r.get(k)) for k in fields} for r in rows)
    name = f"interventoria-{section}-{date.today():%Y%m%d}.csv"
    return Response("﻿" + buffer.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/{audit_id}/count-template")
def count_template(audit_id: UUID, db: Session = Depends(get_db)):
    """Counted products as a physical-count template (physical_count prefilled). Uploading it
    in Inventario > Conteo físico goes through the normal preview -> confirm flow."""
    counts = audits.counted_quantities(db, audits.get_audit(db, audit_id))
    return Response(build_template(db, counts), media_type=XLSX, headers={
        "Content-Disposition": f'attachment; filename="conteo-interventoria-{date.today():%Y%m%d}.xlsx"'})
