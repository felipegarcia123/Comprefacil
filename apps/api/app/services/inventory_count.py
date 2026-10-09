"""Physical stock count: XLSX template -> upload -> preview -> confirm -> ADJUSTMENT ledger rows.

The preview never writes. Confirm is all-or-nothing, guarded by the open cash session,
row locks, per-row `expected_current_stock` and a batch id stored in
`InventoryMovement.reference_id` (a repeated confirmation is refused).
"""
import io
import re
import unicodedata
from collections import Counter
from decimal import Decimal
from uuid import UUID, uuid4
from zipfile import BadZipFile

from fastapi import HTTPException
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils.exceptions import InvalidFileException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CashSession, InventoryMovement, OperationalCategory, Product
from app.schemas.inventory_count import (CountConfirmIn, CountConfirmOut, CountPreviewOut,
                                         CountRowOut, CountSummaryOut)
from app.services.barcodes import barcode_candidates
from app.services.inventory import build_adjustment, stock_subquery
from app.services.operational_session import require_open_session
from app.services.pos import get_operator

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 20_000
MAX_COUNT = 1_000_000
SHEET = "Conteo"
# product_id = stable identifier (also for products without barcode). Files from the
# previous template (barcode only) are still accepted.
HEADERS = ["product_id", "barcode", "name", "current_stock", "physical_count", "category", "unit"]
REASON_PREFIX = "Conteo físico inicial"


def batch_label(batch_id: UUID) -> str:
    return batch_id.hex[:8].upper()


def batch_reason(batch_id: UUID) -> str:
    return f"{REASON_PREFIX} · lote {batch_label(batch_id)}"


def _normal(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).strip().lower().split())


def _plain(value: Decimal):
    return int(value) if value == value.to_integral_value() else float(value)


# ---------------------------------------------------------------- template
def build_template(db: Session, counts: dict[UUID, Decimal] | None = None) -> bytes:
    """Full active catalog with an empty physical_count, or — with `counts` (from a
    physical audit) — only those products, physical_count prefilled for review."""
    stocks = stock_subquery()
    stmt = (
        select(Product.id, Product.barcode, Product.name, func.coalesce(stocks.c.current_stock, 0),
               OperationalCategory.name, Product.unit)
        .outerjoin(stocks, stocks.c.product_id == Product.id)
        .outerjoin(OperationalCategory, OperationalCategory.id == Product.operational_category_id)
        .where(Product.active.is_(True)).order_by(Product.name, Product.barcode))
    if counts is not None:
        stmt = stmt.where(Product.id.in_(list(counts)))
    rows = db.execute(stmt).all()
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    ws.append(HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="087D6D")
    input_fill = PatternFill("solid", fgColor="FFF7D6")
    right = Alignment(horizontal="right")
    id_font = Font(color="6F7D77", size=9)
    for index, (product_id, barcode, name, stock, category, unit) in enumerate(rows, start=2):
        # A product without barcode gets an EMPTY cell — never the text "None".
        counted = counts.get(product_id) if counts is not None else None
        ws.append([str(product_id), barcode or "", name, _plain(Decimal(stock)),
                   _plain(Decimal(counted)) if counted is not None else None, category or "", unit])
        ws.cell(row=index, column=1).font = id_font
        cell = ws.cell(row=index, column=2)   # explicit index: ws.max_row is O(n) per call
        cell.number_format = "@"          # text: keeps leading zeros, never scientific notation
        cell.data_type = "s"
        ws.cell(row=index, column=5).fill = input_fill
        ws.cell(row=index, column=5).alignment = right
    for column, width in zip("ABCDEFG", (38, 18, 48, 14, 16, 22, 10)):
        ws.column_dimensions[column].width = width
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:G{max(len(rows) + 1, 2)}"
    info = wb.create_sheet("Instrucciones")
    for line in ("Conteo físico de inventario",
                 "1. Escribe la cantidad contada en physical_count (entero, 0 o más).",
                 "2. Deja physical_count vacío si NO contaste ese producto: no se modificará.",
                 "3. Un 0 significa que contaste cero unidades.",
                 "4. No cambies las columnas product_id ni barcode; puedes borrar filas que no cuentes.",
                 "5. product_id identifica el producto (también los que no tienen código de barras).",
                 "6. Este archivo no crea productos: un producto desconocido bloquea el lote.",
                 "7. Sube el archivo en Inventario > Conteo físico, revisa el preview y confirma."):
        info.append([line])
    info.column_dimensions["A"].width = 90
    info["A1"].font = Font(bold=True, size=13)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


