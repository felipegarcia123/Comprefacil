"""POS <-> Alegra catalog sync — the single flow (approved 2026-09-28):

  Product -> has alegra_item_id? -> search Alegra -> LINK if it exists
          -> READY_TO_CREATE if not -> controlled CREATE (disabled) -> save alegra_item_id

POS owns catalog, stock and operating prices; Alegra is the fiscal representation.
Automatic matches are ONLY: (A) exact reference, (B) UPC-A <-> EAN-13 with one leading
zero — always unambiguous. A similar name is a review hint, never a match.

Writes: LINK is local (products.alegra_item_id + audit row). CREATE POSTs /items behind
ALEGRA_CATALOG_CREATE_ENABLED (off). Since 2026-10-02 the normal path is on demand at
invoicing time (ensure_alegra_item / ensure_sale_items); the panel and batch are diagnosis
and manual tools over the same function. Previews are never audited.
"""
import logging
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import CatalogSyncOperation, CatalogSyncRun, Product
from app.services.alegra_client import (AlegraAuthError, AlegraError, AlegraNotConfigured, AlegraRateLimitError,
                                        AlegraValidationError)
from app.services.alegra_item_mapping import fetch_all_items, is_valid_gtin
from app.services.alegra_provider import AlegraProvider
from app.services.barcodes import alternate_barcode

logger = logging.getLogger(__name__)

# ---- vocabulary -------------------------------------------------------------------------------------------------
ALREADY_LINKED, EXACT_MATCH, UPC_EAN_MATCH = "ALREADY_LINKED", "EXACT_MATCH", "UPC_EAN_MATCH"
READY_TO_CREATE, NO_BARCODE, REVIEW_REQUIRED = "READY_TO_CREATE", "NO_BARCODE", "REVIEW_REQUIRED"
CONFLICT, INVALID_BARCODE, ERROR = "CONFLICT", "INVALID_BARCODE", "ERROR"
LINKED, CREATED = "LINKED", "CREATED"                 # executed operations
ACTION = {ALREADY_LINKED: "NONE", EXACT_MATCH: "LINK", UPC_EAN_MATCH: "LINK", READY_TO_CREATE: "CREATE"}
STATUSES = (ALREADY_LINKED, EXACT_MATCH, UPC_EAN_MATCH, READY_TO_CREATE, NO_BARCODE, REVIEW_REQUIRED, CONFLICT,
            INVALID_BARCODE, ERROR)
DEFAULT_CATEGORY_ID = "5148"                           # "Ventas": used by all 2.988 current items
DEFINITIVE_CREATE_ERRORS = (AlegraValidationError, AlegraAuthError, AlegraRateLimitError, AlegraNotConfigured)
STALE_RUN = timedelta(minutes=20)

# Reverse flow: Alegra items sold historically with no POS product. Definitive cost and
# price confirmed by the owner (2026-09-29); barcode NULL (Alegra's 901001... references
# are internal codes, not EANs); stock 0 (set later by purchase / adjustment / count).
PRIORITY_POS_PRODUCTS = [
    {"alegra_item_id": "98", "name": "HUEVO AA X UND", "unit_cost": 600, "sale_price": 700, "category_code": "SUPER"},
    {"alegra_item_id": "27", "name": "AREPA SIN SAL", "unit_cost": 300, "sale_price": 1000, "category_code": "SUPER"},
    {"alegra_item_id": "46", "name": "HUEVO AA X 30 UND", "unit_cost": 17000, "sale_price": 18000, "category_code": "SUPER"},
    {"alegra_item_id": "198", "name": "SALCHICHA MANGUERA", "unit_cost": 1950, "sale_price": 3000, "category_code": "SUPER"},
    {"alegra_item_id": "1621", "name": "CIGARRILLO ROTHMANS AZUL", "unit_cost": 1200, "sale_price": 1300, "category_code": "CIGARROS"},
    {"alegra_item_id": "1659", "name": "CLAVOS DE OLOR", "unit_cost": 800, "sale_price": 900, "category_code": "SUPER"},
    {"alegra_item_id": "150", "name": "CIGARRILLO MARLBORO", "unit_cost": 1400, "sale_price": 2000, "category_code": "CIGARROS"},
]


class CatalogSyncError(Exception):
    """Refused operation (limits, barriers, invalid request). `status` = HTTP code."""
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


# ---- remote catalog index -----------------------------------------------------------------------------------------

@dataclass
class CatalogIndex:
    """Alegra items indexed by id and by exact reference. Built only from a COMPLETE scan
    (fetch_all_items aborts when unique ids != remote total)."""
    items_by_id: dict[str, dict] = field(default_factory=dict)
    by_reference: dict[str, list[dict]] = field(default_factory=lambda: defaultdict(list))
    by_name: dict[str, list[dict]] = field(default_factory=lambda: defaultdict(list))

    @property
    def total(self) -> int:
        return len(self.items_by_id)


