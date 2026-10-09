"""Physical audit ("interventoría física"): walk the store, scan or search each product,
mark it found (quantity 1 by default, optionally a real count), finish, get a report.

Local-only and read-only for inventory: nothing here writes `inventory_movements`,
prices or Alegra. Counted quantities reach the ledger only through the regular
physical-count flow (the report exports them as a prefilled count template)."""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import SaleStatus
from app.models import (OperationalCategory, PhysicalAudit, PhysicalAuditItem, PhysicalAuditUnknownCode, Product,
                        Sale, SaleItem)
from app.services.barcodes import find_product_by_barcode
from app.services.inventory import stock_subquery

OPEN, FINISHED = "OPEN", "FINISHED"
UNKNOWN_PRODUCT = "UNKNOWN_PRODUCT"
# No "candidates to deactivate" yet: until a first physical count is applied the ledger stock
# is not a reliable signal, so a product not found is only "to review". Deactivating stays
# a manual action in Inventario, never automatic.
SECTIONS = ("present", "not_found", "counted", "without_alegra", "to_create")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_audit(db: Session, audit_id: UUID) -> PhysicalAudit:
    audit = db.get(PhysicalAudit, audit_id)
    if not audit:
        raise HTTPException(404, "Interventoría no encontrada")
    return audit


def _open_audit(db: Session, audit_id: UUID) -> PhysicalAudit:
    audit = get_audit(db, audit_id)
    if audit.status != OPEN:
        raise HTTPException(409, "La interventoría ya terminó")
    return audit


def current_audit(db: Session) -> PhysicalAudit | None:
    return db.scalar(select(PhysicalAudit).where(PhysicalAudit.status == OPEN)
                     .order_by(PhysicalAudit.started_at.desc()))


def start_audit(db: Session) -> PhysicalAudit:
    """Idempotent: only one walk at a time; starting again resumes the open one."""
    audit = current_audit(db)
    if audit:
        return audit
    audit = PhysicalAudit(status=OPEN)
    db.add(audit); db.commit(); db.refresh(audit)
    return audit


def scan(db: Session, audit_id: UUID, barcode: str | None = None,
         product_id: UUID | None = None) -> PhysicalAuditItem | PhysicalAuditUnknownCode:
    """First sighting = found with quantity 1. Scanning the same product again adds one
    unit and turns it into a real count (the operator is counting units). A scanned code
    that no Product has is kept as UNKNOWN_PRODUCT (never creates a Product)."""
    if (barcode is None) == (product_id is None):
        raise HTTPException(422, "Indica barcode o product_id (exactamente uno)")
    audit = _open_audit(db, audit_id)
    product = find_product_by_barcode(db, barcode) if barcode is not None else db.get(Product, product_id)
    if not product and barcode is not None:
        return _unknown_code(db, audit, barcode.strip())
    if not product:
        raise HTTPException(404, "Producto no encontrado")
    item = db.scalar(select(PhysicalAuditItem).where(PhysicalAuditItem.audit_id == audit.id,
                                                     PhysicalAuditItem.product_id == product.id))
    if item:
        item.quantity += 1
        item.counted = True
        item.scans += 1
        item.last_seen_at = _now()
    else:
        item = PhysicalAuditItem(audit_id=audit.id, product_id=product.id, quantity=Decimal(1), counted=False,
                                 scans=1, first_seen_at=_now(), last_seen_at=_now())
        audit.items.append(item)            # through the relationship: a loaded audit sees it too
    db.commit(); db.refresh(item); db.refresh(audit)
    return item


def _unknown_code(db: Session, audit: PhysicalAudit, barcode: str) -> PhysicalAuditUnknownCode:
    code = db.scalar(select(PhysicalAuditUnknownCode).where(PhysicalAuditUnknownCode.audit_id == audit.id,
                                                            PhysicalAuditUnknownCode.barcode == barcode))
    if code:
        code.scan_count += 1
        code.last_scanned_at = _now()
    else:
        code = PhysicalAuditUnknownCode(audit_id=audit.id, barcode=barcode, status=UNKNOWN_PRODUCT, scan_count=1,
                                        first_scanned_at=_now(), last_scanned_at=_now())
        audit.unknown_codes.append(code)
    db.commit(); db.refresh(code); db.refresh(audit)
    return code


def unknown_rows(audit: PhysicalAudit) -> list[dict]:
    return [{"barcode": c.barcode, "status": c.status, "scan_count": c.scan_count,
             "first_scanned_at": c.first_scanned_at, "last_scanned_at": c.last_scanned_at}
            for c in audit.unknown_codes]


def _item(db: Session, audit_id: UUID, item_id: UUID) -> PhysicalAuditItem:
    _open_audit(db, audit_id)
    item = db.get(PhysicalAuditItem, item_id)
    if not item or item.audit_id != audit_id:
        raise HTTPException(404, "Producto no encontrado en esta interventoría")
    return item


def set_quantity(db: Session, audit_id: UUID, item_id: UUID, quantity: Decimal) -> PhysicalAuditItem:
    item = _item(db, audit_id, item_id)
    item.quantity = quantity
    item.counted = True
    item.last_seen_at = _now()
    db.commit(); db.refresh(item)
    db.refresh(item.audit)                  # keep the list order (last seen first) fresh
    return item


def remove_item(db: Session, audit_id: UUID, item_id: UUID) -> None:
    """Undo a wrong scan: the product goes back to "not found"."""
    item = _item(db, audit_id, item_id)
    item.audit.items.remove(item)           # delete-orphan removes the row
    db.commit()


