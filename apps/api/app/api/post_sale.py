"""Post-sale management (0027): detail + append-only operations on a COMPLETED sale.
Every response is the refreshed sale detail, so the UI never computes rules on its own."""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.schemas.post_sale import (PaymentCorrectionIn, RefundConfirmIn, SaleExchangeIn, SaleExchangePreviewIn,
                                   SaleNoteIn, SaleReturnIn, SaleVoidIn)
from app.services import post_sale

router = APIRouter(tags=["post-sale"])


@router.get("/sales/{sale_id}/detail")
def sale_detail(sale_id: UUID, db: Session = Depends(get_db)):
    return post_sale.sale_detail(db, sale_id)


@router.get("/post-sale/return-reasons")
def return_reasons():
    return [{"code": code, "label": label} for code, label in post_sale.RETURN_REASONS.items()]


@router.post("/sales/{sale_id}/notes", status_code=201)
def add_note(sale_id: UUID, body: SaleNoteIn, db: Session = Depends(get_db)):
    post_sale.add_note(db, sale_id, body.text, body.performed_by_text, body.idempotency_key)
    return post_sale.sale_detail(db, sale_id)


@router.post("/sales/{sale_id}/returns", status_code=201)
def create_return(sale_id: UUID, body: SaleReturnIn, db: Session = Depends(get_db)):
    post_sale.create_return(db, sale_id, lines=[line.model_dump() for line in body.lines],
                            reason_code=body.reason_code, reason_text=body.reason_text,
                            refund_channel_id=body.refund_channel_id, idempotency_key=body.idempotency_key,
                            performed_by_text=body.performed_by_text, refund_account_id=body.refund_account_id)
    return post_sale.sale_detail(db, sale_id)


@router.post("/sales/{sale_id}/void", status_code=201)
def void_sale(sale_id: UUID, body: SaleVoidIn, db: Session = Depends(get_db)):
    post_sale.void_sale(db, sale_id, reason_code=body.reason_code, reason_text=body.reason_text,
                        restock=body.restock, idempotency_key=body.idempotency_key,
                        performed_by_text=body.performed_by_text)
    return post_sale.sale_detail(db, sale_id)


@router.post("/sales/{sale_id}/refunds/{refund_id}/confirm")
def confirm_refund(sale_id: UUID, refund_id: UUID, body: RefundConfirmIn, db: Session = Depends(get_db)):
    post_sale.confirm_refund(db, sale_id, refund_id, body.performed_by_text, account_id=body.account_id)
    return post_sale.sale_detail(db, sale_id)


@router.post("/sales/{sale_id}/payments/{payment_id}/correct", status_code=201)
def correct_payment(sale_id: UUID, payment_id: UUID, body: PaymentCorrectionIn, db: Session = Depends(get_db)):
    post_sale.correct_payment(db, sale_id, payment_id, to_channel_id=body.to_channel_id, reason=body.reason,
                              idempotency_key=body.idempotency_key, performed_by_text=body.performed_by_text,
                              to_account_id=body.to_account_id)
    return post_sale.sale_detail(db, sale_id)


@router.get("/post-sale/exchange-reasons")
def exchange_reasons():
    return [{"code": code, "label": label} for code, label in post_sale.EXCHANGE_REASONS.items()]


@router.post("/sales/{sale_id}/exchanges/preview")
def preview_exchange(sale_id: UUID, body: SaleExchangePreviewIn, db: Session = Depends(get_db)):
    """Returned credit / replacement total / difference, computed by the backend, with no write."""
    return post_sale.preview_exchange(db, sale_id, return_lines=[line.model_dump() for line in body.return_lines],
                                      replacement_lines=[line.model_dump() for line in body.replacement_lines])


@router.post("/sales/{sale_id}/exchanges", status_code=201)
def create_exchange(sale_id: UUID, body: SaleExchangeIn, db: Session = Depends(get_db)):
    post_sale.create_exchange(db, sale_id, return_lines=[line.model_dump() for line in body.return_lines],
                              replacement_lines=[line.model_dump() for line in body.replacement_lines],
                              reason_code=body.reason_code, reason_text=body.reason_text,
                              settlement=body.settlement.model_dump() if body.settlement else None,
                              idempotency_key=body.idempotency_key, performed_by_text=body.performed_by_text)
    return post_sale.sale_detail(db, sale_id)


@router.post("/sales/{sale_id}/exchanges/{exchange_id}/refund/confirm")
def confirm_exchange_refund(sale_id: UUID, exchange_id: UUID, body: RefundConfirmIn, db: Session = Depends(get_db)):
    """The difference of an exchange was given back electronically outside the POS. Idempotent."""
    post_sale.confirm_exchange_refund(db, sale_id, exchange_id, body.performed_by_text, account_id=body.account_id)
    return post_sale.sale_detail(db, sale_id)
