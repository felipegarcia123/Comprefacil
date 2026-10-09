"""Product <-> Alegra item mapping PREVIEW. Read-only: never writes alegra_item_id.

Verified against the real account (2026-09-28): Alegra items carry the EAN in
`reference`; GET /items?reference= is a SUBSTRING filter (not exact), so the
whole catalog is paged once (30/page) and matched locally, exactly.

AUTO_MATCH only when the product's code (or its UPC-A<->EAN-13 twin, the same
equivalence the scanner uses, services/barcodes.py) matches exactly ONE active
Alegra item, and no other product claims that item. Names are never used to
match; they only produce a review suggestion for NO_MATCH rows.
"""
import csv
import io
import logging
import re
import time
import unicodedata
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product
from app.services.alegra_client import AlegraError, AlegraNetworkError, AlegraRateLimitError, AlegraServerError
from app.services.alegra_provider import AlegraProvider
from app.services.barcodes import barcode_candidates

logger = logging.getLogger(__name__)

AUTO_MATCH, NO_MATCH, AMBIGUOUS, INVALID_BARCODE = "AUTO_MATCH", "NO_MATCH", "AMBIGUOUS", "INVALID_BARCODE"
GTIN_LENGTHS = (8, 12, 13, 14)


def is_valid_gtin(code: str) -> bool:
    """GS1 check digit for GTIN-8/12/13/14."""
    if not code.isascii() or not code.isdigit() or len(code) not in GTIN_LENGTHS:
        return False
    body, check = code[:-1], int(code[-1])
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def _norm_ref(value) -> str:
    return str(value or "").strip()


def _tokens(name: str) -> set[str]:
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return {t for t in re.split(r"[^a-z0-9]+", text) if len(t) >= 3}


@dataclass
class MappingRow:
    product_id: str
    barcode: str
    product_name: str
    sale_price: str
    active: bool
    current_alegra_item_id: str | None
    alegra_item_id: str | None
    alegra_name: str | None
    alegra_reference: str | None
    match_status: str
    confidence: str  # HIGH exact code · MEDIUM UPC<->EAN twin · LOW name suggestion only · NONE
    note: str | None = None
    suggestion_item_id: str | None = None
    suggestion_name: str | None = None
    suggestion_reference: str | None = None


def fetch_all_items(provider: AlegraProvider, pause: float = 0.45, max_pages: int = 1000,
                    retry_pause: float = 5.0) -> list[dict]:
    """Every Alegra item, 30 per page. ~0.45 s between pages keeps well under
    the 150 req/min limit; a 429 waits and retries the same page."""
    items, start, total = [], 0, None
    for _ in range(max_pages):
        for attempt in range(5):
            try:
                page, total = provider.list_items_page(start=start, limit=30)
                break
            except AlegraRateLimitError:
                time.sleep(20 * (attempt + 1))
            except (AlegraNetworkError, AlegraServerError):
                # GET-only scan: retrying a page is safe. A long scan (~100 pages) must
                # survive a transient network drop instead of throwing the whole run away.
                time.sleep(retry_pause * (attempt + 1))
        else:
            raise AlegraNetworkError(f"Alegra no respondió leyendo ítems (desde {start}) tras varios intentos")
        items += page
        start += len(page)
        if not page or len(page) < 30 or (total is not None and start >= total):
            break
        time.sleep(pause)
    unique = list({str(item["id"]): item for item in items}.values())
    if total is not None and len(unique) != total:
        # A page shifted while scanning: an incomplete catalog would silently turn
        # real matches into NO_MATCH, so refuse instead of returning a wrong preview.
        raise AlegraError(f"Lectura inconsistente del catálogo de Alegra: {len(unique)} ítems únicos de {total}")
    return unique


