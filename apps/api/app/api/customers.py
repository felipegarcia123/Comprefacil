from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Customer
from app.schemas.customers import (CustomerActiveIn, CustomerCreate, CustomerCreateResult, CustomerOut,
                                   CustomerSyncResult, CustomerSyncStatus, CustomerUpdate)
from app.services.alegra_client import (AlegraAuthError, AlegraError, AlegraNotConfigured,
                                        AlegraValidationError)
from app.services.customers import (CustomerError, create_customer, last_sync_at, prepare_new_customer,
                                    resolve_final_consumer_contact_id, search_customers, sync_customers,
                                    update_customer)
from app.services.electronic_invoicing import connection_status

router = APIRouter(prefix="/customers", tags=["customers"])


def _alegra_http(exc: AlegraError) -> HTTPException:
    """Alegra failures as clear, secret-free messages. Nothing was stored locally."""
    if isinstance(exc, AlegraNotConfigured):
        return HTTPException(409, "Alegra no está configurado: no se pueden crear ni sincronizar clientes")
    if isinstance(exc, AlegraValidationError):
        return HTTPException(422, f"Alegra rechazó los datos del cliente: {exc}")
    if isinstance(exc, AlegraAuthError):
        return HTTPException(502, "Alegra rechazó las credenciales configuradas")
    return HTTPException(502, f"No fue posible comunicarse con Alegra: {exc}")


def _get(db: Session, customer_id: UUID) -> Customer:
    customer = db.get(Customer, customer_id)
    if not customer or customer.is_final_consumer:
        raise HTTPException(404, "Cliente no encontrado")
    return customer


@router.get("", response_model=list[CustomerOut])
def list_customers(q: str = Query(default="", max_length=100), active: bool | None = True,
                   limit: int = Query(default=20, ge=1, le=200), db: Session = Depends(get_db)):
    """Local only — never calls Alegra (safe to use while typing)."""
    return search_customers(db, q, active, limit)


@router.get("/sync-status", response_model=CustomerSyncStatus)
def sync_status(db: Session = Depends(get_db)):
    total = db.scalar(select(func.count()).select_from(Customer).where(Customer.is_final_consumer.is_(False)))
    return CustomerSyncStatus(
        alegra_configured=connection_status()["configured"], total_customers=total or 0,
        last_synced_at=last_sync_at(db),
        final_consumer_resolved=bool(resolve_final_consumer_contact_id(db, allow_remote=False)))


@router.post("/sync", response_model=CustomerSyncResult)
def sync(db: Session = Depends(get_db)):
    if not connection_status()["configured"]:
        raise HTTPException(409, "Alegra no está configurado: no se pueden sincronizar clientes")
    try:
        result = sync_customers(db)
    except AlegraError as exc:
        db.rollback()
        raise _alegra_http(exc)
    return CustomerSyncResult(**result.__dict__)


@router.get("/{customer_id}", response_model=CustomerOut)
def get_customer(customer_id: UUID, db: Session = Depends(get_db)):
    return _get(db, customer_id)


@router.post("", response_model=CustomerCreateResult)
def create(body: CustomerCreate, db: Session = Depends(get_db)):
    try:
        data = prepare_new_customer(body.identification_type, body.identification_number, body.kind_of_person,
                                    body.name, body.first_name, body.last_name, body.email, body.phone,
                                    body.address)
        result = create_customer(db, data)
    except CustomerError as exc:
        raise HTTPException(exc.status, str(exc))
    except AlegraError as exc:
        raise _alegra_http(exc)
    return CustomerCreateResult(customer=result.customer, outcome=result.outcome)


@router.put("/{customer_id}", response_model=CustomerOut)
def update(customer_id: UUID, body: CustomerUpdate, db: Session = Depends(get_db)):
    customer = _get(db, customer_id)
    try:
        return update_customer(db, customer, body.email, body.phone, body.address)
    except CustomerError as exc:
        raise HTTPException(exc.status, str(exc))
    except AlegraError as exc:
        db.rollback()
        raise _alegra_http(exc)


@router.patch("/{customer_id}/active", response_model=CustomerOut)
def set_active(customer_id: UUID, body: CustomerActiveIn, db: Session = Depends(get_db)):
    """Local visibility only (never deletes, never touches Alegra)."""
    customer = _get(db, customer_id)
    customer.active = body.active
    db.commit(); db.refresh(customer)
    return customer
