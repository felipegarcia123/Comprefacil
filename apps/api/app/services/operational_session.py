from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CashSession

SESSION_CLOSED_MESSAGE = "La jornada está cerrada. Abre la caja para registrar esta operación."


def require_open_session(db: Session, *, lock: bool = False) -> CashSession:
    statement = select(CashSession).where(CashSession.closed_at.is_(None))
    if lock:
        statement = statement.with_for_update()
    session = db.scalar(statement)
    if not session:
        raise HTTPException(409, detail={"code": "OPERATIONAL_SESSION_CLOSED", "message": SESSION_CLOSED_MESSAGE})
    return session
