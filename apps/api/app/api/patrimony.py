"""Daily patrimony & account reconciliation (0028). Read models + the few explicit writes (balances typed at
the close, their audited corrections and explicit external account movements). Nothing here changes a sale,
a payment, the cash drawer, expected cash or Block 1."""
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import CashSession
from app.schemas.cash import AccountBalancesIn, DeclaredPatrimonyIn
from app.services import business_accounts as accounts_service
from app.services import inventory_valuation, patrimony

router = APIRouter(tags=["patrimony"])


class SnapshotCorrectionIn(BaseModel):
    new_observed_balance: Decimal = Field(ge=0, max_digits=16, decimal_places=2, allow_inf_nan=False)
    reason: str = Field(min_length=3, max_length=300)
    idempotency_key: str = Field(min_length=8, max_length=80)


class AccountMovementIn(BaseModel):
    reason: str = Field(pattern="^(OTHER_IN|OTHER_OUT|TRANSFER_BETWEEN_ACCOUNTS)$")
    account_id: UUID
    to_account_id: UUID | None = None
    amount: Decimal = Field(gt=0, max_digits=16, decimal_places=2, allow_inf_nan=False)
    note: str | None = Field(default=None, max_length=300)
    idempotency_key: str = Field(min_length=8, max_length=80)


class AccountMovementReverseIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)
    idempotency_key: str = Field(min_length=8, max_length=80)


class DeclarationCorrectionIn(BaseModel):
    new_value: Decimal = Field(ge=0, max_digits=16, decimal_places=2, allow_inf_nan=False)
    reason: str = Field(min_length=3, max_length=300)
    idempotency_key: str = Field(min_length=8, max_length=80)


class PatrimonySourceIn(BaseModel):
    source: str = Field(pattern="^(MANUAL|AUTOMATIC)$")
    reason: str = Field(min_length=10, max_length=500)
    performed_by_text: str | None = Field(default=None, max_length=120)
    idempotency_key: str = Field(min_length=8, max_length=80)


def _session(db: Session, session_id: UUID) -> CashSession:
    session = db.get(CashSession, session_id)
    if session is None:
        raise HTTPException(404, "Jornada no encontrada")
    return session


# ---- inventory patrimony ------------------------------------------------------------------------------

@router.get("/inventory-valuation/current")
def valuation_current(db: Session = Depends(get_db)):
    """Live value now (stock x current cost) vs the last frozen close. Never persisted."""
    return inventory_valuation.current_valuation(db)


@router.get("/inventory-valuation/history")
def valuation_history(limit: int = Query(default=60, ge=1, le=366), db: Session = Depends(get_db)):
    return inventory_valuation.valuation_history(db, limit)


@router.get("/cash/sessions/{session_id}/inventory-valuation")
def valuation_for_session(session_id: UUID, db: Session = Depends(get_db)):
    return inventory_valuation.session_valuation(db, _session(db, session_id))


# ---- declared patrimony (MANUAL, the official source by default) -----------------------------------------

@router.get("/patrimony/history")
def patrimony_history(limit: int = Query(default=60, ge=1, le=366), db: Session = Depends(get_db)):
    """Declared (MANUAL) and registered (POS, diagnostic) series side by side — never mixed."""
    return patrimony.patrimony_history(db, limit)


@router.get("/patrimony/source")
def patrimony_source(db: Session = Depends(get_db)):
    return {**patrimony.official_source(db), "changes": patrimony.source_history(db),
            "confirmed_physical_counts": patrimony.confirmed_physical_counts(db)}


@router.post("/patrimony/source", status_code=201)
def change_patrimony_source(body: PatrimonySourceIn, db: Session = Depends(get_db)):
    """Explicit, audited change of the official source (AUTOMATIC only after a confirmed physical inventory)."""
    return patrimony.set_official_source(db, body.source, body.reason, body.idempotency_key, body.performed_by_text)


@router.get("/cash/sessions/{session_id}/patrimony-declaration")
def session_patrimony_declaration(session_id: UUID, db: Session = Depends(get_db)):
    return patrimony.session_declaration(db, _session(db, session_id))


@router.put("/cash/sessions/{session_id}/patrimony-declaration")
def declare_patrimony(session_id: UUID, body: DeclaredPatrimonyIn, db: Session = Depends(get_db)):
    """Declare after the close (once per jornada; afterwards only an audited correction)."""
    patrimony.declare_after_close(db, session_id, body.value, body.note, body.performed_by_text)
    return patrimony.session_declaration(db, _session(db, session_id))


@router.post("/patrimony-declarations/{declaration_id}/corrections", status_code=201)
def correct_patrimony(declaration_id: UUID, body: DeclarationCorrectionIn, db: Session = Depends(get_db)):
    declaration = patrimony.correct_declaration(db, declaration_id, body.new_value, body.reason, body.idempotency_key)
    return patrimony.session_declaration(db, _session(db, declaration.cash_session_id))


# ---- business accounts --------------------------------------------------------------------------------

@router.get("/business-accounts")
def business_accounts(active: bool | None = None, db: Session = Depends(get_db)):
    return [{"id": a.id, "code": a.code, "name": a.name, "active": a.active, "display_order": a.display_order}
            for a in accounts_service.list_accounts(db, active)]


@router.get("/business-accounts/history")
def business_accounts_history(limit: int = Query(default=60, ge=1, le=366), db: Session = Depends(get_db)):
    return accounts_service.accounts_history(db, limit)


@router.get("/cash/sessions/{session_id}/accounts")
def session_accounts(session_id: UUID, db: Session = Depends(get_db)):
    return accounts_service.account_reconciliation(db, _session(db, session_id))


@router.put("/cash/sessions/{session_id}/account-balances")
def session_account_balances(session_id: UUID, body: AccountBalancesIn, db: Session = Depends(get_db)):
    """Completes a pending account reconciliation after the close. A registered balance is never
    overwritten here: it is fixed with an audited correction."""
    session = accounts_service.register_balances_after_close(
        db, session_id, [balance.model_dump() for balance in body.balances], body.note)
    return accounts_service.account_reconciliation(db, session)


@router.post("/business-account-snapshots/{snapshot_id}/corrections", status_code=201)
def correct_account_snapshot(snapshot_id: UUID, body: SnapshotCorrectionIn, db: Session = Depends(get_db)):
    snapshot = accounts_service.correct_snapshot(db, snapshot_id, body.new_observed_balance, body.reason,
                                                 body.idempotency_key)
    return accounts_service.account_reconciliation(db, _session(db, snapshot.cash_session_id))


@router.get("/business-account-movements")
def account_movements(session_id: UUID | None = None, db: Session = Depends(get_db)):
    return accounts_service.list_movements(db, session_id)


@router.post("/business-account-movements", status_code=201)
def create_account_movement(body: AccountMovementIn, db: Session = Depends(get_db)):
    rows = accounts_service.create_movement(db, reason=body.reason, account_id=body.account_id, amount=body.amount,
                                            note=body.note, to_account_id=body.to_account_id,
                                            idempotency_key=body.idempotency_key)
    return accounts_service.movements_out(db, rows)


@router.post("/business-account-movements/{movement_id}/reverse", status_code=201)
def reverse_account_movement(movement_id: UUID, body: AccountMovementReverseIn, db: Session = Depends(get_db)):
    rows = accounts_service.reverse_movement(db, movement_id, body.reason, body.idempotency_key)
    return accounts_service.movements_out(db, rows)