def normalize_name(name: str | None) -> str:
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().upper()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9]+", " ", text)).strip()


def build_index(items: list[dict]) -> CatalogIndex:
    index = CatalogIndex()
    for item in items:
        item_id = str(item.get("id"))
        if item_id in index.items_by_id:
            continue                                    # dedupe by remote id
        index.items_by_id[item_id] = item
        reference = str(item.get("reference") or "").strip()
        if reference:
            index.by_reference[reference].append(item)
        index.by_name[normalize_name(item.get("name"))].append(item)
    return index


def fetch_index(provider: AlegraProvider | None = None) -> CatalogIndex:
    """Full paginated scan with stable order (order_field=id). Raises AlegraError on an
    inconsistent read (unique ids != total): an incomplete catalog is never analysed."""
    return build_index(fetch_all_items(provider or AlegraProvider()))


# ---- per-product preview (pure) ---------------------------------------------------------------------------------

def _row(product: Product) -> dict:
    return {"product_id": str(product.id), "barcode": product.barcode, "product_name": product.name,
            "sale_price": str(product.sale_price), "current_alegra_item_id": product.alegra_item_id,
            "matched_alegra_item_id": None, "matched_alegra_name": None, "matched_reference": None,
            "sync_status": None, "confidence": "NONE", "reason": "", "proposed_action": "REVIEW", "review_hint": None}


def _set(row: dict, status: str, reason: str = "", item: dict | None = None, confidence: str = "NONE") -> dict:
    row.update(sync_status=status, reason=reason, confidence=confidence, proposed_action=ACTION.get(status, "REVIEW"))
    if item is not None:
        row.update(matched_alegra_item_id=str(item.get("id")), matched_alegra_name=item.get("name"),
                   matched_reference=str(item.get("reference") or "").strip() or None)
    return row


def remote_matches(barcode: str, index: CatalogIndex) -> list[tuple[dict, str]]:
    """(item, EXACT_MATCH|UPC_EAN_MATCH) for rule A and rule B only."""
    found = [(item, EXACT_MATCH) for item in index.by_reference.get(barcode, [])]
    twin = alternate_barcode(barcode)
    if twin:
        found += [(item, UPC_EAN_MATCH) for item in index.by_reference.get(twin, [])]
    return found


def preview_product(product: Product, index: CatalogIndex, linked_by_item: dict[str, str]) -> dict:
    """Status of one product against the remote catalog. `linked_by_item`: alegra_item_id
    -> product_id already holding it in the POS."""
    row = _row(product)
    if product.alegra_item_id:
        item = index.items_by_id.get(product.alegra_item_id)
        if item is None:
            return _set(row, REVIEW_REQUIRED, "Vinculado a un ítem que ya no existe en Alegra")
        return _set(row, ALREADY_LINKED, "Ya vinculado", item, "HIGH")
    barcode = (product.barcode or "").strip()
    name_twins = index.by_name.get(normalize_name(product.name), [])
    if name_twins:
        row["review_hint"] = "Nombre igual en Alegra: " + ", ".join(f"{i.get('id')} {i.get('name')} ({i.get('reference') or 'sin ref.'})"
                                                                    for i in name_twins[:3])
    if not barcode:
        return _set(row, NO_BARCODE, "Sin código de barras: al facturar se crea con la referencia interna "
                                     f"{POS_REFERENCE_PREFIX}… derivada del producto")
    matches = remote_matches(barcode, index)
    if len({str(i.get("id")) for i, _ in matches}) > 1:
        return _set(row, REVIEW_REQUIRED, "Varios ítems de Alegra con ese código: " +
                    ", ".join(sorted({str(i.get('id')) for i, _ in matches})))
    if matches:
        item, kind = matches[0]
        holder = linked_by_item.get(str(item.get("id")))
        if holder and holder != str(product.id):
            return _set(row, CONFLICT, "El ítem ya está vinculado a otro producto del POS", item)
        if (item.get("status") or "active") != "active":
            return _set(row, REVIEW_REQUIRED, "El ítem coincidente está inactivo en Alegra", item)
        return _set(row, kind, "Referencia idéntica" if kind == EXACT_MATCH else "Equivalencia UPC-A/EAN-13",
                    item, "HIGH" if kind == EXACT_MATCH else "MEDIUM")
    if not is_valid_gtin(barcode):
        return _set(row, INVALID_BARCODE, "El código no es un EAN/UPC válido y no existe en Alegra")
    stripped = barcode.lstrip("0")
    zeros = [items[0] for ref, items in index.by_reference.items() if ref.isdigit() and ref.lstrip("0") == stripped]
    if zeros:
        return _set(row, REVIEW_REQUIRED, f"Alegra tiene el ítem {zeros[0].get('id')} con el mismo código salvo "
                                          f"ceros iniciales ({zeros[0].get('reference')})", zeros[0])
    if product.sale_price is None or product.sale_price <= 0:
        return _set(row, REVIEW_REQUIRED, "Precio de venta en cero o vacío")
    if len((product.name or "").strip()) > 150 or "�" in (product.name or ""):
        return _set(row, REVIEW_REQUIRED, "Nombre vacío, dañado o de más de 150 caracteres")
    if name_twins:
        return _set(row, REVIEW_REQUIRED, "Ya hay en Alegra un ítem con el mismo nombre y otro código")
    return _set(row, READY_TO_CREATE, "No existe en Alegra")