def finish(db: Session, audit_id: UUID) -> PhysicalAudit:
    audit = _open_audit(db, audit_id)
    audit.status = FINISHED
    audit.finished_at = _now()
    db.commit(); db.refresh(audit)
    return audit


def reopen(db: Session, audit_id: UUID) -> PhysicalAudit:
    audit = get_audit(db, audit_id)
    if audit.status == OPEN:
        return audit
    other = current_audit(db)
    if other:
        raise HTTPException(409, "Ya hay otra interventoría en curso")
    audit.status, audit.finished_at = OPEN, None
    db.commit(); db.refresh(audit)
    return audit


# ------------------------------------------------------------------ read models
def _stocks(db: Session, product_ids) -> dict[UUID, Decimal]:
    stocks = stock_subquery()
    ids = list(product_ids)
    if not ids:
        return {}
    return {pid: Decimal(v) for pid, v in db.execute(
        select(stocks.c.product_id, stocks.c.current_stock).where(stocks.c.product_id.in_(ids)))}


def item_rows(db: Session, audit: PhysicalAudit) -> list[dict]:
    stocks = _stocks(db, (i.product_id for i in audit.items))
    rows = []
    for i in audit.items:
        p = i.product
        stock = stocks.get(i.product_id, Decimal(0))
        rows.append({"id": i.id, "product_id": p.id, "name": p.name, "barcode": p.barcode, "unit": p.unit,
                     "category": p.operational_category.name if p.operational_category else None,
                     "quantity": i.quantity, "counted": i.counted, "scans": i.scans, "system_stock": stock,
                     "difference": (i.quantity - stock) if i.counted else None,
                     "alegra_item_id": p.alegra_item_id, "active": p.active, "last_seen_at": i.last_seen_at})
    return rows


def audit_out(db: Session, audit: PhysicalAudit) -> dict:
    rows = item_rows(db, audit)
    return {"id": audit.id, "status": audit.status, "started_at": audit.started_at, "finished_at": audit.finished_at,
            "found": len(rows), "counted": sum(1 for r in rows if r["counted"]),
            "unknown": len(audit.unknown_codes), "unknown_codes": unknown_rows(audit),
            "active_products": db.scalar(select(func.count()).select_from(Product).where(Product.active.is_(True))),
            "items": rows}


def list_audits(db: Session, limit: int = 30) -> list[dict]:
    counts = dict(db.execute(select(PhysicalAuditItem.audit_id, func.count()).group_by(PhysicalAuditItem.audit_id)).all())
    audits = db.scalars(select(PhysicalAudit).order_by(PhysicalAudit.started_at.desc()).limit(limit)).all()
    return [{"id": a.id, "status": a.status, "started_at": a.started_at, "finished_at": a.finished_at,
             "found": counts.get(a.id, 0)} for a in audits]


def _not_found_rows(db: Session, audit: PhysicalAudit) -> list[dict]:
    """Active catalog products never seen in this walk, with ledger stock and last sale."""
    stocks = stock_subquery()
    last_sale = (select(SaleItem.product_id, func.max(Sale.completed_at).label("last_sale_at"))
                 .join(Sale, Sale.id == SaleItem.sale_id).where(Sale.status == SaleStatus.COMPLETED)
                 .group_by(SaleItem.product_id).subquery())
    seen = select(PhysicalAuditItem.product_id).where(PhysicalAuditItem.audit_id == audit.id)
    result = db.execute(
        select(Product.id, Product.name, Product.barcode, Product.unit, OperationalCategory.name,
               func.coalesce(stocks.c.current_stock, 0), Product.alegra_item_id, last_sale.c.last_sale_at)
        .outerjoin(stocks, stocks.c.product_id == Product.id)
        .outerjoin(last_sale, last_sale.c.product_id == Product.id)
        .outerjoin(OperationalCategory, OperationalCategory.id == Product.operational_category_id)
        .where(Product.active.is_(True), Product.id.not_in(seen))
        .order_by(Product.name))
    return [{"product_id": pid, "name": name, "barcode": barcode, "unit": unit, "category": category,
             "system_stock": Decimal(stock), "alegra_item_id": alegra, "last_sale_at": last_sale_at}
            for pid, name, barcode, unit, category, stock, alegra, last_sale_at in result]


def report_sections(db: Session, audit: PhysicalAudit) -> dict[str, list[dict]]:
    present = item_rows(db, audit)
    return {
        "present": present,
        # "Revisar / no encontrado": not a deactivation signal on its own (see SECTIONS note).
        "not_found": _not_found_rows(db, audit),
        "counted": [r for r in present if r["counted"]],
        # Physically here but not invoiceable yet: the priority list for the Alegra catalog.
        "without_alegra": [r for r in present if not r["alegra_item_id"]],
        # Scanned codes with no Product: found in the store, missing from the POS catalog.
        "to_create": unknown_rows(audit),
    }


def report(db: Session, audit: PhysicalAudit, limit: int = 100) -> dict:
    sections = report_sections(db, audit)
    counted = sections["counted"]
    return {
        "audit": {"id": audit.id, "status": audit.status, "started_at": audit.started_at,
                  "finished_at": audit.finished_at},
        "summary": {
            "active_products": db.scalar(select(func.count()).select_from(Product).where(Product.active.is_(True))),
            **{name: len(rows) for name, rows in sections.items()},
            "present_inactive": sum(1 for r in sections["present"] if not r["active"]),
            "counted_with_difference": sum(1 for r in counted if r["difference"] != 0),
        },
        "sections": {name: rows[:limit] for name, rows in sections.items()},
    }


def counted_quantities(db: Session, audit: PhysicalAudit) -> dict[UUID, Decimal]:
    return {i.product_id: i.quantity for i in audit.items if i.counted}
