from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import ElectronicInvoice, Sale
from app.schemas.electronic_invoices import (AlegraAuditOut, ConfigStatusOut,
                                             ElectronicInvoiceOut, InvoicePreviewOut, IssueInvoiceIn,
                                             PreflightOut)
from app.services.alegra_client import AlegraError
from app.services.electronic_invoicing import (AlreadyIssuedError, InvoiceHeldForReviewError, InvoiceInProgressError,
                                               RemoteDraftError,
                                               InvoicingConfigError, SaleValidationError, audit_account,
                                               config_status, get_electronic_invoice_for_sale, issue_invoice,
                                               preflight_sale, preview_invoice, retry_invoice,
                                               sync_invoice_status)

router = APIRouter(prefix="/electronic-invoices", tags=["electronic invoicing"])


@router.get("/config-status", response_model=ConfigStatusOut)
def get_config_status():
    from app.core.config import settings
    return {**config_status(), "auto_issue": settings.alegra_auto_issue,
            "auto_issue_stamp": settings.alegra_auto_issue_stamp}


@router.get("/account-audit", response_model=AlegraAuditOut)
def get_account_audit():
    """Read-only. Never creates contacts/items/invoices/number templates."""
    return audit_account()


@router.get("/sale/{sale_id}", response_model=ElectronicInvoiceOut | None)
def get_for_sale(sale_id: UUID, db: Session = Depends(get_db)):
    return get_electronic_invoice_for_sale(db, sale_id)


@router.get("/sale/{sale_id}/preflight", response_model=PreflightOut)
def preflight(sale_id: UUID, db: Session = Depends(get_db)):
    """READY/BLOCKED with every missing piece (lines without alegra_item_id,
    payment method, contact, config). Read-only; never touches the sale."""
    sale = db.get(Sale, sale_id)
    if not sale:
        raise HTTPException(404, "Venta no encontrada")
    try:
        return preflight_sale(db, sale)
    except AlegraError as exc:
        raise HTTPException(502, f"No fue posible consultar Alegra: {exc}")


@router.get("/sale/{sale_id}/preview", response_model=InvoicePreviewOut)
def preview(sale_id: UUID, generate_stamp: bool = False, db: Session = Depends(get_db)):
    """Read-only review of the payload before any real invoice. Never POSTs."""
    sale = db.get(Sale, sale_id)
    if not sale:
        raise HTTPException(404, "Venta no encontrada")
    return preview_invoice(db, sale, generate_stamp=generate_stamp)


def _web_issue_allowed(allow_after_cutover: bool) -> None:
    """No ordinary web call may issue an invoice outside auto-issue: before the
    cutover the only path is the controlled script. `alegra_manual_issue_enabled`
    (off by default) is an explicit administrative override."""
    from app.core.config import settings
    if settings.alegra_manual_issue_enabled or (allow_after_cutover and settings.alegra_auto_issue):
        return
    raise HTTPException(403, "La emisión desde la web está deshabilitada. Antes del cutover solo se emite con el "
                             "proceso controlado; después, la emisión es automática.")


@router.post("/sale/{sale_id}/issue", response_model=ElectronicInvoiceOut)
def issue(sale_id: UUID, body: IssueInvoiceIn, db: Session = Depends(get_db)):
    """Administrative only (disabled by default, never in the cashier's UI)."""
    _web_issue_allowed(allow_after_cutover=False)
    sale = db.get(Sale, sale_id)
    if not sale:
        raise HTTPException(404, "Venta no encontrada")
    try:
        customer = sale.customer if sale.customer_id else None
        if customer is not None and body.alegra_contact_id and customer.alegra_contact_id != body.alegra_contact_id:
            customer = None  # explicit contact overrides the sale's customer
        return issue_invoice(db, sale, body.alegra_contact_id, generate_stamp=body.generate_stamp, customer=customer)
    except InvoicingConfigError as exc:
        raise HTTPException(409, str(exc))
    except (AlreadyIssuedError, InvoiceInProgressError, InvoiceHeldForReviewError, RemoteDraftError) as exc:
        raise HTTPException(409, str(exc))
    except SaleValidationError as exc:
        raise HTTPException(422, {"message": "La venta no se puede facturar todavía", "reasons": exc.reasons})
    except AlegraError as exc:
        raise HTTPException(502, f"No fue posible consultar Alegra: {exc}")


@router.post("/{invoice_id}/retry", response_model=ElectronicInvoiceOut)
def retry(invoice_id: UUID, db: Session = Depends(get_db)):
    """Protected retry (claim + reconcile + pre-flight). Only once auto-issue is on
    (after the cutover) or with the administrative override."""
    _web_issue_allowed(allow_after_cutover=True)
    record = db.get(ElectronicInvoice, invoice_id)
    if not record:
        raise HTTPException(404, "Factura electrónica no encontrada")
    sale = db.get(Sale, record.sale_id)
    if not sale:
        raise HTTPException(404, "Venta asociada no encontrada")
    from app.core.config import settings
    try:
        # Same DIAN behaviour as the automatic issue it retries (generateStamp).
        return retry_invoice(db, record, sale, generate_stamp=settings.alegra_auto_issue_stamp)
    except InvoicingConfigError as exc:
        raise HTTPException(409, str(exc))
    except (AlreadyIssuedError, InvoiceInProgressError, InvoiceHeldForReviewError, RemoteDraftError) as exc:
        raise HTTPException(409, str(exc))
    except SaleValidationError as exc:
        raise HTTPException(422, {"message": "No se puede reintentar todavía", "reasons": exc.reasons})
    except AlegraError as exc:
        raise HTTPException(502, f"No fue posible consultar Alegra: {exc}")


@router.post("/{invoice_id}/sync", response_model=ElectronicInvoiceOut)
def sync(invoice_id: UUID, db: Session = Depends(get_db)):
    record = db.get(ElectronicInvoice, invoice_id)
    if not record:
        raise HTTPException(404, "Factura electrónica no encontrada")
    try:
        return sync_invoice_status(db, record)
    except InvoicingConfigError as exc:
        raise HTTPException(409, str(exc))
    except SaleValidationError as exc:
        raise HTTPException(422, str(exc))
    except AlegraError as exc:
        raise HTTPException(502, f"No fue posible sincronizar con Alegra: {exc}")


@router.get("/item-mapping/preview")
def item_mapping_preview(format: str = "json", db: Session = Depends(get_db)):
    """Product.barcode -> Alegra item (reference) PREVIEW. Reads the whole Alegra
    catalog (GET only, ~1 request per 30 items) and writes NOTHING."""
    from fastapi.responses import Response
    from app.services.alegra_item_mapping import build_mapping_preview, fetch_all_items, rows_to_csv
    from app.services.alegra_provider import AlegraProvider
    if not connection_status_ok():
        raise HTTPException(409, "Alegra no está configurado")
    try:
        preview = build_mapping_preview(db, fetch_all_items(AlegraProvider()))
    except AlegraError as exc:
        raise HTTPException(502, f"No fue posible leer los ítems de Alegra: {exc}")
    if format == "csv":
        return Response(rows_to_csv(preview["rows"]), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename=alegra_item_mapping_preview.csv"})
    return preview


def connection_status_ok() -> bool:
    from app.services.electronic_invoicing import connection_status
    return connection_status()["configured"]