# ------------------------------------------------------------------ parsing
def _read_barcode(value) -> tuple[str, str | None]:
    """Returns (barcode, error)."""
    if value is None:
        return "", None
    if isinstance(value, bool):
        return str(value), "Barcode inválido"
    if isinstance(value, int):
        return str(value), None
    if isinstance(value, float):
        if value != int(value) or abs(value) >= 2 ** 53:
            return str(value), "Barcode dañado por formato numérico (usa texto)"
        return str(int(value)), None
    text = str(value).strip()
    if re.fullmatch(r"\d+(?:[.,]\d+)?[eE][+-]?\d+", text):
        return text, "Barcode en notación científica (usa texto)"
    return text, None


def _read_count(value) -> tuple[int | None, str | None, bool]:
    """Returns (count, error, blank)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None, True
    if isinstance(value, bool):
        return None, "physical_count debe ser un entero ≥ 0", False
    number = value
    if isinstance(value, str):
        text = value.strip().replace(" ", "")
        if not re.fullmatch(r"-?\d+", text):
            return None, "physical_count debe ser un entero ≥ 0", False
        number = int(text)
    if isinstance(number, float):
        if number != int(number):
            return None, "physical_count debe ser un entero (no se admiten decimales)", False
        number = int(number)
    if not isinstance(number, int):
        return None, "physical_count debe ser un entero ≥ 0", False
    if number < 0:
        return None, "physical_count no puede ser negativo", False
    if number > MAX_COUNT:
        return None, f"physical_count excede el máximo permitido ({MAX_COUNT})", False
    return number, None, False


def _read_product_id(value) -> tuple[str | None, UUID | None, str | None]:
    """Returns (raw, uuid, error). Empty -> (None, None, None)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None, None
    raw = str(value).strip()
    try:
        return raw, UUID(raw), None
    except ValueError:
        return raw, None, "product_id inválido (no lo modifiques en la plantilla)"


def parse_workbook(content: bytes) -> list[tuple[int, object, object, object]]:
    """Rows as (row_number, product_id, barcode, physical_count). `product_id` is None for
    files made with the previous template (barcode only)."""
    if not content:
        raise HTTPException(422, "El archivo está vacío")
    if len(content) > MAX_BYTES:
        raise HTTPException(413, "El archivo supera el límite de 10 MB")
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except (BadZipFile, InvalidFileException, KeyError, OSError, ValueError):
        raise HTTPException(422, "El archivo no es un XLSX válido")
    try:
        ws = wb[SHEET] if SHEET in wb.sheetnames else wb.worksheets[0]
        iterator = ws.iter_rows(values_only=True)
        header = next(iterator, None)
        names = [_normal(h) for h in (header or [])]
        if "physical_count" not in names or ("barcode" not in names and "product_id" not in names):
            raise HTTPException(422, "El archivo debe tener physical_count y product_id o barcode")
        if any(names.count(n) > 1 for n in ("barcode", "physical_count", "product_id")):
            raise HTTPException(422, "El archivo tiene columnas product_id, barcode o physical_count repetidas")
        b_idx = names.index("barcode") if "barcode" in names else None
        p_idx = names.index("product_id") if "product_id" in names else None
        c_idx = names.index("physical_count")
        cell = lambda row, idx: row[idx] if idx is not None and idx < len(row) else None
        rows = []
        for number, row in enumerate(iterator, start=2):
            product_id, barcode, count = cell(row, p_idx), cell(row, b_idx), cell(row, c_idx)
            if all(v is None or (isinstance(v, str) and not v.strip()) for v in (product_id, barcode, count)):
                continue
            rows.append((number, product_id, barcode, count))
            if len(rows) > MAX_ROWS:
                raise HTTPException(413, "El archivo supera el límite de 20.000 filas")
        return rows
    finally:
        wb.close()


