from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.config import settings
from app.schemas.receipt_ai import (CatalogDiagnostic, ReceiptAnalyzeRequest,
                                    ReceiptAnalyzeResponse, ReceiptEquivalenceActive,
                                    ReceiptEquivalenceCreate, ReceiptPurchaseConfirm)
from app.schemas.purchases import PurchaseCreate, PurchaseItemIn, PurchaseOut
from app.api.purchases import purchase_out
from app.services.purchases import create_purchase
from app.services.receipt_ai import VisionProviderError, analyze, diagnostic, find_candidates
from app.services.receipt_equivalences import (confirm_equivalence, list_equivalences,
                                               replace_equivalence, set_equivalence_active)
from uuid import UUID

router = APIRouter(prefix="/receipt-ai", tags=["receipt-ai"])


def require_receipt_write_enabled() -> None:
    if not settings.receipt_ai_write_enabled:
        raise HTTPException(403, "La escritura de recepción inteligente no está habilitada en este entorno")


@router.get("/catalog-diagnostic", response_model=CatalogDiagnostic)
def catalog_diagnostic(db: Session = Depends(get_db)):
    return diagnostic(db)


@router.get("/candidates")
def candidates(q: str = Query(default="", max_length=180), barcode: str | None = Query(default=None, max_length=64),
               material_code: str | None = Query(default=None, max_length=64), provider: str | None = Query(default=None, max_length=120),
               db: Session = Depends(get_db)):
    return find_candidates(db, q, barcode, vendor_hint=provider, material_code=material_code)


@router.get("/equivalences")
def equivalences(provider: str | None = Query(default=None, max_length=120), material_code: str | None = Query(default=None, max_length=64),
                 active: bool | None = True, db: Session = Depends(get_db)):
    return list_equivalences(db, provider, material_code, active)


@router.post("/equivalences")
def create_equivalence(body: ReceiptEquivalenceCreate, db: Session = Depends(get_db), _: None = Depends(require_receipt_write_enabled)):
    return confirm_equivalence(db, body)


@router.patch("/equivalences/{equivalence_id}/active")
def update_equivalence_active(equivalence_id: UUID, body: ReceiptEquivalenceActive, db: Session = Depends(get_db), _: None = Depends(require_receipt_write_enabled)):
    return set_equivalence_active(db, equivalence_id, body)


@router.post("/equivalences/{equivalence_id}/replace")
def replace_equivalence_route(equivalence_id: UUID, body: ReceiptEquivalenceCreate, db: Session = Depends(get_db), _: None = Depends(require_receipt_write_enabled)):
    return replace_equivalence(db, equivalence_id, body)


@router.post("/purchase", response_model=PurchaseOut, status_code=201)
def create_purchase_from_receipt(body: ReceiptPurchaseConfirm, db: Session = Depends(get_db), _: None = Depends(require_receipt_write_enabled)):
    if not body.confirmed:
        raise HTTPException(409, "La recepción requiere confirmación humana explícita")
    if len({item.product_id for item in body.items}) != len(body.items):
        raise HTTPException(422, "Un producto no puede repetirse en la misma recepción")
    purchase = create_purchase(db, PurchaseCreate(
        supplier_id=body.supplier_id, invoice_number=body.invoice_number.strip(),
        purchased_at=body.purchased_at, notes=body.notes, items=[
            PurchaseItemIn(product_id=item.product_id, quantity=item.quantity, unit_cost=item.unit_cost)
            for item in body.items]), enforce_invoice_unique=True)
    return purchase_out(db, purchase)


@router.post("/analyze", response_model=ReceiptAnalyzeResponse)
def analyze_document(body: ReceiptAnalyzeRequest, db: Session = Depends(get_db)):
    try:
        digest, lines, adapter, vendor, message = analyze(db, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except VisionProviderError as exc:
        raise HTTPException(502, str(exc)) from exc
    return ReceiptAnalyzeResponse(
        status="READY_FOR_REVIEW" if lines else "REVIEW_REQUIRED", adapter=adapter,
        filename=body.filename, content_type=body.content_type, sha256=digest,
        vendor=vendor, region=body.region, lines=lines, message=message,
    )

