"""PATRIMONIO — which value is the store's official merchandise value (migration 0028).

Two series that are NEVER mixed:
  * VALOR DECLARADO POR EL DUEÑO (PatrimonyDeclaration, origin MANUAL): what the owner states at a close —
    optional, never blocks it, can be added or corrected later with an audit trail (old / new / reason / when).
    Variation only between consecutive declarations; a jornada without declaration has no value (never invented).
  * VALOR CALCULADO POR EL POS (services/inventory_valuation.py): the inventory REGISTERED in the POS. Diagnostic,
    "NO VERIFICADA", while the official source is MANUAL.

The official source is MANUAL until someone explicitly switches it to AUTOMATIC (after a validated physical
inventory), with a reason, in PatrimonySourceChange (append-only). It never switches by itself. The change time
is the transition: automatic photos taken before it stay diagnostic, and no variation is computed across it.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.enums import InventoryMovementType
from app.models import (CashSession, InventoryMovement, PatrimonyDeclaration, PatrimonyDeclarationCorrection,
                        PatrimonySourceChange)
from app.services.pos import get_operator

CENT = Decimal("0.01")
SOURCES = {"MANUAL": "Valor declarado por el dueño", "AUTOMATIC": "Valoración automática del POS"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _value(raw) -> Decimal:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(422, "Escribe un valor numérico válido")
    if not value.is_finite() or value < 0:
        raise HTTPException(422, "El valor de la mercancía no puede ser negativo")
    return value.quantize(CENT)


# ---- official source --------------------------------------------------------------------------------

def official_source(db: Session, at: datetime | None = None) -> dict:
    """The official patrimony source at `at` (default now): MANUAL unless an explicit change says otherwise."""
    statement = select(PatrimonySourceChange).order_by(PatrimonySourceChange.created_at.desc())
    if at is not None:
        statement = statement.where(PatrimonySourceChange.created_at <= at)
    last = db.scalar(statement.limit(1))
    return {"source": last.source if last else "MANUAL", "label": SOURCES[last.source if last else "MANUAL"],
            "since": last.created_at if last else None}


def confirmed_physical_counts(db: Session) -> int:
    """Confirmed physical-count batches (each confirmation writes ADJUSTMENT rows sharing a reference_id)."""
    return db.scalar(select(func.count(func.distinct(InventoryMovement.reference_id))).where(
        InventoryMovement.movement_type == InventoryMovementType.ADJUSTMENT,
        InventoryMovement.reference_id.is_not(None))) or 0


def source_history(db: Session) -> list[dict]:
    return [{"id": c.id, "source": c.source, "label": SOURCES[c.source], "reason": c.reason,
             "performed_by_text": c.performed_by_text, "created_at": c.created_at}
            for c in db.scalars(select(PatrimonySourceChange).order_by(PatrimonySourceChange.created_at))]


def set_official_source(db: Session, source: str, reason: str, idempotency_key: str,
                        performed_by_text: str | None = None) -> dict:
    """Explicit, audited switch of the official source. AUTOMATIC requires a confirmed physical inventory in the
    POS (at least one confirmed count batch) and a reason; it never happens automatically."""
    if source not in SOURCES:
        raise HTTPException(422, "Fuente de patrimonio desconocida")
    reason = (reason or "").strip()
    if len(reason) < 10:
        raise HTTPException(422, "Explica el motivo del cambio (al menos 10 caracteres)")
    try:
        existing = db.scalar(select(PatrimonySourceChange).where(PatrimonySourceChange.idempotency_key == idempotency_key))
        if existing is not None:
            return official_source(db)
        current = official_source(db)["source"]
        if current == source:
            raise HTTPException(409, f"La fuente oficial ya es: {SOURCES[source]}")
        if source == "AUTOMATIC" and confirmed_physical_counts(db) == 0:
            raise HTTPException(409, "No hay un inventario físico confirmado en el POS: la valoración automática no "
                                     "puede ser la fuente oficial todavía")
        db.add(PatrimonySourceChange(source=source, reason=reason[:500], user_id=get_operator(db).id,
                                     performed_by_text=(performed_by_text or "").strip()[:120] or None,
                                     idempotency_key=idempotency_key, created_at=_now()))
        db.commit()
        return official_source(db)
    except Exception:
        db.rollback()
        raise


# ---- declared value (MANUAL) ------------------------------------------------------------------------

def declare(db: Session, session: CashSession, value, note: str | None = None,
            performed_by_text: str | None = None) -> PatrimonyDeclaration:
    """The owner's declared merchandise value for a CLOSED jornada (inside the close transaction or later). One per
    jornada; a mistake is corrected, never overwritten. Does not commit."""
    if session.closed_at is None:
        raise HTTPException(409, "El valor de la mercancía se declara al cerrar la jornada")
    amount = _value(value)
    if db.scalar(select(PatrimonyDeclaration).where(PatrimonyDeclaration.cash_session_id == session.id)):
        raise HTTPException(409, "Esta jornada ya tiene un valor declarado: usa \"Corregir valor declarado\"")
    declaration = PatrimonyDeclaration(cash_session_id=session.id, declared_value=amount, source="MANUAL",
                                       note=(note or "").strip()[:300] or None, user_id=get_operator(db).id,
                                       performed_by_text=(performed_by_text or "").strip()[:120] or None,
                                       declared_at=_now())
    db.add(declaration); db.flush()
    return declaration


def declare_after_close(db: Session, session_id: UUID, value, note: str | None = None,
                        performed_by_text: str | None = None) -> PatrimonyDeclaration:
    try:
        session = db.scalar(select(CashSession).where(CashSession.id == session_id).with_for_update())
        if session is None:
            raise HTTPException(404, "Jornada no encontrada")
        declaration = declare(db, session, value, note, performed_by_text)
        db.commit()
        return declaration
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Esta jornada ya tiene un valor declarado: usa \"Corregir valor declarado\"")
    except Exception:
        db.rollback()
        raise


def correct_declaration(db: Session, declaration_id: UUID, new_value, reason: str, idempotency_key: str) -> PatrimonyDeclaration:
    """Audited correction (old / new / reason / when); the declaration keeps its original value."""
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise HTTPException(422, "Escribe el motivo de la corrección")
    try:
        existing = db.scalar(select(PatrimonyDeclarationCorrection)
                             .where(PatrimonyDeclarationCorrection.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.declaration_id != declaration_id:
                raise HTTPException(409, "La clave de idempotencia ya se usó en otro valor")
            return db.get(PatrimonyDeclaration, declaration_id)
        declaration = db.scalar(select(PatrimonyDeclaration).where(PatrimonyDeclaration.id == declaration_id)
                                .with_for_update())
        if declaration is None:
            raise HTTPException(404, "Valor declarado no encontrado")
        amount = _value(new_value)
        current = declaration.effective_value
        if amount == current:
            raise HTTPException(422, "El valor corregido es igual al declarado")
        db.add(PatrimonyDeclarationCorrection(declaration_id=declaration.id, old_value=current, new_value=amount,
                                              reason=reason[:300], user_id=get_operator(db).id,
                                              idempotency_key=idempotency_key, created_at=_now()))
        db.commit()
        db.expire(declaration)
        return db.get(PatrimonyDeclaration, declaration_id)
    except Exception:
        db.rollback()
        raise


def _declaration_out(declaration: PatrimonyDeclaration) -> dict:
    return {"id": declaration.id, "cash_session_id": declaration.cash_session_id, "source": declaration.source,
            "declared_value": declaration.effective_value, "original_value": declaration.declared_value,
            "note": declaration.note, "performed_by_text": declaration.performed_by_text,
            "declared_at": declaration.declared_at,
            "corrections": [{"id": c.id, "old_value": c.old_value, "new_value": c.new_value, "reason": c.reason,
                             "created_at": c.created_at} for c in declaration.corrections]}


def _declarations_by_close(db: Session) -> list[tuple[PatrimonyDeclaration, CashSession]]:
    return db.execute(select(PatrimonyDeclaration, CashSession)
                      .join(CashSession, CashSession.id == PatrimonyDeclaration.cash_session_id)
                      .order_by(CashSession.closed_at)).all()


def _with_variation(rows, index: int) -> dict:
    """Variation vs the PREVIOUS DECLARATION (whatever days passed); days without a declaration add nothing."""
    from app.services.inventory_valuation import variation
    declaration, session = rows[index]
    previous = rows[index - 1] if index > 0 else None
    data = _declaration_out(declaration)
    return {**data, "closed_at": session.closed_at,
            **variation(data["declared_value"], previous[0].effective_value if previous else None),
            "previous_closed_at": previous[1].closed_at if previous else None, "is_first": previous is None}


def session_declaration(db: Session, session: CashSession) -> dict:
    rows = _declarations_by_close(db)
    index = next((i for i, (_, s) in enumerate(rows) if s.id == session.id), None)
    source = official_source(db)
    if index is None:
        last = rows[-1] if rows else None
        return {"cash_session_id": session.id, "status": "NOT_DECLARED", "official_source": source,
                "last_declared_value": last[0].effective_value if last else None,
                "last_declared_closed_at": last[1].closed_at if last else None}
    return {"cash_session_id": session.id, "status": "DECLARED", "official_source": source, **_with_variation(rows, index)}


def patrimony_history(db: Session, limit: int = 60) -> dict:
    """Both series side by side per closed jornada (newest first), never mixed: the owner's declared value with
    its own variation, and the POS registered value (diagnostic unless official at capture)."""
    from app.services.inventory_valuation import valuation_history
    rows = _declarations_by_close(db)
    declared = {s.id: _with_variation(rows, i) for i, (_, s) in enumerate(rows)}
    automatic = valuation_history(db, limit)
    return {"official_source": official_source(db), "source_changes": source_history(db),
            "confirmed_physical_counts": confirmed_physical_counts(db),
            "sessions": [{"cash_session_id": a["cash_session_id"], "opened_at": a["opened_at"], "closed_at": a["closed_at"],
                          "declared": declared.get(a["cash_session_id"]), "automatic": a} for a in automatic]}