def preview_all(db: Session, index: CatalogIndex) -> dict:
    products = db.scalars(select(Product).where(Product.active.is_(True)).order_by(Product.name)).all()
    linked_by_item = {p.alegra_item_id: str(p.id) for p in db.scalars(
        select(Product).where(Product.alegra_item_id.is_not(None)))}
    rows = [preview_product(p, index, linked_by_item) for p in products]
    # Two POS products claiming the same remote item: neither is linked automatically.
    claims = Counter(r["matched_alegra_item_id"] for r in rows if r["proposed_action"] == "LINK")
    for row in rows:
        if row["proposed_action"] == "LINK" and claims[row["matched_alegra_item_id"]] > 1:
            _set(row, CONFLICT, f"El ítem {row['matched_alegra_item_id']} coincide con {claims[row['matched_alegra_item_id']]} "
                                "productos del POS")
    matched_items = {r["matched_alegra_item_id"] for r in rows if r["matched_alegra_item_id"]} | set(linked_by_item)
    alegra_only = [{"alegra_item_id": i, "name": item.get("name"), "reference": item.get("reference")}
                   for i, item in index.items_by_id.items() if i not in matched_items]
    return {"rows": rows, "summary": summarize(rows, index.total, alegra_only), "alegra_only": alegra_only}


def summarize(rows: list[dict], remote_items: int, alegra_only: list[dict]) -> dict:
    counts = Counter(r["sync_status"] for r in rows)
    return {
        "active": len(rows), "linked": counts[ALREADY_LINKED], "unlinked": len(rows) - counts[ALREADY_LINKED],
        "exact_match": counts[EXACT_MATCH] + counts[UPC_EAN_MATCH], "ready_to_create": counts[READY_TO_CREATE],
        "review": counts[REVIEW_REQUIRED], "no_barcode": counts[NO_BARCODE], "conflicts": counts[CONFLICT],
        "invalid_barcode": counts[INVALID_BARCODE], "errors": counts[ERROR],
        "by_status": {s: counts[s] for s in STATUSES}, "remote_items": remote_items,
        "alegra_items_without_pos": len(alegra_only),
    }


# ---- live remote search ---------------------------------------------------------------------------------------------

def find_remote_match(barcode: str, provider: AlegraProvider) -> list[tuple[dict, str]]:
    """Live search right before writing. Alegra's `reference` filter is a SUBSTRING match,
    so results are filtered locally to rule A/B only. Raises AlegraError when it can't search."""
    candidates = [barcode] + ([alternate_barcode(barcode)] if alternate_barcode(barcode) else [])
    items: dict[str, dict] = {}
    for code in candidates:
        response = provider.client.get("/items", params={"reference": code, "limit": 30})
        for item in response if isinstance(response, list) else (response or {}).get("data", []):
            items[str(item.get("id"))] = item
    return remote_matches(barcode, build_index(list(items.values())))


# ---- LINK (local write) ----------------------------------------------------------------------------------------------

def _audit(db: Session, product_id, action: str, status: str, old: str | None, new: str | None,
           reference: str | None, error: str | None = None) -> None:
    db.add(CatalogSyncOperation(product_id=product_id, action=action, status=status, old_alegra_item_id=old,
                                new_alegra_item_id=new, reference=reference, error=(error or None) and error[:500]))


