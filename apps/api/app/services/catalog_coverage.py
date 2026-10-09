"""Catalog coverage of REAL sales + Alegra item-creation preview. Analysis only:
nothing here writes to Alegra or changes a product.

"Real" sales = COMPLETED sales on/after the date the current catalog was loaded
(`catalog_since`, derived from the data: the day most active products were
created). Before it the POS sold demo products; those, and sales containing
now-inactive products, are excluded — counted, never deleted.

Coverage has two readings, never mixed: by LINES (how many sale lines could be
invoiced) and by VALUE (how much money). Manual lines (granel/bulto) count as
covered only if their generic Alegra item is configured.
"""
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CashSession, OperationalCategory, Product, Sale
from app.domain.enums import SaleStatus
from app.services.alegra_item_mapping import is_valid_gtin
from app.services.barcodes import barcode_candidates

LOCAL = ZoneInfo("America/Bogota")
PARETO_TARGETS = (80, 90, 95, 98, 99)
ZERO = Decimal("0")


# ---- window ---------------------------------------------------------------------------------------------------

def catalog_since(db: Session) -> date | None:
    """Local day on which most currently-active products were created (the real
    catalog import). Derived, so it is stated in every report, never hidden."""
    local_day = func.date(func.timezone("America/Bogota", Product.created_at)) \
        if db.get_bind().dialect.name == "postgresql" else func.date(Product.created_at)
    row = db.execute(select(local_day.label("d"), func.count()).where(Product.active.is_(True))
                     .group_by("d").order_by(func.count().desc()).limit(1)).first()
    if not row or row[0] is None:
        return None
    return row[0] if isinstance(row[0], date) else date.fromisoformat(str(row[0]))


def _manual_item(sale: Sale, item=None) -> str | None:
    from app.services.electronic_invoicing import manual_item_id
    return manual_item_id(sale, item)


def real_sales(db: Session, since: date | None) -> tuple[list[Sale], dict]:
    """(real sales, exclusion counts). Excluded: before `since`, or containing a
    product that is inactive now (demo catalog)."""
    all_completed = db.scalars(select(Sale).where(Sale.status == SaleStatus.COMPLETED)
                               .order_by(Sale.completed_at)).unique().all()
    start = datetime.combine(since, time.min, LOCAL) if since else None
    kept, excluded = [], Counter()
    for sale in all_completed:
        completed = sale.completed_at if sale.completed_at.tzinfo else sale.completed_at.replace(tzinfo=timezone.utc)
        if start and completed < start:
            excluded["before_catalog"] += 1
        elif any(item.product is not None and not item.product.active for item in sale.items):
            excluded["inactive_product"] += 1
        else:
            kept.append(sale)
    return kept, {"before_catalog": excluded["before_catalog"], "inactive_product": excluded["inactive_product"],
                  "total_completed": len(all_completed)}


# ---- coverage ---------------------------------------------------------------------------------------------------

def _line_state(sale: Sale, item) -> str:
    if item.product_id is None:
        return "MANUAL_COVERED" if _manual_item(sale, item) else "MANUAL_UNCOVERED"
    return "MAPPED" if item.product and item.product.alegra_item_id else "UNMAPPED"