def build_mapping_preview(db: Session, items: list[dict]) -> dict:
    by_ref: dict[str, list[dict]] = {}
    for item in items:
        ref = _norm_ref(item.get("reference"))
        if ref:
            by_ref.setdefault(ref, []).append(item)
    token_index: dict[str, list[dict]] = {}
    for item in items:
        for token in _tokens(item.get("name") or ""):
            token_index.setdefault(token, []).append(item)

    products = db.scalars(select(Product).order_by(Product.name)).all()
    rows: list[MappingRow] = []
    for product in products:
        code = _norm_ref(product.barcode)
        row = MappingRow(product_id=str(product.id), barcode=product.barcode, product_name=product.name,
                         sale_price=str(product.sale_price), active=bool(product.active),
                         current_alegra_item_id=product.alegra_item_id, alegra_item_id=None, alegra_name=None,
                         alegra_reference=None, match_status=NO_MATCH, confidence="NONE")
        exact = by_ref.get(code, [])
        if not is_valid_gtin(code):
            row.match_status = INVALID_BARCODE
            if exact:
                row.note = (f"El código interno coincide exactamente con {len(exact)} ítem(s) de Alegra; "
                            "no es un EAN/UPC válido, revisar a mano")
                _fill(row, exact[0]); row.confidence = "LOW"
            rows.append(row)
            continue
        candidates: dict[str, tuple[dict, bool]] = {}
        for candidate_code in barcode_candidates(code):
            for item in by_ref.get(candidate_code, []):
                candidates[str(item["id"])] = (item, candidate_code == code)
        if not candidates:
            _suggest(row, product.name, token_index)
            rows.append(row)
            continue
        if len(candidates) > 1:
            row.match_status = AMBIGUOUS
            row.note = "Varios ítems de Alegra con ese código: " + ", ".join(sorted(candidates))
            rows.append(row)
            continue
        item, same_spelling = next(iter(candidates.values()))
        _fill(row, item)
        if (item.get("status") or "active") != "active":
            row.match_status = AMBIGUOUS
            row.note = "El único ítem con ese código está inactivo en Alegra"
        else:
            row.match_status = AUTO_MATCH
            row.confidence = "HIGH" if same_spelling else "MEDIUM"
            if not same_spelling:
                row.note = f"Coincide por equivalencia UPC-A/EAN-13 ({item.get('reference')})"
        rows.append(row)

    # One Alegra item claimed by two products -> neither can be auto-mapped.
    claims: dict[str, list[MappingRow]] = {}
    for row in rows:
        if row.match_status == AUTO_MATCH:
            claims.setdefault(row.alegra_item_id, []).append(row)
    for item_id, claimants in claims.items():
        if len(claimants) > 1:
            for row in claimants:
                row.match_status = AMBIGUOUS
                row.confidence = "NONE"
                row.note = f"El ítem {item_id} de Alegra coincide con {len(claimants)} productos del POS"

    for row in rows:
        if row.current_alegra_item_id and row.alegra_item_id and row.current_alegra_item_id != row.alegra_item_id:
            row.note = ((row.note + " · ") if row.note else "") + \
                f"Ya mapeado a {row.current_alegra_item_id}, distinto al encontrado"
    return {"summary": summarize(rows, len(items)), "rows": [asdict(r) for r in rows]}


def _fill(row: MappingRow, item: dict) -> None:
    row.alegra_item_id = str(item.get("id"))
    row.alegra_name = item.get("name")
    row.alegra_reference = _norm_ref(item.get("reference")) or None


def _suggest(row: MappingRow, name: str, token_index: dict[str, list[dict]]) -> None:
    """Review aid only (never a match): the Alegra item sharing the most name tokens."""
    tokens = _tokens(name)
    if len(tokens) < 2:
        return
    scores: dict[str, tuple[int, dict]] = {}
    for token in tokens:
        bucket = token_index.get(token, [])
        if len(bucket) > 200:
            continue  # too common to discriminate ("x", "gr", brand-wide words)
        for item in bucket:
            key = str(item["id"])
            scores[key] = (scores.get(key, (0, item))[0] + 1, item)
    if not scores:
        return
    best_id, (hits, item) = max(scores.items(), key=lambda kv: kv[1][0])
    jaccard = hits / len(tokens | _tokens(item.get("name") or ""))
    if hits >= 2 and jaccard >= 0.5:
        row.suggestion_item_id = best_id
        row.suggestion_name = item.get("name")
        row.suggestion_reference = _norm_ref(item.get("reference")) or None
        row.confidence = "LOW"


