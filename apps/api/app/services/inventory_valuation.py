"""VALOR CALCULADO POR EL POS (migration 0028) — the inventory REGISTERED in the POS, at cost. Diagnostic.

    registered value = SUM over products with stock > 0 of  stock x Product.unit_cost

* stock = SUM(inventory_movements.quantity) — the ledger, as everywhere else (never a stored Product.stock).
* Product.unit_cost = the cost the POS uses today (receiving a purchase overwrites it; the product form edits it).
  It is NOT NULL (default 0), so "no cost" means unit_cost <= 0. No FIFO / weighted average is invented here.
* A product with stock and no cost is never valued at 0 silently: it is excluded and counted.

It is NOT the store's patrimony. The catalog was imported without quantities (most active products have no
registered stock and that does not mean they are physically missing), so while the official source is MANUAL
(services/patrimony.py) this value is "NO VERIFICADA": shown only as a diagnostic with its coverage, never as a
total, never as the official baseline. A photo is still frozen at every close (1 per cash session) with
`is_official` = whether AUTOMATIC was the official source at that moment; it is never recomputed.
"""
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import CashSession, InventoryMovement, InventoryValuationSnapshot, Product

ZERO = Decimal("0")
CENT = Decimal("0.01")
COST_SOURCE = "PRODUCT_UNIT_COST"
NOT_VERIFIED = "NO VERIFICADA"
MISSING_SAMPLE = 50


def _coverage_message(active_with_stock: int, active_catalog: int) -> str:
    fmt = lambda n: f"{n:,}".replace(",", ".")
    return (f"Solo {fmt(active_with_stock)} de {fmt(active_catalog)} productos activos tienen existencias registradas en el POS. "
            "Este valor es el inventario registrado, no la mercancía física de la tienda.")


def compute_valuation(db: Session) -> dict:
    """Live registered valuation from the ledger and the current costs. Read only."""
    stocks = (select(InventoryMovement.product_id.label("product_id"),
                     func.sum(InventoryMovement.quantity).label("stock"))
              .group_by(InventoryMovement.product_id).subquery())
    positive, has_cost = stocks.c.stock > 0, Product.unit_cost > 0
    valued = positive & has_cost
    row = db.execute(
        select(func.count().filter(positive),
               func.count().filter(valued),
               func.count().filter(positive & ~has_cost),
               func.count().filter(stocks.c.stock < 0),
               func.coalesce(func.sum(case((valued, stocks.c.stock * Product.unit_cost), else_=0)), 0),
               func.coalesce(func.sum(case((valued, stocks.c.stock), else_=0)), 0),
               func.coalesce(func.sum(case((positive & ~has_cost, stocks.c.stock), else_=0)), 0),
               func.coalesce(func.sum(case((valued & Product.active.is_(True), stocks.c.stock * Product.unit_cost), else_=0)), 0),
               func.count().filter(positive & Product.active.is_(True)))
        .select_from(stocks).join(Product, Product.id == stocks.c.product_id)).one()
    active_catalog = db.scalar(select(func.count()).select_from(Product).where(Product.active.is_(True))) or 0
    missing_rows = db.execute(
        select(Product.id, Product.name, Product.barcode, stocks.c.stock)
        .join(stocks, stocks.c.product_id == Product.id)
        .where(positive, ~has_cost).order_by(Product.name).limit(MISSING_SAMPLE)).all()
    value, active_value = Decimal(row[4]).quantize(CENT), Decimal(row[7]).quantize(CENT)
    missing, active_with_stock = int(row[2]), int(row[8])
    return {
        "inventory_value": value, "active_value": active_value, "inactive_value": value - active_value,
        "products_with_positive_stock": int(row[0]), "products_valued": int(row[1]),
        "products_missing_cost": missing, "products_negative_stock": int(row[3]),
        "quantity_valued": Decimal(row[5]), "quantity_missing_cost": Decimal(row[6]),
        "active_catalog_count": int(active_catalog), "active_with_stock": active_with_stock,
        "cost_source": COST_SOURCE,
        "complete": missing == 0,
        "coverage_message": _coverage_message(active_with_stock, int(active_catalog)),
        "missing_cost_products": [{"id": r[0], "name": r[1], "barcode": r[2], "stock": Decimal(r[3])}
                                  for r in missing_rows],
    }


def snapshot_for_session(db: Session, session: CashSession) -> InventoryValuationSnapshot:
    """Freezes the registered valuation for that session (idempotent). Called inside the close transaction."""
    from app.services.patrimony import official_source
    existing = db.scalar(select(InventoryValuationSnapshot)
                         .where(InventoryValuationSnapshot.cash_session_id == session.id))
    if existing is not None:
        return existing
    data = compute_valuation(db)
    snapshot = InventoryValuationSnapshot(
        cash_session_id=session.id, inventory_value=data["inventory_value"],
        products_with_positive_stock=data["products_with_positive_stock"], products_valued=data["products_valued"],
        products_missing_cost=data["products_missing_cost"], products_negative_stock=data["products_negative_stock"],
        quantity_valued=data["quantity_valued"], quantity_missing_cost=data["quantity_missing_cost"],
        cost_source=COST_SOURCE, active_value=data["active_value"], inactive_value=data["inactive_value"],
        active_catalog_count=data["active_catalog_count"], active_with_stock=data["active_with_stock"],
        is_official=official_source(db, session.closed_at)["source"] == "AUTOMATIC", captured_at=session.closed_at)
    db.add(snapshot); db.flush()
    return snapshot