def link_existing(db: Session, product_id: UUID, alegra_item_id: str, provider: AlegraProvider | None = None,
                  dry_run: bool = True) -> dict:
    """Links one product to an EXISTING Alegra item after re-validating live: product
    still unlinked, remote item still exists and active, reference still matches (rule A/B),
    no other product holds the item. Idempotent: same link -> ALREADY_LINKED (skip)."""
    provider = provider or AlegraProvider()
    product = db.scalar(select(Product).where(Product.id == product_id).with_for_update(of=Product))
    result = {"product_id": str(product_id), "action": "LINK", "alegra_item_id": alegra_item_id, "dry_run": dry_run}
    if product is None:
        return {**result, "status": ERROR, "reason": "Producto no encontrado"}
    if product.alegra_item_id == alegra_item_id:
        return {**result, "status": ALREADY_LINKED, "reason": "Ya vinculado a ese ítem"}
    if product.alegra_item_id:
        return _link_refused(db, product, alegra_item_id, CONFLICT, "El producto ya está vinculado a otro ítem", dry_run, result)
    holder = db.scalar(select(Product.id).where(Product.alegra_item_id == alegra_item_id))
    if holder:
        return _link_refused(db, product, alegra_item_id, CONFLICT, "El ítem ya está vinculado a otro producto", dry_run, result)
    barcode = (product.barcode or "").strip()
    if not barcode:
        return {**result, "status": NO_BARCODE, "reason": "Sin código: el vínculo automático exige referencia"}
    try:
        item = provider.client.get(f"/items/{alegra_item_id}")
    except AlegraError as exc:
        return {**result, "status": ERROR, "reason": f"No se pudo verificar el ítem en Alegra: {exc}"}
    if not item or (item.get("status") or "active") != "active":
        return {**result, "status": REVIEW_REQUIRED, "reason": "El ítem ya no existe o está inactivo en Alegra"}
    reference = str(item.get("reference") or "").strip()
    if reference not in (barcode, alternate_barcode(barcode)):
        return {**result, "status": REVIEW_REQUIRED, "reason": f"La referencia remota ({reference or 'vacía'}) ya no coincide"}
    if dry_run:
        db.rollback()
        return {**result, "status": EXACT_MATCH if reference == barcode else UPC_EAN_MATCH,
                "reason": "Se vincularía (simulación)"}
    old = product.alegra_item_id
    product.alegra_item_id = alegra_item_id
    try:
        _audit(db, product.id, "LINK", LINKED, old, alegra_item_id, barcode)
        db.commit()
    except IntegrityError:
        # uq_products_alegra_item_id: another product linked it concurrently.
        db.rollback()
        _audit(db, product_id, "LINK", CONFLICT, None, alegra_item_id, barcode, "Vinculado por otro proceso al mismo tiempo")
        db.commit()
        return {**result, "status": CONFLICT, "reason": "Otro producto quedó vinculado a ese ítem al mismo tiempo"}
    logger.info("catalog_link product_id=%s alegra_item_id=%s", product_id, alegra_item_id)
    return {**result, "status": LINKED, "reason": "Vinculado"}


def _link_refused(db, product, item_id, status, reason, dry_run, result):
    if not dry_run:
        _audit(db, product.id, "LINK", status, product.alegra_item_id, item_id, product.barcode, reason)
        db.commit()
    else:
        db.rollback()
    return {**result, "status": status, "reason": reason}


# ---- ENSURE ITEM: link or create on demand, at invoicing time -------------------------------------------------
#
# Strategy approved 2026-10-02 ("Punto Red" experience): no prior mass sync. When a sale is
# invoiced, every product without alegra_item_id is resolved right there, invisibly for the
# cashier: exact reference search -> LINK, or POST /items -> save the id. CREATE stays behind
# ALEGRA_CATALOG_CREATE_ENABLED; while it is off the flow runs up to the POST and stops.

PENDING = "PENDING"                    # remote outcome unknown: invoice waits, retry reconciles first
UNCERTAIN = "UNCERTAIN"                # audit status of a POST /items whose outcome is unknown
UNCERTAIN_COOLDOWN = timedelta(minutes=10)
POS_REFERENCE_PREFIX = "POS-"
RESOLVED = (ALREADY_LINKED, LINKED, CREATED)


def item_reference(product: Product) -> str:
    """The Alegra `reference` of a product: its barcode, or — without barcode — a stable
    internal code derived from Product.id (never from the name). Same product, same code,
    always: it is recomputed, never stored, and it is what reconciliation searches for."""
    barcode = (product.barcode or "").strip()
    return barcode or f"{POS_REFERENCE_PREFIX}{product.id.hex[:16].upper()}"


UNIT_PRODUCT = "unidad"     # Product.unit of products sold by unit -> Alegra inventory.unit "unit"


def prepare_create(product: Product, category_id: str = DEFAULT_CATEGORY_ID) -> dict:
    """POST /items body (minimal, validated against the account). inventory carries ONLY the
    unit of measure: the DIAN requires it to stamp (code 3061; verified with ERG4622, item 2989
    unit="unit"). Never unitCost / initialQuantity / availableQuantity / warehouses: the POS
    owns stock and cost. tax [] like every current item (current fiscal policy), price =
    operating price (each invoice line sends the real sale price anyway). Only for products
    sold by unit; ensure_alegra_item refuses to create any other unit."""
    return {"name": re.sub(r"\s+", " ", product.name).strip()[:150], "reference": item_reference(product),
            "type": "product", "price": [{"idPriceList": "1", "price": float(product.sale_price)}], "tax": [],
            "category": {"id": category_id}, "inventory": {"unit": "unit"}}


