from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Product


def alternate_barcode(code: str) -> str | None:
    """UPC-A (12 digits) <-> EAN-13 with a single leading zero. Never strips other zeros."""
    if not code.isascii() or not code.isdigit():
        return None
    if len(code) == 13 and code[0] == "0":
        return code[1:]
    if len(code) == 12:
        return "0" + code
    return None


def barcode_candidates(code: str) -> list[str]:
    code = code.strip()
    alternate = alternate_barcode(code)
    return [code, alternate] if alternate else [code]


def find_product_by_barcode(db: Session, code: str, *, lock: bool = False) -> Product | None:
    """Exact match first; the UPC/EAN variant only when the exact code is unknown.

    If both spellings belong to different products the scan is ambiguous, so it is
    refused instead of picking one. Stored barcodes are never modified.
    """
    codes = barcode_candidates(code)
    stmt = select(Product).where(func.lower(Product.barcode).in_([c.lower() for c in codes]))
    if lock:
        stmt = stmt.with_for_update()
    found = {p.barcode.lower(): p for p in db.scalars(stmt).unique()}
    exact = found.get(codes[0].lower())
    variant = found.get(codes[1].lower()) if len(codes) > 1 else None
    if exact and variant and exact.id != variant.id:
        raise HTTPException(409, f"Conflicto: los códigos {exact.barcode} y {variant.barcode} "
                                 "pertenecen a productos distintos")
    return exact or variant
