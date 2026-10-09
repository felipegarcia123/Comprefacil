"""POS <-> Alegra catalog sync endpoints. Analysis (GET-only on Alegra) runs in the
background and is stored; LINK writes locally after live re-validation; CREATE is
prepared but disabled (ALEGRA_CATALOG_CREATE_ENABLED + dry_run + batch limit)."""
import csv
import io
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.models import CatalogSyncOperation
from app.services import alegra_catalog_sync as sync
from app.services.electronic_invoicing import connection_status

router = APIRouter(prefix="/alegra-catalog", tags=["alegra catalog"])
ROW_FIELDS = ["product_id", "barcode", "product_name", "sale_price", "current_alegra_item_id", "matched_alegra_item_id",
              "matched_alegra_name", "matched_reference", "sync_status", "confidence", "reason", "proposed_action",
              "review_hint"]


class BatchIn(BaseModel):
    product_ids: list[UUID] = Field(min_length=1, max_length=500)
    dry_run: bool = True                     # explicit opt-out required for a real (local) link


def _run_meta(run):
    if run is None:
        return None
    return {"id": run.id, "status": run.status, "started_at": run.started_at, "finished_at": run.finished_at,
            "remote_items": run.remote_items, "error": run.error}


@router.get("/status")
def status(db: Session = Depends(get_db)):
    latest = sync.latest_run(db)
    done = sync.latest_run(db, done_only=True)
    return {"create_enabled": settings.alegra_catalog_create_enabled, "batch_limit": settings.alegra_catalog_batch_limit,
            "alegra_configured": connection_status()["configured"], "latest_run": _run_meta(latest),
            "summary": done.summary if done else None, "summary_run": _run_meta(done)}


@router.post("/analysis", status_code=202)
def start_analysis(background: BackgroundTasks, db: Session = Depends(get_db)):
    if not connection_status()["configured"]:
        raise HTTPException(409, "Alegra no está configurado")
    try:
        run = sync.start_run(db)
    except sync.CatalogSyncError as exc:
        raise HTTPException(exc.status, str(exc))
    background.add_task(sync.execute_run_background, run.id)
    return _run_meta(run)


@router.get("/analysis/latest")
def latest_analysis(status: str | None = None, q: str = Query("", max_length=100), page: int = Query(1, ge=1),
                    page_size: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    run = sync.latest_run(db, done_only=True)
    if run is None:
        return {"run": None, "summary": None, "rows": [], "total": 0, "page": 1, "total_pages": 1}
    rows = run.rows or []
    if status:
        rows = [r for r in rows if r["sync_status"] == status]
    if q.strip():
        term = q.strip().lower()
        rows = [r for r in rows if term in (r["product_name"] or "").lower() or term in (r["barcode"] or "")]
    total = len(rows)
    return {"run": _run_meta(run), "summary": run.summary, "total": total, "page": page,
            "total_pages": max(1, -(-total // page_size)), "rows": rows[(page - 1) * page_size: page * page_size]}


@router.get("/analysis/latest.csv")
def latest_csv(db: Session = Depends(get_db)):
    run = sync.latest_run(db, done_only=True)
    if run is None:
        raise HTTPException(404, "Todavía no hay análisis")
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=ROW_FIELDS, extrasaction="ignore")
    writer.writeheader(); writer.writerows(run.rows or [])
    return Response(buffer.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=catalog_sync_preview.csv"})


@router.post("/link")
def link(body: BatchIn, db: Session = Depends(get_db)):
    """Only products whose LAST analysis found an unambiguous match (EXACT / UPC_EAN);
    each one is re-validated live against Alegra before writing."""
    run = sync.latest_run(db, done_only=True)
    expected = {r["product_id"]: r["matched_alegra_item_id"] for r in (run.rows if run else [])
                if r["proposed_action"] == "LINK"}
    try:
        return sync.sync_batch(db, body.product_ids, "LINK", dry_run=body.dry_run, expected=expected)
    except sync.CatalogSyncError as exc:
        raise HTTPException(exc.status, str(exc))


@router.post("/create")
def create(body: BatchIn, db: Session = Depends(get_db)):
    """Prepared, not enabled: returns READY_TO_CREATE + payload. A real run is refused (403)
    while ALEGRA_CATALOG_CREATE_ENABLED is off — the UI cannot bypass it."""
    try:
        return sync.sync_batch(db, body.product_ids, "CREATE", dry_run=body.dry_run)
    except sync.CatalogSyncError as exc:
        raise HTTPException(exc.status, str(exc))


@router.get("/operations")
def operations(limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)):
    rows = db.scalars(select(CatalogSyncOperation).order_by(CatalogSyncOperation.created_at.desc()).limit(limit)).all()
    return [{"id": r.id, "product_id": r.product_id, "action": r.action, "old_alegra_item_id": r.old_alegra_item_id,
             "new_alegra_item_id": r.new_alegra_item_id, "reference": r.reference, "status": r.status,
             "error": r.error, "created_at": r.created_at} for r in rows]


@router.get("/priority-products")
def priority_products(db: Session = Depends(get_db)):
    """Reverse flow preview: the 7 approved historical Alegra items without POS product (not created)."""
    return sync.priority_pos_products_preview(db)
