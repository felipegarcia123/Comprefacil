"""Fill products.operational_category_id from database/products/catalogo_clasificado.xlsx
(audited 2026-10-03). Usage inside the api container:

  python -m app.scripts.apply_catalog_classification --file X.xlsx --csv plan.csv                         # dry-run
  python -m app.scripts.apply_catalog_classification --file X.xlsx --csv plan.csv --confirm --expected 7552

Rules (the audit's, nothing else):
  - source: sheet CATALOGO_CLASIFICADO only; rows with classification_confidence=HIGH and
    requires_review=false. REVISAR / RESUMEN are ignored. SIN_CLASIFICAR is never written.
  - identity: barcode only — exact; 11 digits -> leading zero recovered (UPC-A 12, the original
    import's rule: the Excel stores EANs as numbers); then the POS UPC<->EAN rule
    (alternate_barcode). Never by name. Ambiguous / one product hit by several rows -> skipped.
  - only FILLS: a product whose category is already set (whatever it is) is reported, never
    changed. The UPDATE itself is guarded by `operational_category_id IS NULL`.
  - writes only products.operational_category_id and products.updated_at, in ONE transaction;
    if the rows updated differ from --expected, everything is rolled back. Never calls Alegra.
Historical sales keep their SaleItem.category_code snapshot (it is not touched).
"""
import argparse
import csv
import sys
from collections import Counter, defaultdict

import openpyxl
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.db import engine
from app.models import OperationalCategory, Product
from app.services.barcodes import alternate_barcode

SHEET = "CATALOGO_CLASIFICADO"
APPLIED = ("SUPER", "MASCOTAS_CON_CODIGO", "LICORES", "CIGARROS", "MASCOTAS_SIN_CODIGO")   # never SIN_CLASIFICAR


def excel_code(value) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else None
    text = str(value).strip()
    return text if text.isdigit() else None        # e.g. "A7702402053099A": not a barcode


def candidates(code: str) -> list[tuple[str, str]]:
    out = [(code, "EXACT")]
    if len(code) == 11:
        out.append(("0" + code, "ZERO_RECOVERED"))
    for value, kind in list(out):
        alternate = alternate_barcode(value)
        if alternate:
            out.append((alternate, "UPC_EAN" if kind == "EXACT" else "ZERO_RECOVERED+UPC_EAN"))
    return out


def build_plan(db: Session, path: str) -> dict:
    sheet = openpyxl.load_workbook(path, read_only=True, data_only=True)[SHEET]
    rows = list(sheet.iter_rows(values_only=True))
    header = [str(h) for h in rows[0]]
    cats = {c.code: c for c in db.scalars(select(OperationalCategory))}
    code_by_id = {c.id: c.code for c in cats.values()}
    by_barcode = defaultdict(list)
    for product in db.scalars(select(Product)):
        if product.barcode:
            by_barcode[product.barcode.strip().lower()].append(product)

    skipped, hits = Counter(), defaultdict(list)            # product id -> [(excel row, kind, data)]
    ambiguous = []
    for number, values in enumerate(rows[1:], start=2):
        row = dict(zip(header, values))
        if not any(v not in (None, "") for v in values):
            continue
        if row["classification_confidence"] != "HIGH" or row["requires_review"] is not False:
            skipped["NOT_HIGH_OR_REVIEW"] += 1; continue
        target = row["operational_category"]
        if target not in APPLIED or target not in cats:
            skipped[f"TARGET_{target}"] += 1; continue
        code = excel_code(row["Codigo Ean"])
        if not code:
            skipped["INVALID_CODE"] += 1; continue
        found = {}
        for value, kind in candidates(code):
            for product in by_barcode.get(value.lower(), []):
                found.setdefault(product.id, (product, kind))
        if not found:
            skipped["NO_MATCH"] += 1; continue
        if len(found) > 1:
            ambiguous.append((number, code)); continue
        product, kind = next(iter(found.values()))
        hits[product.id].append((number, kind, code, row, product))

    plan, existing, same, duplicates = [], [], [], []
    for product_id, entries in hits.items():
        if len(entries) > 1:
            duplicates.append((product_id, [e[0] for e in entries])); continue
        number, kind, code, row, product = entries[0]
        before = code_by_id.get(product.operational_category_id)
        entry = {"pid": product.id, "product_id": str(product.id), "nombre": product.name, "barcode": product.barcode,
                 "categoria_anterior": before or "NULL", "categoria_nueva": row["operational_category"],
                 "excel_hoja": SHEET, "excel_fila": number, "excel_ean": code, "match_tipo": kind,
                 "category_id": cats[row["operational_category"]].id}
        if before is None:
            plan.append({**entry, "accion": "UPDATE"})
        elif before == row["operational_category"]:
            same.append({**entry, "accion": "SAME"})
        else:
            existing.append({**entry, "accion": "EXISTING_CATEGORY_NOT_CHANGED"})
    return {"plan": plan, "same": same, "existing": existing, "ambiguous": ambiguous,
            "duplicates": duplicates, "skipped": skipped}