def variation(current: Decimal, previous: Decimal | None) -> dict:
    """amount = current - previous; percent = amount / previous (None when there is no base or it is 0)."""
    if previous is None:
        return {"previous_value": None, "variation_amount": None, "variation_percent": None}
    amount = current - previous
    percent = (amount / previous * 100).quantize(CENT) if previous > 0 else None
    return {"previous_value": previous, "variation_amount": amount, "variation_percent": percent}


def snapshot_out(snapshot: InventoryValuationSnapshot) -> dict:
    return {"inventory_value": snapshot.inventory_value, "active_value": snapshot.active_value,
            "inactive_value": snapshot.inactive_value,
            "products_with_positive_stock": snapshot.products_with_positive_stock,
            "products_valued": snapshot.products_valued, "products_missing_cost": snapshot.products_missing_cost,
            "products_negative_stock": snapshot.products_negative_stock,
            "quantity_valued": snapshot.quantity_valued, "quantity_missing_cost": snapshot.quantity_missing_cost,
            "active_catalog_count": snapshot.active_catalog_count, "active_with_stock": snapshot.active_with_stock,
            "cost_source": snapshot.cost_source, "captured_at": snapshot.captured_at,
            "is_official": snapshot.is_official,
            "verification": "OFICIAL" if snapshot.is_official else NOT_VERIFIED,
            "complete": snapshot.products_missing_cost == 0,
            "coverage_message": _coverage_message(snapshot.active_with_stock, snapshot.active_catalog_count)}


def _snapshots_by_close(db: Session) -> list[tuple[InventoryValuationSnapshot, CashSession]]:
    return db.execute(select(InventoryValuationSnapshot, CashSession)
                      .join(CashSession, CashSession.id == InventoryValuationSnapshot.cash_session_id)
                      .order_by(CashSession.closed_at)).all()


def _previous_same_kind(snapshots, index: int):
    """Variation only between photos of the SAME kind (official vs diagnostic): never across a source change."""
    own = snapshots[index][0]
    for snap, _ in reversed(snapshots[:index]):
        if snap.is_official == own.is_official:
            return snap
    return None


def session_valuation(db: Session, session: CashSession) -> dict:
    """The registered (diagnostic) valuation of a session; the live preview while it is open."""
    from app.services.patrimony import official_source
    snapshots = _snapshots_by_close(db)
    if session.closed_at is None:
        data = compute_valuation(db)
        official = official_source(db)["source"] == "AUTOMATIC"
        previous = next((s for s, _ in reversed(snapshots) if s.is_official == official), None)
        return {"cash_session_id": session.id, "status": "LIVE", **data, "is_official": official,
                "verification": "OFICIAL" if official else NOT_VERIFIED,
                **variation(data["inventory_value"], previous.inventory_value if previous else None)}
    index = next((i for i, (snap, s) in enumerate(snapshots) if s.id == session.id), None)
    if index is None:
        return {"cash_session_id": session.id, "status": "NO_SNAPSHOT", "message": "Sin snapshot patrimonial"}
    own = snapshots[index][0]
    previous = _previous_same_kind(snapshots, index)
    return {"cash_session_id": session.id, "status": "SNAPSHOT", **snapshot_out(own),
            **variation(own.inventory_value, previous.inventory_value if previous else None),
            "is_baseline": previous is None}


def current_valuation(db: Session) -> dict:
    """GET current: live registered value now (diagnostic unless the official source is AUTOMATIC)."""
    from app.services.patrimony import official_source
    data = compute_valuation(db)
    official = official_source(db)["source"] == "AUTOMATIC"
    snapshots = _snapshots_by_close(db)
    previous = next(((s, c) for s, c in reversed(snapshots) if s.is_official == official), None)
    return {**data, "is_official": official, "verification": "OFICIAL" if official else NOT_VERIFIED,
            **variation(data["inventory_value"], previous[0].inventory_value if previous else None),
            "previous_closed_at": previous[1].closed_at if previous else None}


def valuation_history(db: Session, limit: int = 60) -> list[dict]:
    """The last `limit` closed jornadas, newest first, with their registered (diagnostic) photo; variation only
    between photos of the same kind. Jornadas closed before 0028 say "Sin snapshot patrimonial" (never $0)."""
    sessions = db.scalars(select(CashSession).where(CashSession.closed_at.is_not(None))
                          .order_by(CashSession.closed_at.desc()).limit(limit)).all()
    snapshots = _snapshots_by_close(db)
    by_session = {s.id: i for i, (snap, s) in enumerate(snapshots)}
    result = []
    for session in sessions:
        base = {"cash_session_id": session.id, "opened_at": session.opened_at, "closed_at": session.closed_at}
        if session.id not in by_session:
            result.append({**base, "status": "NO_SNAPSHOT", "inventory_value": None})
            continue
        index = by_session[session.id]
        snap = snapshots[index][0]
        previous = _previous_same_kind(snapshots, index)
        result.append({**base, "status": "SNAPSHOT", **snapshot_out(snap),
                       **variation(snap.inventory_value, previous.inventory_value if previous else None),
                       "is_baseline": previous is None})
    return result