def _lock_product(db: Session, product_id: UUID) -> Product | None:
    """Row lock for the whole search -> POST -> save sequence: two cashiers (or two retries)
    invoicing the same new product serialize here; the second one finds the id already saved."""
    # of=Product: Product.operational_category is eagerly LEFT JOINed and PostgreSQL refuses
    # FOR UPDATE on the nullable side of an outer join; lock only the products row.
    return db.scalar(select(Product).where(Product.id == product_id).with_for_update(of=Product)
                     .execution_options(populate_existing=True))


def _recent_uncertain_create(db: Session, product_id: UUID) -> bool:
    last = db.scalar(select(CatalogSyncOperation).where(CatalogSyncOperation.product_id == product_id,
                                                        CatalogSyncOperation.action == "CREATE")
                     .order_by(CatalogSyncOperation.created_at.desc()).limit(1))
    return bool(last and last.status == UNCERTAIN and
                _aware(last.created_at) > datetime.now(timezone.utc) - UNCERTAIN_COOLDOWN)


def _save_link(db: Session, product: Product, item_id: str, reference: str, action: str, status: str,
               result: dict) -> dict:
    product_id = product.id
    product.alegra_item_id = item_id
    try:
        _audit(db, product_id, action, status, None, item_id, reference)
        db.commit()
    except IntegrityError:
        # uq_products_alegra_item_id: another product took this item at the same moment.
        db.rollback()
        _audit(db, product_id, action, CONFLICT, None, item_id, reference, "Ítem tomado por otro producto al mismo tiempo")
        db.commit()
        return {**result, "status": CONFLICT, "reason": "El ítem de Alegra quedó vinculado a otro producto"}
    logger.info("catalog_%s product_id=%s alegra_item_id=%s", status.lower(), product_id, item_id)
    return {**result, "status": status, "alegra_item_id": item_id,
            "reason": "Creado en Alegra y vinculado" if status == CREATED else "Vinculado a un ítem existente"}


def _match(db: Session, product: Product, reference: str, provider: AlegraProvider, result: dict) -> dict | None:
    """Exact-reference search used before a POST and to reconcile an ambiguous one.
    None = searched and absent; {"item_id"} = one valid item; {"status"...} = refused.
    Raises AlegraError when it cannot search."""
    matches = find_remote_match(reference, provider)
    ids = sorted({str(i.get("id")) for i, _ in matches})
    if not ids:
        return None
    if len(ids) > 1:
        db.rollback()
        return {**result, "status": REVIEW_REQUIRED, "reason": "Varios ítems de Alegra con esa referencia: " + ", ".join(ids)}
    found = matches[0][0]
    if (found.get("status") or "active") != "active":
        db.rollback()
        return {**result, "status": REVIEW_REQUIRED, "reason": f"El ítem {ids[0]} de Alegra está inactivo"}
    holder = db.scalar(select(Product.id).where(Product.alegra_item_id == ids[0], Product.id != product.id))
    if holder:
        db.rollback()
        return {**result, "status": CONFLICT, "reason": f"El ítem {ids[0]} ya está vinculado a otro producto"}
    return {"item_id": ids[0]}


