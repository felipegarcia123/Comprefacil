from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.domain.enums import CashMovementType
from app.models import CashSession
from app.schemas.cash import (AdjustmentIn, CashHistoryRow, CashMovementOut,
                              CashSessionOut, CashSummaryOut, CloseCashSessionIn,
                              ManualCashMovementIn, OpenCashSessionIn, ReconciliationBlock1Out,
                              ScaleClosingIn, WithdrawalIn)
from app.services.cash import (add_manual_movement, cash_history, cash_summary,
                               close_cash_session, current_session,
                               open_cash_session, _session_out)

router = APIRouter(prefix="/cash", tags=["cash"])


@router.get("/current", response_model=CashSummaryOut | None)
def current_cash(db: Session = Depends(get_db)):
    session = current_session(db)
    return cash_summary(db, session) if session else None


@router.post("/open", response_model=CashSummaryOut, status_code=201)
def open_cash(body: OpenCashSessionIn, db: Session = Depends(get_db)):
    return cash_summary(db, open_cash_session(db, body.opening_amount, body.note))


@router.post("/deposit", response_model=CashMovementOut, status_code=201)
def deposit(body: ManualCashMovementIn, db: Session = Depends(get_db)):
    return add_manual_movement(db, CashMovementType.DEPOSIT, body.amount, body.reason)


@router.post("/withdrawal", response_model=CashMovementOut, status_code=201)
def withdrawal(body: WithdrawalIn, db: Session = Depends(get_db)):
    return add_manual_movement(db, CashMovementType.WITHDRAWAL, body.amount, body.reason,
                               withdrawal_reason=body.withdrawal_reason)


@router.post("/adjustment", response_model=CashMovementOut, status_code=201)
def adjustment(body: AdjustmentIn, db: Session = Depends(get_db)):
    return add_manual_movement(db, CashMovementType.ADJUSTMENT,
                               body.amount_delta, body.reason)


@router.post("/close", response_model=CashSummaryOut)
def close_cash(body: CloseCashSessionIn, db: Session = Depends(get_db)):
    session = close_cash_session(db, body)
    return cash_summary(db, session)


@router.get("/history", response_model=list[CashHistoryRow])
def history(limit: int = Query(default=90, ge=1, le=366), db: Session = Depends(get_db)):
    return cash_history(db, limit)


@router.get("/sessions/{session_id}", response_model=CashSummaryOut)
def session_detail(session_id: UUID, db: Session = Depends(get_db)):
    session = db.get(CashSession, session_id)
    if not session:
        raise HTTPException(404, "Sesión de caja no encontrada")
    return cash_summary(db, session)


# ---- Cuadre de jornada (reconciliation; never changes expected cash or the cash ledger) ----------------------

@router.get("/reconciliation", response_model=ReconciliationBlock1Out)
def reconciliation(session_id: UUID | None = None, db: Session = Depends(get_db)):
    """Block 1 of the day's closing for the given session (default: open, else last closed)."""
    from app.services.reconciliation import block1, resolve_session
    return block1(db, resolve_session(db, session_id))


@router.put("/sessions/{session_id}/scale-closing", response_model=ReconciliationBlock1Out)
def scale_closing(session_id: UUID, body: ScaleClosingIn, db: Session = Depends(get_db)):
    """Registers the scale's closing (total + digital) for that session; its cash is derived."""
    from app.services.reconciliation import block1, resolve_session, set_scale_closing
    set_scale_closing(db, session_id, body.scale_total, body.scale_digital)
    return block1(db, resolve_session(db, session_id))
