"""Apply the HUMAN-APPROVED classification review (database/products/classification_review_candidates.csv,
2026-10-03): move exactly those products from SUPER to the suggested category. Usage in the api container:

  python -m app.scripts.apply_classification_review --csv candidates.csv --out applied.csv                       # dry-run
  python -m app.scripts.apply_classification_review --csv candidates.csv --out applied.csv --confirm --expected 174

Identity = barcode ONLY (name/brand/prefix/reason were audit aids, never used here). Every candidate must
exist exactly once, be active and be in SUPER now; the suggested category must be MASCOTAS_CON_CODIGO,
LICORES or CIGARROS. Any deviation -> nothing is written. Each UPDATE is guarded by
`barcode = <barcode> AND operational_category_id = <SUPER>`; all in ONE transaction, rolled back unless
exactly --expected rows changed. Writes only products.operational_category_id and updated_at. No Alegra.
"""
import argparse
import csv
import sys
from collections import Counter

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.db import engine
from app.models import OperationalCategory, Product

ALLOWED = ("MASCOTAS_CON_CODIGO", "LICORES", "CIGARROS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="approved classification_review_candidates.csv")
    parser.add_argument("--out", required=True, help="CSV of what is (or would be) applied")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--expected", type=int)
    args = parser.parse_args()

    with open(args.csv, encoding="utf-8-sig") as handle:
        candidates = list(csv.DictReader(handle, delimiter=";"))
    with Session(engine) as db:
        cats = {c.code: c for c in db.scalars(select(OperationalCategory))}
        code_by_id = {c.id: c.code for c in cats.values()}
        super_id = cats["SUPER"].id
        problems = Counter()
        seen = Counter(c["barcode"].strip() for c in candidates)
        plan = []
        for c in candidates:
            barcode = c["barcode"].strip()
            if seen[barcode] > 1:
                problems["AMBIGUOUS"] += 1; continue
            if c["suggested_category"] not in ALLOWED or c["suggested_category"] not in cats:
                problems["UNEXPECTED_CATEGORY"] += 1; continue
            found = list(db.scalars(select(Product).where(Product.barcode == barcode)))
            if not found:
                problems["NOT_FOUND"] += 1; continue
            if len(found) > 1:
                problems["AMBIGUOUS"] += 1; continue
            product = found[0]
            if not product.active:
                problems["NOT_ACTIVE"] += 1; continue
            if product.operational_category_id != super_id:
                problems["NOT_CURRENTLY_SUPER"] += 1; continue
            plan.append({"product_id": str(product.id), "barcode": product.barcode, "product_name": product.name,
                         "categoria_anterior": code_by_id[product.operational_category_id],
                         "categoria_nueva": c["suggested_category"], "confidence": c["confidence"],
                         "source_excel_row": c["source_excel_row"], "target_id": cats[c["suggested_category"]].id})
        db.rollback()
        moves = Counter(p["categoria_nueva"] for p in plan)
        print(f"MODE={'CONFIRM' if args.confirm else 'DRY_RUN'}")
        print(f"CSV_ROWS={len(candidates)}")
        print(f"TOTAL_TO_CHANGE={len(plan)}")
        for code in ALLOWED:
            print(f"SUPER_TO_{code}={moves.get(code, 0)}")
        for key in ("NOT_FOUND", "AMBIGUOUS", "NOT_CURRENTLY_SUPER", "UNEXPECTED_CATEGORY", "NOT_ACTIVE"):
            print(f"{key}={problems.get(key, 0)}")
        print(f"BY_CONFIDENCE={dict(Counter(p['confidence'] for p in plan))}")

        with open(args.out, "w", newline="", encoding="utf-8-sig") as handle:
            fields = ["product_id", "barcode", "product_name", "categoria_anterior", "categoria_nueva", "confidence",
                      "source_excel_row"]
            writer = csv.DictWriter(handle, fieldnames=fields, delimiter=";", extrasaction="ignore")
            writer.writeheader(); writer.writerows(plan)
        print(f"OUT={args.out}")

        if not args.confirm:
            return 0
        if problems or args.expected is None or args.expected != len(plan) or len(plan) != len(candidates):
            print(f"NOT_APPLIED: problems={dict(problems)} expected={args.expected} plan={len(plan)}"); return 1
        updated = 0
        try:
            for p in plan:
                updated += db.execute(
                    update(Product)
                    .where(Product.barcode == p["barcode"], Product.operational_category_id == super_id)
                    .values(operational_category_id=p["target_id"], updated_at=func.now())
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