def ensure_alegra_item(db: Session, product_id: UUID, provider: AlegraProvider | None = None,
                       dry_run: bool = False) -> dict:
    """Idempotent: once it returns ALREADY_LINKED / LINKED / CREATED the product has an
    alegra_item_id and nothing remote is ever done for it again.

      linked            -> ALREADY_LINKED, zero I/O
      exact reference   -> LINKED (local write, GET only)
      absent            -> POST /items (flag on, not dry_run) -> CREATED
                           flag off / dry_run -> READY_TO_CREATE with the exact payload, no POST
      POST ambiguous    -> search again: found -> LINKED; else PENDING (never re-POSTs blindly;
                           a later attempt POSTs only after UNCERTAIN_COOLDOWN and a fresh
                           search proving it still does not exist)
      can't search      -> PENDING;  rejected -> ERROR;  ambiguous match / inactive -> REVIEW_REQUIRED;
      item held by another product -> CONFLICT.  dry_run never writes (not even a LINK)."""
    provider = provider or AlegraProvider()
    product = _lock_product(db, product_id)
    result = {"product_id": str(product_id), "action": "ENSURE", "dry_run": dry_run}
    if product is None:
        db.rollback()
        return {**result, "status": ERROR, "reason": "Producto no encontrado"}
    reference = item_reference(product)
    result.update(product_name=product.name, reference=reference)
    if product.alegra_item_id:
        db.rollback()                                  # releases the lock; nothing to do
        return {**result, "status": ALREADY_LINKED, "alegra_item_id": product.alegra_item_id, "reason": "Ya vinculado"}
    if product.sale_price is None or product.sale_price <= 0:
        db.rollback()
        return {**result, "status": REVIEW_REQUIRED, "reason": "Precio de venta en cero"}

    try:
        match = _match(db, product, reference, provider, result)
    except AlegraError as exc:
        db.rollback()
        return {**result, "status": PENDING, "reason": f"No se pudo consultar Alegra: {exc}"}
    if match and "status" in match:
        return match
    if match:
        if dry_run:
            db.rollback()
            return {**result, "status": EXACT_MATCH, "alegra_item_id": match["item_id"], "reason": "Se vincularía"}
        return _save_link(db, product, match["item_id"], reference, "LINK", LINKED, result)

    if (product.unit or UNIT_PRODUCT).strip().lower() != UNIT_PRODUCT:
        # e.g. kg: its Alegra unit of measure is not defined yet; never guessed.
        db.rollback()
        return {**result, "status": REVIEW_REQUIRED,
                "reason": f"Unidad '{product.unit}' sin equivalencia definida en Alegra; no se crea automáticamente"}
    payload = prepare_create(product)
    if dry_run or not settings.alegra_catalog_create_enabled:
        db.rollback()
        return {**result, "status": READY_TO_CREATE, "payload": payload,
                "reason": "Simulación: se crearía en Alegra" if dry_run else "Creación automática en Alegra deshabilitada"}
    if _recent_uncertain_create(db, product.id):
        db.rollback()
        return {**result, "status": PENDING, "reason": "Creación anterior sin confirmar; esperando a que aparezca en Alegra"}

    try:
        created = provider.create_item(payload)
    except DEFINITIVE_CREATE_ERRORS as exc:
        _audit(db, product.id, "CREATE", ERROR, None, None, reference, str(exc)); db.commit()
        return {**result, "status": ERROR, "reason": f"Alegra rechazó la creación del ítem: {exc}"}
    except AlegraError as exc:
        ambiguous = str(exc) or type(exc).__name__
    else:
        if (created or {}).get("id") not in (None, ""):
            return _save_link(db, product, str(created["id"]), reference, "CREATE", CREATED, result)
        ambiguous = "respuesta sin id"

    # Ambiguous: Alegra may or may not have created it. Search; never re-POST here.
    try:
        after = _match(db, product, reference, provider, result)
        detail = f"Resultado incierto ({ambiguous}); el ítem no aparece todavía en Alegra"
    except AlegraError as search_exc:
        after = None
        detail = f"Resultado incierto ({ambiguous}) y no se pudo verificar ({search_exc})"
    if after and "status" in after:
        return after
    if after:
        return _save_link(db, product, after["item_id"], reference, "CREATE", LINKED, result)
    _audit(db, product.id, "CREATE", UNCERTAIN, None, None, reference, detail); db.commit()
    return {**result, "status": PENDING, "reason": detail}


def ensure_sale_items(db: Session, sale, provider: AlegraProvider | None = None, dry_run: bool = False) -> dict:
    """ensure_alegra_item for every catalog product of a sale that still lacks a mapping,
    once per product (two lines of the same product -> at most one creation). Manual lines
    (GRANEL/BULTO) use their configured generic item and are not touched here.
    status: READY (all resolved) | PENDING (only transient problems: retry) | BLOCKED."""
    pending = list(dict.fromkeys(item.product_id for item in sale.items
                                 if item.product_id is not None and not (item.product and item.product.alegra_item_id)))
    provider = provider or AlegraProvider() if pending else provider
    results = [ensure_alegra_item(db, pid, provider, dry_run) for pid in pending]
    ok = RESOLVED + ((EXACT_MATCH, READY_TO_CREATE) if dry_run else ())
    unresolved = [r for r in results if r["status"] not in ok]
    transient = [r for r in unresolved if r["status"] in (PENDING, ERROR)]
    status = "READY" if not unresolved else "PENDING" if len(transient) == len(unresolved) else "BLOCKED"
    return {"status": status, "products": results, "unresolved": unresolved}


def create_remote(db: Session, product_id: UUID, provider: AlegraProvider | None = None, dry_run: bool = True) -> dict:
    """CREATE from the catalog panel / batch: the same single path as invoicing."""
    return {**ensure_alegra_item(db, product_id, provider, dry_run), "action": "CREATE"}


# ---- orchestration ------------------------------------------------------------------------------------------------

def sync_product(db: Session, product_id: UUID, index: CatalogIndex | None = None,
                 provider: AlegraProvider | None = None, dry_run: bool = True) -> dict:
    """The whole flow for one product: preview -> LINK (if unambiguous) or CREATE (barriers)."""
    provider = provider or AlegraProvider()
    product = db.get(Product, product_id)
    if product is None:
        return {"product_id": str(product_id), "status": ERROR, "reason": "Producto no encontrado"}
    if index is None:
        matches = find_remote_match(product.barcode.strip(), provider) if product.barcode else []
        index = build_index([m[0] for m in matches])
        if product.alegra_item_id:
            index.items_by_id.setdefault(product.alegra_item_id, {"id": product.alegra_item_id})
    linked = {p.alegra_item_id: str(p.id) for p in db.scalars(select(Product).where(Product.alegra_item_id.is_not(None)))}
    row = preview_product(product, index, linked)
    if row["proposed_action"] == "LINK":
        return link_existing(db, product_id, row["matched_alegra_item_id"], provider, dry_run)
    if row["proposed_action"] == "CREATE":
        return create_remote(db, product_id, provider, dry_run)
    return {"product_id": str(product_id), "status": row["sync_status"], "reason": row["reason"], "dry_run": dry_run}


