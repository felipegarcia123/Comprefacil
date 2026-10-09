"""Operational control panel of electronic invoices. Read-mostly: the only
Alegra calls are GETs (refresh signed PDF/XML links, optional readiness probe)."""
from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import ElectronicInvoice
from app.services import invoicing_ops as ops
from app.services.alegra_client import AlegraError

router = APIRouter(prefix="/electronic-invoices", tags=["electronic invoicing"])
LOCAL = ZoneInfo("America/Bogota")


def _window(db: Session, scope: str, day: date | None):
    """jornada = current operational window (same as Caja/Resumen); day = a local
    calendar day; all = no date filter."""
    if scope == "all":
        return None, None
    if scope == "day":
        if day is None:
            raise HTTPException(422, "Indica la fecha")
        start = datetime.combine(day, time.min, LOCAL)
        return start, start + timedelta(days=1)
    start, end, _ = ops.current_window(db)
    return start, end


@router.get("/mode")
def mode():
    """What the cashier may see: manual vs automatic. Never env variable names."""
    from app.core.config import settings
    auto = bool(settings.alegra_auto_issue)
    return {"automatic": auto,
            "label": "Facturación automática activa" if auto else "Facturación automática desactivada"}


@router.get("/summary")
def summary(scope: str = Query("session", pattern="^(session|day|all)$"), day: date | None = None,
            db: Session = Depends(get_db)):
    start, end = _window(db, scope, day)
    return ops.summary(db, start, end)


@router.get("/list")
def list_invoices(state: str | None = None, scope: str = Query("session", pattern="^(session|day|all)$"),
                  day: date | None = None, sale_number: str = Query("", max_length=40),
                  customer: str = Query("", max_length=100), page: int = Query(1, ge=1),
                  page_size: int = Query(50, ge=1, le=100), db: Session = Depends(get_db)):
    if state and state not in ops.DISPLAY_STATES:
        raise HTTPException(422, "Estado no válido")
    start, end = _window(db, scope, day)
    return ops.list_invoices(db, state=state, date_from=start, date_to=end, sale_number=sale_number,
                             customer=customer, page=page, page_size=page_size)


@router.get("/blocked-sales")
def blocked_sales(scope: str = Query("session", pattern="^(session|day)$"), day: date | None = None,
                  db: Session = Depends(get_db)):
    start, end = _window(db, scope, day)
    return ops.blocked_sales(db, start, end)


@router.get("/catalog-coverage")
def catalog_coverage(db: Session = Depends(get_db)):
    return ops.catalog_coverage(db)


@router.get("/unmapped-products")
def unmapped_products(q: str = Query("", max_length=100), page: int = Query(1, ge=1),
                      page_size: int = Query(50, ge=1, le=100), db: Session = Depends(get_db)):
    return ops.unmapped_products(db, q, page, page_size)


@router.get("/unmapped-products.csv")
def unmapped_products_csv(db: Session = Depends(get_db)):
    return Response(ops.unmapped_products_csv(db), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=productos_sin_mapear_alegra.csv"})


@router.get("/cutover-readiness")
def cutover_readiness(remote: bool = False, db: Session = Depends(get_db)):
    """READY_FOR_CUTOVER / NOT_READY. Never changes flags or configuration."""
    return ops.cutover_readiness(db, remote=remote)


@router.get("/sales-coverage")
def sales_coverage(db: Session = Depends(get_db)):
    """Coverage of REAL sales (lines and value) by the Alegra-linked catalog, with
    Pareto of unmapped sold products. Local analysis only."""
    from app.services.catalog_coverage import coverage, pareto, unmapped_ranking
    cov = coverage(db)
    ranking = unmapped_ranking(db, cov["since"])
    covered = cov["lines"]["mapped"] + cov["lines"]["manual_covered"]
    return {**cov, "top_unmapped": ranking[:50], "pareto": pareto(cov["lines"]["total"], covered, ranking)}


@router.get("/cutover-report")
def cutover_report(db: Session = Depends(get_db)):
    """Facts per area (technical, catalog, payments, manual, operational). No score, no flag change."""
    return ops.cutover_report(db)


@router.get("/{invoice_id}/document")
def document(invoice_id: UUID, kind: str = Query(..., pattern="^(pdf|xml)$"), force: bool = False,
             db: Session = Depends(get_db)):
    """A usable PDF/XML link. Stored signed links expire: when expired it is
    refreshed with a GET to Alegra. Never creates or changes a fiscal document."""
    record = _record(db, invoice_id)
    try:
        return ops.document_link(db, record, kind, force=force)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except AlegraError as exc:
        raise HTTPException(502, f"No fue posible consultar Alegra: {exc}")


@router.get("/{invoice_id}")
def detail(invoice_id: UUID, db: Session = Depends(get_db)):
    return ops.detail(db, _record(db, invoice_id))


def _record(db: Session, invoice_id: UUID) -> ElectronicInvoice:
    record = db.get(ElectronicInvoice, invoice_id)
    if not record:
        raise HTTPException(404, "Factura electrónica no encontrada")
    return record