def coverage(db: Session, since: date | None = None) -> dict:
    since = since if since is not None else catalog_since(db)
    sales, excluded = real_sales(db, since)
    lines = values = ZERO
    counts, amounts = Counter(), defaultdict(lambda: ZERO)
    products_sold, products_mapped = set(), set()
    sales_fully_covered = 0
    for sale in sales:
        covered = True
        for item in sale.items:
            state = _line_state(sale, item)
            counts[state] += 1
            amounts[state] += item.line_total
            if item.product_id:
                products_sold.add(item.product_id)
                if state == "MAPPED":
                    products_mapped.add(item.product_id)
            covered = covered and state in ("MAPPED", "MANUAL_COVERED")
        sales_fully_covered += covered
    total_lines = sum(counts.values())
    total_value = sum(amounts.values(), ZERO)
    ok_lines = counts["MAPPED"] + counts["MANUAL_COVERED"]
    ok_value = amounts["MAPPED"] + amounts["MANUAL_COVERED"]
    active = db.scalar(select(func.count()).select_from(Product).where(Product.active.is_(True))) or 0
    mapped_active = db.scalar(select(func.count()).select_from(Product).where(
        Product.active.is_(True), Product.alegra_item_id.is_not(None))) or 0
    pct = lambda part, whole: float(round(Decimal(part) * 100 / whole, 1)) if whole else None
    return {
        "since": since, "excluded": excluded, "sales": len(sales), "sales_fully_invoiceable": sales_fully_covered,
        "sales_invoiceable_pct": pct(sales_fully_covered, len(sales)),
        "catalog": {"active": active, "mapped": mapped_active, "pct": pct(mapped_active, active)},
        "products": {"distinct_sold": len(products_sold), "distinct_sold_mapped": len(products_mapped),
                     "pct": pct(len(products_mapped), len(products_sold))},
        "lines": {"total": total_lines, "mapped": counts["MAPPED"], "unmapped": counts["UNMAPPED"],
                  "manual_covered": counts["MANUAL_COVERED"], "manual_uncovered": counts["MANUAL_UNCOVERED"],
                  "pct": pct(ok_lines, total_lines)},
        "value": {"total": total_value, "mapped": amounts["MAPPED"], "unmapped": amounts["UNMAPPED"],
                  "manual_covered": amounts["MANUAL_COVERED"], "manual_uncovered": amounts["MANUAL_UNCOVERED"],
                  "pct": pct(ok_value, total_value)},
        "by_session": _by_session(db, sales),
    }


def _by_session(db: Session, sales: list[Sale]) -> list[dict]:
    sessions = db.scalars(select(CashSession).order_by(CashSession.opened_at)).all()
    rows = []
    for session in sessions:
        opened = session.opened_at if session.opened_at.tzinfo else session.opened_at.replace(tzinfo=timezone.utc)
        closed = session.closed_at and (session.closed_at if session.closed_at.tzinfo
                                        else session.closed_at.replace(tzinfo=timezone.utc))
        in_session = [s for s in sales if (s.completed_at if s.completed_at.tzinfo else s.completed_at.replace(tzinfo=timezone.utc)) >= opened
                      and (closed is None or (s.completed_at if s.completed_at.tzinfo else s.completed_at.replace(tzinfo=timezone.utc)) <= closed)]
        if not in_session:
            continue
        states = [_line_state(s, i) for s in in_session for i in s.items]
        ok = sum(1 for st in states if st in ("MAPPED", "MANUAL_COVERED"))
        value = sum((i.line_total for s in in_session for i in s.items), ZERO)
        ok_value = sum((i.line_total for s in in_session for i in s.items
                        if _line_state(s, i) in ("MAPPED", "MANUAL_COVERED")), ZERO)
        rows.append({"cash_session_id": str(session.id), "opened_at": session.opened_at, "closed_at": session.closed_at,
                     "sales": len(in_session), "lines": len(states),
                     "lines_pct": round(ok * 100 / len(states), 1) if states else None,
                     "value_pct": float(round(ok_value * 100 / value, 1)) if value else None})
    return rows


# ---- ranking / Pareto / priorities ------------------------------------------------------------------------------

def unmapped_ranking(db: Session, since: date | None = None) -> list[dict]:
    """Active products WITHOUT alegra_item_id sold in real sales, with their usage."""
    since = since if since is not None else catalog_since(db)
    sales, _ = real_sales(db, since)
    usage: dict = defaultdict(lambda: {"sales": set(), "lines": 0, "units": ZERO, "revenue": ZERO})
    for sale in sales:
        for item in sale.items:
            if item.product_id and item.product and not item.product.alegra_item_id:
                u = usage[item.product_id]
                u["sales"].add(sale.id); u["lines"] += 1; u["units"] += item.quantity; u["revenue"] += item.line_total
    products = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(list(usage))))} if usage else {}
    categories = {c.id: c.name for c in db.scalars(select(OperationalCategory))}
    ranking = [{"product_id": str(pid), "barcode": products[pid].barcode, "name": products[pid].name,
                "times_sold": len(u["sales"]), "lines": u["lines"], "units_sold": u["units"], "revenue": u["revenue"],
                "current_sale_price": products[pid].sale_price, "current_cost": products[pid].unit_cost,
                "category": categories.get(products[pid].operational_category_id),
                "mapping_status": ("NO_BARCODE" if not products[pid].barcode else "UNMAPPED" if is_valid_gtin(products[pid].barcode.strip()) else "INVALID_BARCODE")}
               for pid, u in usage.items()]
    ranking.sort(key=lambda r: (-r["lines"], -r["revenue"], r["name"]))
    return ranking