def sync_batch(db: Session, product_ids: list[UUID], action: str, provider: AlegraProvider | None = None,
               dry_run: bool = True, expected: dict[str, str] | None = None) -> dict:
    """LINK or CREATE for at most ALEGRA_CATALOG_BATCH_LIMIT products. CREATE requires
    dry_run while ALEGRA_CATALOG_CREATE_ENABLED is off. `expected` (product_id -> item id)
    comes from the stored analysis; every link is re-validated live anyway."""
    if action not in ("LINK", "CREATE"):
        raise CatalogSyncError("Acción no válida")
    limit = settings.alegra_catalog_batch_limit
    if not product_ids:
        raise CatalogSyncError("No hay productos seleccionados")
    if len(product_ids) > limit:
        raise CatalogSyncError(f"Máximo {limit} productos por lote")
    if action == "CREATE" and not dry_run and not settings.alegra_catalog_create_enabled:
        raise CatalogSyncError("La creación de ítems en Alegra está deshabilitada; solo se permite simulación", 403)
    provider = provider or AlegraProvider()
    results = []
    for pid in dict.fromkeys(product_ids):                       # de-duplicate, keep order
        if action == "LINK":
            item_id = (expected or {}).get(str(pid))
            if not item_id:
                results.append({"product_id": str(pid), "status": REVIEW_REQUIRED,
                                "reason": "Sin coincidencia inequívoca en el último análisis"})
                continue
            results.append(link_existing(db, pid, item_id, provider, dry_run))
        else:
            results.append(create_remote(db, pid, provider, dry_run))
    return {"action": action, "dry_run": dry_run, "results": results,
            "summary": dict(Counter(r["status"] for r in results))}


# ---- stored analysis runs -----------------------------------------------------------------------------------------

def start_run(db: Session) -> CatalogSyncRun:
    running = db.scalar(select(CatalogSyncRun).where(CatalogSyncRun.status == "RUNNING")
                        .order_by(CatalogSyncRun.started_at.desc()))
    if running and running.started_at and _aware(running.started_at) > datetime.now(timezone.utc) - STALE_RUN:
        raise CatalogSyncError("Ya hay un análisis en curso", 409)
    run = CatalogSyncRun(status="RUNNING", started_at=datetime.now(timezone.utc))
    db.add(run); db.commit(); db.refresh(run)
    return run


def execute_run(db: Session, run_id: UUID, provider: AlegraProvider | None = None) -> CatalogSyncRun:
    run = db.get(CatalogSyncRun, run_id)
    try:
        index = fetch_index(provider)
        result = preview_all(db, index)
        run.rows = result["rows"]
        run.summary = {**result["summary"], "alegra_only_sample": result["alegra_only"][:50]}
        run.remote_items = index.total
        run.status = "DONE"
    except AlegraError as exc:
        db.rollback()
        run = db.get(CatalogSyncRun, run_id)
        run.status, run.error = "FAILED", str(exc)[:500]
    run.finished_at = datetime.now(timezone.utc)
    db.commit(); db.refresh(run)
    # keep the 5 most recent runs (each stores ~7.5k rows)
    old = db.scalars(select(CatalogSyncRun).order_by(CatalogSyncRun.started_at.desc()).offset(5)).all()
    for stale in old:
        db.delete(stale)
    db.commit()
    return run


def execute_run_background(run_id: UUID) -> None:
    from app.core.db import SessionLocal
    with SessionLocal() as db:
        try:
            execute_run(db, run_id)
        except Exception:  # noqa: BLE001 - background job: record, never propagate
            logger.exception("catalog_sync_run_failed run_id=%s", run_id)
            db.rollback()
            run = db.get(CatalogSyncRun, run_id)
            if run:
                run.status, run.error, run.finished_at = "FAILED", "Error inesperado", datetime.now(timezone.utc)
                db.commit()


def latest_run(db: Session, done_only: bool = False) -> CatalogSyncRun | None:
    stmt = select(CatalogSyncRun).order_by(CatalogSyncRun.started_at.desc())
    if done_only:
        stmt = stmt.where(CatalogSyncRun.status == "DONE")
    return db.scalar(stmt.limit(1))


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def priority_pos_products_preview(db: Session) -> list[dict]:
    """The 7 approved historical products (reverse flow) and whether they exist in the POS."""
    rows = []
    for spec in PRIORITY_POS_PRODUCTS:
        existing = db.scalar(select(Product).where(Product.alegra_item_id == spec["alegra_item_id"]))
        rows.append({**spec, "category": spec["category_code"], "barcode": None, "initial_stock": 0,
                     "product_id": str(existing.id) if existing else None,
                     "status": "ALREADY_IN_POS" if existing else "READY_TO_CREATE_LOCAL",
                     "reason": "Ya existe en el POS" if existing else "Costo y precio confirmados; pendiente de crear"})
    return rows