def summarize(rows: list[MappingRow], alegra_items: int) -> dict:
    def count(status, active_only=True):
        return sum(1 for r in rows if r.match_status == status and (r.active or not active_only))
    active = sum(1 for r in rows if r.active)
    auto = count(AUTO_MATCH)
    return {
        "alegra_items": alegra_items,
        "products_total": len(rows),
        "products_active": active,
        "active": {s: count(s) for s in (AUTO_MATCH, NO_MATCH, AMBIGUOUS, INVALID_BARCODE)},
        "all": {s: count(s, active_only=False) for s in (AUTO_MATCH, NO_MATCH, AMBIGUOUS, INVALID_BARCODE)},
        "active_auto_match_pct": round(auto * 100 / active, 1) if active else 0.0,
        "active_no_match_with_suggestion": sum(1 for r in rows if r.active and r.match_status == NO_MATCH
                                               and r.suggestion_item_id),
        "already_mapped": sum(1 for r in rows if r.current_alegra_item_id),
    }


def rows_to_csv(rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(MappingRow.__dataclass_fields__))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def apply_auto_matches(db: Session, rows: list[dict]) -> list[dict]:
    """Write alegra_item_id ONLY for preview rows that are AUTO_MATCH with HIGH
    confidence (exact code). Idempotent, never overwrites:
      APPLIED              product had no mapping -> set
      SKIP_ALREADY_MAPPED  product already has this same id
      CONFLICT             product has a different id, its barcode changed since the
                           preview, the product is gone, or another product already
                           holds that Alegra id
    Everything else in the preview (NO_MATCH, AMBIGUOUS, INVALID_BARCODE, MEDIUM/LOW)
    is ignored. Commits once at the end; Alegra is never contacted."""
    eligible = [r for r in rows if r.get("match_status") == AUTO_MATCH and r.get("confidence") == "HIGH"
                and r.get("alegra_item_id")]
    holders = {p.alegra_item_id: str(p.id) for p in db.scalars(
        select(Product).where(Product.alegra_item_id.is_not(None)))}
    report = []
    for row in eligible:
        product = db.get(Product, _uuid(row["product_id"]))
        new_id = str(row["alegra_item_id"])
        entry = {"product_id": row["product_id"], "barcode": row["barcode"], "product_name": row["product_name"],
                 "old_alegra_item_id": product.alegra_item_id if product else None,
                 "new_alegra_item_id": new_id, "status": None, "detail": None}
        if product is None:
            entry.update(status="CONFLICT", detail="El producto ya no existe")
        elif product.barcode != row["barcode"]:
            entry.update(status="CONFLICT", detail=f"El código cambió desde el preview ({product.barcode})")
        elif product.alegra_item_id == new_id:
            entry.update(status="SKIP_ALREADY_MAPPED")
        elif product.alegra_item_id:
            entry.update(status="CONFLICT", detail="El producto ya tiene otro alegra_item_id; no se sobrescribe")
        elif holders.get(new_id) and holders[new_id] != str(product.id):
            entry.update(status="CONFLICT", detail=f"El ítem {new_id} ya está asignado a otro producto")
        else:
            product.alegra_item_id = new_id
            holders[new_id] = str(product.id)
            entry["status"] = "APPLIED"
        report.append(entry)
    db.commit()
    return report


def _uuid(value):
    from uuid import UUID
    return value if isinstance(value, UUID) else UUID(str(value))