# ------------------------------------------------------------------ preview
def _stock_for(db: Session, product_ids: list[UUID]) -> dict[UUID, Decimal]:
    if not product_ids:
        return {}
    rows = db.execute(select(InventoryMovement.product_id, func.sum(InventoryMovement.quantity))
                      .where(InventoryMovement.product_id.in_(product_ids))
                      .group_by(InventoryMovement.product_id)).all()
    return {pid: Decimal(total or 0) for pid, total in rows}


def preview_count(db: Session, content: bytes) -> CountPreviewOut:
    raw_rows = parse_workbook(content)
    parsed = []
    wanted: set[str] = set()
    wanted_ids: set[UUID] = set()
    for number, raw_product_id, raw_barcode, raw_count in raw_rows:
        pid_raw, pid, pid_error = _read_product_id(raw_product_id)
        barcode, barcode_error = _read_barcode(raw_barcode)
        count, count_error, blank = _read_count(raw_count)
        errors = [e for e in (pid_error, barcode_error, count_error) if e]
        if not pid_raw and not barcode and not barcode_error:
            errors.insert(0, "Falta product_id o barcode")
        if pid:
            wanted_ids.add(pid)
        if barcode and not barcode_error:
            wanted.update(c.lower() for c in barcode_candidates(barcode))
        parsed.append((number, pid_raw, pid, barcode, count, blank, errors, bool(barcode) and not barcode_error))

    found: dict[str, Product] = {}
    if wanted:
        for product in db.scalars(select(Product).where(func.lower(Product.barcode).in_(wanted))).unique():
            found[product.barcode.lower()] = product
    by_id = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(wanted_ids))).unique()} if wanted_ids else {}

    def by_barcode(barcode: str, errors: list[str]) -> Product | None:
        codes = [c.lower() for c in barcode_candidates(barcode)]
        exact = found.get(codes[0])
        variant = found.get(codes[1]) if len(codes) > 1 else None
        if exact and variant and exact.id != variant.id:
            errors.append(f"Conflicto: {exact.barcode} y {variant.barcode} son productos distintos")
            return None
        return exact or variant

    resolved: list[Product | None] = []
    for number, pid_raw, pid, barcode, count, blank, errors, lookup in parsed:
        product = None
        if pid:
            # New template: product_id is authoritative; a barcode next to it must agree.
            product = by_id.get(pid)
            if not product:
                errors.append("Producto desconocido: el conteo no crea productos")
            elif lookup and by_barcode(barcode, []) is not product:
                errors.append("product_id y barcode no corresponden al mismo producto")
                product = None
        elif lookup and not pid_raw:
            product = by_barcode(barcode, errors)
            if not product and not errors:
                errors.append("Código desconocido: el conteo no crea productos")
        if product and not product.active:
            errors.append("Producto inactivo")
            product = None
        resolved.append(product)

    per_product = Counter(p.id for p in resolved if p)
    stocks = _stock_for(db, list(per_product))
    rows = []
    for (number, pid_raw, _pid, barcode, count, blank, errors, _), product in zip(parsed, resolved):
        if product and per_product[product.id] > 1:
            errors.append("Producto duplicado dentro del archivo")
        current = stocks.get(product.id, Decimal(0)) if product else None
        if errors:
            status, delta = "ERROR", None
        elif blank:
            status, delta = "SKIPPED", None
        else:
            delta = Decimal(count) - current
            status = "CHANGE" if delta != 0 else "UNCHANGED"
        rows.append(CountRowOut(
            row_number=number, source_barcode=barcode or "", source_product_id=pid_raw,
            product_id=product.id if product else None,
            barcode=product.barcode if product else None, name=product.name if product else None,
            current_stock=current, physical_count=count, delta=delta, status=status, errors=errors))

    changes = [r for r in rows if r.status == "CHANGE"]
    positive = sum((r.delta for r in changes if r.delta > 0), Decimal(0))
    negative = sum((r.delta for r in changes if r.delta < 0), Decimal(0))
    active_total = db.scalar(select(func.count()).select_from(Product).where(Product.active.is_(True))) or 0
    in_file = {r.product_id for r in rows if r.product_id and r.status != "ERROR"}
    summary = CountSummaryOut(
        file_rows=len(rows), with_difference=len(changes),
        unchanged=sum(r.status == "UNCHANGED" for r in rows),
        skipped_blank=sum(r.status == "SKIPPED" for r in rows),
        errors=sum(r.status == "ERROR" for r in rows),
        positive_units=positive, negative_units=negative, net_units=positive + negative,
        active_products_not_in_file=max(active_total - len(in_file), 0))
    session_open = db.scalar(select(CashSession.id).where(CashSession.closed_at.is_(None))) is not None
    return CountPreviewOut(
        batch_id=uuid4(), session_open=session_open,
        can_confirm=summary.errors == 0 and summary.with_difference > 0 and session_open,
        summary=summary, rows=rows)