# ---- reverse flow: existing Alegra item -> NEW local Product (no Alegra write) -----------------------------------

def create_pos_products_for_alegra_items(db: Session, specs: list[dict], provider: AlegraProvider | None = None,
                                         dry_run: bool = True) -> dict:
    """Creates local Products for Alegra items that ALREADY exist, born linked
    (alegra_item_id), barcode NULL (Alegra's internal 901001... references are NOT EANs),
    stock 0, exact confirmed cost/price (no rounding, no margin rules).

    All-or-nothing: every spec is validated first (remote item exists + active + expected
    name, item not held by another Product, no local Product with the same name, category
    exists); any conflict aborts the whole batch. Alegra is only read (GET).
    spec: {alegra_item_id, name, unit_cost, sale_price, category_code}."""
    from app.models import OperationalCategory
    from app.schemas.products import ProductCreate
    from app.services.pos import get_operator
    from app.services.products import _relations, add_product_to_session
    provider = provider or AlegraProvider()
    checks, problems = [], []
    categories = {c.code: c for c in db.scalars(select(OperationalCategory).where(OperationalCategory.active.is_(True)))}
    local_names = {normalize_name(p.name): p for p in db.scalars(select(Product))}
    seen_items = Counter(str(s["alegra_item_id"]) for s in specs)
    for spec in specs:
        item_id, name = str(spec["alegra_item_id"]), spec["name"]
        check = {"alegra_item_id": item_id, "name": name, "problems": []}
        try:
            item = provider.client.get(f"/items/{item_id}")
        except AlegraError as exc:
            item = None
            check["problems"].append(f"No se pudo consultar el ítem en Alegra: {exc}")
        if item is not None and not item:
            check["problems"].append("El ítem no existe en Alegra")
        if item:
            check["remote"] = {"name": item.get("name"), "status": item.get("status"),
                               "reference": item.get("reference"), "price": [p.get("price") for p in item.get("price") or []]}
            if (item.get("status") or "active") != "active":
                check["problems"].append("El ítem está inactivo en Alegra")
            if normalize_name(item.get("name")) != normalize_name(name):
                check["problems"].append(f"Nombre en Alegra distinto: {item.get('name')}")
        if seen_items[item_id] > 1:
            check["problems"].append("Ítem repetido en el lote")
        holder = db.scalar(select(Product).where(Product.alegra_item_id == item_id))
        if holder:
            check["problems"].append(f"El ítem ya está vinculado al producto {holder.name} ({holder.id})")
        if normalize_name(name) in local_names:
            twin = local_names[normalize_name(name)]
            check["problems"].append(f"Ya existe un producto local con ese nombre ({twin.id})")
        if spec["category_code"] not in categories:
            check["problems"].append(f"No existe la categoría {spec['category_code']}")
        if Decimal(str(spec["sale_price"])) <= 0 or Decimal(str(spec["unit_cost"])) < 0:
            check["problems"].append("Precio o costo no válido")
        checks.append(check)
        problems += [f"{item_id} {name}: {p}" for p in check["problems"]]
    if problems:
        db.rollback()
        return {"status": "ABORTED", "dry_run": dry_run, "created": [], "checks": checks, "problems": problems}
    if dry_run:
        db.rollback()
        return {"status": "VALIDATED", "dry_run": True, "created": [], "checks": checks, "problems": []}

    operator = get_operator(db)
    created = []
    try:
        for spec in specs:
            category = categories[spec["category_code"]]
            body = ProductCreate(barcode=None, name=spec["name"], sale_price=Decimal(str(spec["sale_price"])),
                                 purchase_cost=Decimal(str(spec["unit_cost"])), category_id=category.id,
                                 supplier_id=None, minimum_stock=0, initial_stock=0)
            _relations(db, body.category_id, body.supplier_id)
            product = add_product_to_session(db, body, operator.id)         # same construction as manual/CSV
            product.alegra_item_id = str(spec["alegra_item_id"])
            db.flush()
            _audit(db, product.id, "CREATE_POS_PRODUCT", LINKED, None, product.alegra_item_id, None)
            created.append(product)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"status": "CREATED", "dry_run": False, "checks": checks, "problems": [],
            "created": [{"product_id": str(p.id), "name": p.name, "alegra_item_id": p.alegra_item_id,
                         "unit_cost": str(p.unit_cost), "sale_price": str(p.sale_price), "barcode": p.barcode}
                        for p in created]}