def pareto(total_lines: int, covered_lines: int, ranking: list[dict]) -> list[dict]:
    """How many of the ranked unmapped products must be resolved to reach each
    line-coverage target (manual/other gaps are not solvable by mapping)."""
    rows = []
    for target in PARETO_TARGETS:
        needed_lines = -(-target * total_lines // 100) if total_lines else 0
        if covered_lines >= needed_lines:
            rows.append({"target_pct": target, "products_needed": 0, "reachable": True})
            continue
        acc, count = covered_lines, 0
        for row in ranking:
            acc += row["lines"]; count += 1
            if acc >= needed_lines:
                break
        rows.append({"target_pct": target, "products_needed": count if acc >= needed_lines else None,
                     "reachable": acc >= needed_lines})
    return rows


def priorities(ranking: list[dict], total_lines: int, covered_lines: int) -> dict[str, str]:
    """product_id -> P0..P2 from real usage only (P3 = never sold, assigned by caller):
    P0 = the fewest products that take line coverage to 80 %; P1 = next ones up to
    95 % or sold in 2+ sales; P2 = sold at least once."""
    result, acc = {}, covered_lines
    p0_goal = -(-80 * total_lines // 100) if total_lines else 0
    p1_goal = -(-95 * total_lines // 100) if total_lines else 0
    for row in ranking:
        if acc < p0_goal:
            result[row["product_id"]] = "P0"
        elif acc < p1_goal or row["times_sold"] >= 2:
            result[row["product_id"]] = "P1"
        else:
            result[row["product_id"]] = "P2"
        acc += row["lines"]
    return result


# ---- Alegra item-creation preview (no POST) ---------------------------------------------------------------------

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def normalize_name(name: str) -> tuple[str, bool]:
    """Only safe cleanup: control characters out, whitespace collapsed. Meaning untouched.
    Returns (name, looks_corrupt) — U+FFFD means an upstream encoding error to review."""
    cleaned = re.sub(r"\s+", " ", _CONTROL.sub(" ", unicodedata.normalize("NFC", name or ""))).strip()
    return cleaned, "�" in cleaned


def audit_alegra_items(items: list[dict]) -> dict:
    """How existing items are set up (category, tax, type, inventory unit)."""
    cat = Counter(((i.get("category") or {}).get("id"), (i.get("category") or {}).get("name")) for i in items)
    return {
        "items": len(items),
        "category": [{"id": k[0], "name": k[1], "count": v} for k, v in cat.most_common(5)],
        "tax_empty": sum(1 for i in items if not i.get("tax")),
        "type": dict(Counter(i.get("type") for i in items)),
        "inventory_unit": dict(Counter((i.get("inventory") or {}).get("unit") if i.get("inventory") else None
                                       for i in items)),
        "price_lists": dict(Counter(str(p.get("idPriceList")) for i in items for p in (i.get("price") or []))),
    }


def creation_preview(db: Session, items: list[dict], category_id: str, since: date | None = None) -> dict:
    """One row per ACTIVE product without alegra_item_id. Statuses:
    READY_TO_CREATE · ALREADY_EXISTS (exact reference, incl. UPC/EAN twin, already in
    Alegra -> link, never create) · INVALID_BARCODE · REVIEW_REQUIRED (price <= 0,
    corrupt/too long name, or an Alegra item with the very same name under another code)."""
    since = since if since is not None else catalog_since(db)
    refs = {str(i.get("reference") or "").strip(): i for i in items if str(i.get("reference") or "").strip()}
    # Same digits except leading zeros (e.g. GTIN-14 "0750…" vs EAN-13 "750…"): not a
    # link under the scanner's UPC/EAN rule, but creating it would duplicate the product.
    stripped = {ref.lstrip("0"): i for ref, i in refs.items() if ref.isdigit()}
    names = Counter(normalize_name(i.get("name") or "")[0].upper() for i in items)
    coverage_now = coverage(db, since)
    ranking = unmapped_ranking(db, since)
    usage = {r["product_id"]: r for r in ranking}
    prio = priorities(ranking, coverage_now["lines"]["total"],
                      coverage_now["lines"]["mapped"] + coverage_now["lines"]["manual_covered"])
    rows = []
    for product in db.scalars(select(Product).where(Product.active.is_(True), Product.alegra_item_id.is_(None))
                              .order_by(Product.name)):
        pid = str(product.id)
        code = (product.barcode or "").strip()
        name, corrupt = normalize_name(product.name)
        use = usage.get(pid, {})
        row = {"product_id": pid, "barcode": product.barcode, "name": product.name, "sale_price": product.sale_price,
               "times_sold": use.get("times_sold", 0), "revenue": use.get("revenue", ZERO),
               "proposed_reference": code, "proposed_name": name, "proposed_price": product.sale_price,
               "proposed_category": category_id, "proposed_tax": "[]", "status": "READY_TO_CREATE", "reason": "",
               "creation_priority": prio.get(pid, "P3")}
        existing = next((refs[c] for c in barcode_candidates(code) if c in refs), None)
        if not is_valid_gtin(code):
            row.update(status="INVALID_BARCODE", reason="El código no es un EAN/UPC válido; definir referencia a mano")
        elif existing:
            row.update(status="ALREADY_EXISTS",
                       reason=f"Ya existe en Alegra (ítem {existing.get('id')}, {existing.get('name')}); vincular, no crear")
        elif code.lstrip("0") in stripped:
            twin = stripped[code.lstrip("0")]
            row.update(status="REVIEW_REQUIRED",
                       reason=f"Alegra tiene el ítem {twin.get('id')} ({twin.get('name')}) con el mismo código salvo ceros "
                              f"iniciales ({twin.get('reference')}); revisar antes de crear")
        elif product.sale_price is None or product.sale_price <= 0:
            row.update(status="REVIEW_REQUIRED", reason="Precio de venta en cero o vacío")
        elif not name or corrupt:
            row.update(status="REVIEW_REQUIRED", reason="Nombre vacío o con caracteres dañados")
        elif len(name) > 150:
            row.update(status="REVIEW_REQUIRED", reason="Nombre de más de 150 caracteres (límite de Alegra)")
        elif names.get(name.upper()):
            row.update(status="REVIEW_REQUIRED",
                       reason="Ya hay en Alegra un ítem con exactamente este nombre y otro código; revisar duplicado")
        rows.append(row)
    rows.sort(key=lambda r: (r["creation_priority"], -r["times_sold"], -r["revenue"], r["proposed_name"]))
    return {"rows": rows, "summary": {"status": dict(Counter(r["status"] for r in rows)),
                                      "priority": dict(Counter(r["creation_priority"] for r in rows))}}


def item_payload(row: dict) -> dict:
    """The standard POST /items body for a READY_TO_CREATE row (not sent here). Same as
    alegra_catalog_sync.prepare_create: inventory carries only unit="unit" (BULTO, created
    without it, cannot be stamped), never quantities or costs (POS owns stock); no tax
    invented (tax: [] like every current item), category Ventas."""
    return {"name": row["proposed_name"], "reference": row["proposed_reference"], "type": "product",
            "price": [{"idPriceList": "1", "price": float(row["proposed_price"])}], "tax": [],
            "category": {"id": row["proposed_category"]}, "inventory": {"unit": "unit"}}