# ------------------------------------------------------------------ confirm
def confirm_count(db: Session, body: CountConfirmIn) -> CountConfirmOut:
    try:
        require_open_session(db, lock=True)      # serialises confirmations and enforces the jornada rule
        if db.scalar(select(func.count()).select_from(InventoryMovement)
                     .where(InventoryMovement.reference_id == body.batch_id)):
            raise HTTPException(409, detail={"code": "COUNT_BATCH_ALREADY_APPLIED",
                "message": f"El lote {batch_label(body.batch_id)} ya fue aplicado. No se modificó el inventario."})
        ids = [row.product_id for row in body.rows]
        if len(set(ids)) != len(ids):
            raise HTTPException(422, "El lote contiene productos duplicados")
        products = {p.id: p for p in db.scalars(
            select(Product).where(Product.id.in_(ids)).order_by(Product.id).with_for_update(of=Product)).unique()}
        if any(i not in products for i in ids):
            raise HTTPException(409, detail={"code": "COUNT_PRODUCT_NOT_FOUND",
                "message": "Hay productos que ya no existen. Genera un nuevo preview."})
        inactive = [products[i].barcode for i in ids if not products[i].active]
        if inactive:
            raise HTTPException(409, detail={"code": "COUNT_PRODUCT_INACTIVE",
                "message": f"Productos inactivos en el lote: {', '.join(inactive[:5])}. Genera un nuevo preview."})
        stocks = _stock_for(db, ids)
        stale = [products[r.product_id] for r in body.rows
                 if stocks.get(r.product_id, Decimal(0)) != r.expected_current_stock]
        if stale:
            names = ", ".join(f"{p.name} ({p.barcode})" for p in stale[:5])
            more = f" y {len(stale) - 5} más" if len(stale) > 5 else ""
            raise HTTPException(409, detail={"code": "COUNT_STOCK_CHANGED", "changed_count": len(stale),
                "message": f"El stock cambió desde el preview en {len(stale)} producto(s): {names}{more}. "
                           "No se aplicó nada; genera un nuevo preview."})
        operator = get_operator(db)
        reason = batch_reason(body.batch_id)
        created, positive, negative = 0, Decimal(0), Decimal(0)
        for row in sorted(body.rows, key=lambda r: str(r.product_id)):
            delta = Decimal(row.physical_count) - stocks.get(row.product_id, Decimal(0))
            if delta == 0:
                continue
            db.add(build_adjustment(products[row.product_id], delta, reason, operator.id, body.batch_id))
            created += 1
            if delta > 0:
                positive += delta
            else:
                negative += delta
        db.commit()
        return CountConfirmOut(batch_id=body.batch_id, batch_label=batch_label(body.batch_id), reason=reason,
                               movements_created=created, positive_units=positive,
                               negative_units=negative, net_units=positive + negative)
    except Exception:
        db.rollback()
        raise
