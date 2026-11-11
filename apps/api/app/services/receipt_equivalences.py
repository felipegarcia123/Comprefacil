from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product, ReceiptEquivalence
from app.schemas.receipt_ai import (ReceiptEquivalenceActive,
                                    ReceiptEquivalenceCreate,
                                    ReceiptEquivalenceOut)
from app.services.pos import get_operator


COCA_COLA_PROVIDER = "COCA-COLA"
_COCA_COLA_ALIASES = {
    "coca cola": COCA_COLA_PROVIDER,
    "coca-cola": COCA_COLA_PROVIDER,
    "coca cola company": COCA_COLA_PROVIDER,
}


def _provider(value: str) -> str:
    normalized = " ".join((value or "").strip().split()).casefold()
    return _COCA_COLA_ALIASES.get(normalized, normalized.upper())


def _require_coca_cola(value: str) -> str:
    provider = _provider(value)
    if provider != COCA_COLA_PROVIDER:
        raise HTTPException(422, "En esta fase solo se permiten equivalencias del proveedor Coca-Cola")
    return provider


def _code(value: str) -> str:
    return (value or "").strip()


def _out(row: ReceiptEquivalence, product: Product | None = None) -> ReceiptEquivalenceOut:
    return ReceiptEquivalenceOut(
        id=str(row.id), provider=row.provider, material_code=row.material_code,
        product_id=str(row.product_id), product_name=(product.name if product else row.product_name_snapshot),
        source_description=row.source_description, source_brand=row.source_brand,
        source_variant=row.source_variant, source_size=row.source_size,
        confirmation_method=row.confirmation_method, confirmed_by=str(row.confirmed_by),
        confirmed_at=row.confirmed_at.isoformat(), active=row.active,
        deactivated_at=row.deactivated_at.isoformat() if row.deactivated_at else None)


def list_equivalences(db: Session, provider: str | None = None, material_code: str | None = None,
                      active: bool | None = True) -> list[ReceiptEquivalenceOut]:
    stmt = select(ReceiptEquivalence, Product).outerjoin(Product, Product.id == ReceiptEquivalence.product_id)
    if provider:
        stmt = stmt.where(ReceiptEquivalence.provider == _provider(provider))
    if material_code:
        stmt = stmt.where(ReceiptEquivalence.material_code == _code(material_code))
    if active is not None:
        stmt = stmt.where(ReceiptEquivalence.active.is_(active))
    return [_out(row, product) for row, product in db.execute(stmt.order_by(ReceiptEquivalence.updated_at.desc())).all()]


def active_equivalence(db: Session, provider: str | None, material_code: str | None) -> ReceiptEquivalence | None:
    if not provider or not material_code:
        return None
    return db.scalar(select(ReceiptEquivalence).where(
        ReceiptEquivalence.provider == _provider(provider),
        ReceiptEquivalence.material_code == _code(material_code),
        ReceiptEquivalence.active.is_(True)))


def confirm_equivalence(db: Session, body: ReceiptEquivalenceCreate) -> ReceiptEquivalenceOut:
    provider, code = _require_coca_cola(body.provider), _code(body.material_code)
    if not code:
        raise HTTPException(422, "El código de material es obligatorio")
    try:
        product_id = UUID(body.product_id)
    except ValueError as exc:
        raise HTTPException(422, "Producto interno inválido") from exc
    product = db.scalar(select(Product).where(Product.id == product_id).with_for_update(of=Product))
    if not product:
        raise HTTPException(404, "Producto interno no encontrado")
    if not product.active:
        raise HTTPException(409, "No se puede asociar un producto inactivo")
    existing = active_equivalence(db, provider, code)
    if existing:
        if existing.product_id == product.id:
            return _out(existing, product)
        raise HTTPException(409, "El código del proveedor ya tiene una equivalencia activa; desactívela antes de corregirla")
    row = ReceiptEquivalence(
        id=uuid4(), provider=provider, material_code=code, product_id=product.id,
        product_name_snapshot=product.name, source_description=body.source_description,
        source_brand=body.source_brand, source_variant=body.source_variant, source_size=body.source_size,
        confirmation_method="manual", confirmed_by=get_operator(db).id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _out(row, product)


def set_equivalence_active(db: Session, equivalence_id: UUID, body: ReceiptEquivalenceActive) -> ReceiptEquivalenceOut:
    row = db.scalar(select(ReceiptEquivalence).where(ReceiptEquivalence.id == equivalence_id).with_for_update())
    if not row:
        raise HTTPException(404, "Equivalencia no encontrada")
    if row.provider != COCA_COLA_PROVIDER:
        raise HTTPException(422, "Solo se permiten equivalencias del proveedor Coca-Cola")
    operator = get_operator(db)
    if body.active:
        product = db.scalar(select(Product).where(Product.id == row.product_id).with_for_update(of=Product))
        if not product or not product.active:
            raise HTTPException(409, "No se puede activar una equivalencia cuyo producto está inactivo")
        other = db.scalar(select(ReceiptEquivalence).where(
            ReceiptEquivalence.provider == row.provider, ReceiptEquivalence.material_code == row.material_code,
            ReceiptEquivalence.active.is_(True), ReceiptEquivalence.id != row.id))
        if other:
            raise HTTPException(409, "Ya existe otra equivalencia activa para este código")
        row.active = True
        row.deactivated_at = None
        row.deactivated_by = None
    else:
        row.active = False
        row.deactivated_at = datetime.now(timezone.utc)
        row.deactivated_by = operator.id
    db.commit()
    db.refresh(row)
    product = db.get(Product, row.product_id)
    return _out(row, product)


def replace_equivalence(db: Session, equivalence_id: UUID, body: ReceiptEquivalenceCreate) -> ReceiptEquivalenceOut:
    row = db.scalar(select(ReceiptEquivalence).where(ReceiptEquivalence.id == equivalence_id).with_for_update())
    if not row:
        raise HTTPException(404, "Equivalencia no encontrada")
    if row.provider != COCA_COLA_PROVIDER:
        raise HTTPException(422, "Solo se permiten equivalencias del proveedor Coca-Cola")
    try:
        product_id = UUID(body.product_id)
    except ValueError as exc:
        raise HTTPException(422, "Producto interno inválido") from exc
    product = db.scalar(select(Product).where(Product.id == product_id).with_for_update(of=Product))
    if not product or not product.active:
        raise HTTPException(409, "No se puede asociar un producto inactivo o inexistente")
    provider, code = _require_coca_cola(body.provider), _code(body.material_code)
    if not code:
        raise HTTPException(422, "El código de material es obligatorio")
    other = active_equivalence(db, provider, code)
    if other and other.id != row.id:
        raise HTTPException(409, "El código ya tiene otra equivalencia activa")
    operator = get_operator(db)
    row.active = False
    row.deactivated_at = datetime.now(timezone.utc)
    row.deactivated_by = operator.id
    replacement = ReceiptEquivalence(
        id=uuid4(), provider=provider, material_code=code, product_id=product.id,
        product_name_snapshot=product.name, source_description=body.source_description,
        source_brand=body.source_brand, source_variant=body.source_variant, source_size=body.source_size,
        confirmation_method="manual-replacement", confirmed_by=operator.id)
    db.add(replacement)
    db.commit()
    db.refresh(replacement)
    return _out(replacement, product)
