from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.schemas.inventory_count import CountConfirmIn, CountConfirmOut, CountPreviewOut
from app.services.inventory_count import build_template, confirm_count, preview_count

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
router = APIRouter(prefix="/inventory/count", tags=["inventory-count"])


@router.get("/template")
def template(db: Session = Depends(get_db)):
    return Response(build_template(db), media_type=XLSX, headers={
        "Content-Disposition": f'attachment; filename="plantilla-conteo-fisico-{date.today():%Y%m%d}.xlsx"'})


@router.post("/preview", response_model=CountPreviewOut)
async def preview(request: Request, db: Session = Depends(get_db)):
    """The XLSX file is sent as the raw request body (no multipart dependency)."""
    return await run_in_threadpool(preview_count, db, await request.body())


@router.post("/confirm", response_model=CountConfirmOut, status_code=201)
def confirm(body: CountConfirmIn, db: Session = Depends(get_db)):
    return confirm_count(db, body)