def write_csv(path: str, result: dict) -> None:
    fields = ["product_id", "nombre", "barcode", "categoria_anterior", "categoria_nueva", "excel_hoja",
              "excel_fila", "excel_ean", "match_tipo", "accion"]
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter=";", extrasaction="ignore")
        writer.writeheader()
        for entry in result["plan"] + result["existing"] + result["same"]:
            writer.writerow(entry)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, help="catalogo_clasificado.xlsx")
    parser.add_argument("--csv", required=True, help="audit CSV to write (the plan / what was applied)")
    parser.add_argument("--confirm", action="store_true", help="apply (one transaction); default is dry-run")
    parser.add_argument("--expected", type=int, help="exact number of updates (required with --confirm)")
    args = parser.parse_args()

    with Session(engine) as db:
        result = build_plan(db, args.file)
        plan = result["plan"]
        write_csv(args.csv, result)
        by_cat = Counter(e["categoria_nueva"] for e in plan)
        print(f"MODE={'CONFIRM' if args.confirm else 'DRY_RUN'}")
        print(f"TOTAL_TO_UPDATE={len(plan)}")
        for code in ("SUPER", "MASCOTAS_CON_CODIGO", "LICORES", "CIGARROS", "MASCOTAS_SIN_CODIGO"):
            print(f"{code}={by_cat.get(code, 0)}")
        print(f"MATCH_TYPES={dict(Counter(e['match_tipo'] for e in plan))}")
        print(f"SAME_CATEGORY={len(result['same'])}")
        print(f"EXISTING_CATEGORY_CHANGES={len(result['existing'])}")
        print(f"AMBIGUOUS={len(result['ambiguous'])}")
        print(f"PRODUCT_HIT_BY_SEVERAL_ROWS={len(result['duplicates'])}")
        print(f"SKIPPED={dict(result['skipped'])}")
        print(f"CSV={args.csv}")
        for entry in result["existing"]:
            print("  EXISTING (not changed):", entry["barcode"], entry["nombre"], entry["categoria_anterior"],
                  "->", entry["categoria_nueva"])
        db.rollback()
        if not args.confirm:
            return 0
        if args.expected is None or args.expected != len(plan):
            print(f"NOT_APPLIED: expected={args.expected} plan={len(plan)}"); return 1
        if result["existing"] or result["ambiguous"] or result["duplicates"]:
            print("NOT_APPLIED: existing categories / ambiguous / duplicates present"); return 1

        updated = 0
        try:
            groups = defaultdict(list)
            for entry in plan:
                groups[entry["category_id"]].append(entry["pid"])
            for category_id, ids in groups.items():
                for start in range(0, len(ids), 500):
                    updated += db.execute(
                        update(Product)
                        .where(Product.id.in_(ids[start:start + 500]), Product.operational_category_id.is_(None))
                        .values(operational_category_id=category_id, updated_at=func.now())
                        .execution_options(synchronize_session=False)).rowcount
            if updated != args.expected:
                db.rollback()
                print(f"ROLLBACK: updated_rows={updated} expected={args.expected}"); return 1
            db.commit()
        except Exception:
            db.rollback()
            raise
        print(f"APPLIED: updated_rows={updated}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
