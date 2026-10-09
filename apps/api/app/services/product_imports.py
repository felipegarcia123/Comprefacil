import csv
import io
import re
import unicodedata
from collections import Counter
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import OperationalCategory, Product, Supplier
from app.schemas.product_imports import (ProductImportPreview, ProductImportRequest,
                                         ProductImportResult, ProductImportRow)
from app.schemas.products import ProductCreate
from app.services.pos import get_operator
from app.services.products import add_product_to_session
from app.services.operational_session import require_open_session

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 20_000
BATCH_SIZE = 250
FIELDS = {"barcode", "name", "category", "supplier", "purchase_cost", "sale_price",
          "initial_stock", "minimum_stock", "unit", "description", "active"}


def normalized(value: str) -> str:
    value = "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))
    return " ".join(value.strip().casefold().split())


def parse_money(raw: str, field: str) -> Decimal:
    value = raw.strip().replace("$", "").replace(" ", "")
    if not value:
        return Decimal("0")
    if re.search(r"[eE]", value) or not re.fullmatch(r"-?[0-9.,]+", value):
        raise ValueError(f'{field} inválido: "{raw}"')
    if "." in value and "," in value:
        if value.rfind(".") < value.rfind(","):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif "," in value:
        parts = value.split(",")
        value = "".join(parts) if len(parts[-1]) == 3 else ".".join(parts)
    elif "." in value:
        parts = value.split(".")
        if len(parts[-1]) == 3:
            value = "".join(parts)
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ValueError(f'{field} inválido: "{raw}"')
    if not result.is_finite():
        raise ValueError(f'{field} inválido: "{raw}"')
    return result


def parse_integer(raw: str, field: str) -> int:
    value = raw.strip().replace(" ", "")
    if not value:
        return 0
    if re.fullmatch(r"-?[0-9]{1,3}(\.[0-9]{3})+", value):
        value = value.replace(".", "")
    if not re.fullmatch(r"-?[0-9]+", value):
        raise ValueError(f"{field} debe ser entero")
    return int(value)


def parse_active(raw: str) -> bool:
    value = normalized(raw)
    if not value:
        return True
    if value in {"true", "1", "activo", "si", "sí", "yes"}:
        return True
    if value in {"false", "0", "inactivo", "no"}:
        return False
    raise ValueError(f'Estado activo inválido: "{raw}"')


def _csv(request: ProductImportRequest) -> tuple[str, list[str], list[dict[str, str]]]:
    if len(request.content.encode("utf-8")) > MAX_BYTES:
        raise HTTPException(413, "El archivo supera el límite de 10 MB")
    content = request.content.lstrip("\ufeff")
    sample = content[:8192]
    delimiter = request.delimiter
    if delimiter == "auto":
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;").delimiter
        except csv.Error:
            raise HTTPException(422, "No fue posible detectar el delimitador. Selecciona coma o punto y coma.")
    try:
        reader = csv.DictReader(io.StringIO(content), delimiter=delimiter, strict=True)
        headers = [header.strip() for header in (reader.fieldnames or [])]
        if not headers or any(not header for header in headers):
            raise HTTPException(422, "El CSV debe tener encabezados válidos")
        if len({normalized(h) for h in headers}) != len(headers):
            raise HTTPException(422, "El CSV contiene encabezados duplicados")
        rows = []
        for row in reader:
            if None in row:
                raise HTTPException(422, "Una fila contiene más columnas que el encabezado")
            clean = {str(key).strip(): (value or "").strip() for key, value in row.items()}
            if any(clean.values()):
                rows.append(clean)
            if len(rows) > MAX_ROWS:
                raise HTTPException(413, "El CSV supera el límite de 20.000 filas")
        return delimiter, headers, rows
    except (csv.Error, UnicodeError):
        raise HTTPException(422, "El archivo CSV está corrupto o tiene una codificación inválida")


def validate_import(db: Session, request: ProductImportRequest) -> ProductImportPreview:
    delimiter, headers, raw_rows = _csv(request)
    unknown_headers = set(request.mapping) - set(headers)
    mapped = [value for value in request.mapping.values() if value]
    if unknown_headers or any(value not in FIELDS for value in mapped):
        raise HTTPException(422, "El mapeo contiene columnas o campos desconocidos")
    if len(mapped) != len(set(mapped)):
        raise HTTPException(422, "No puedes mapear dos columnas al mismo campo")
    if not {"barcode", "name", "sale_price"}.issubset(mapped):
        raise HTTPException(422, "Mapea Código, Nombre y Precio de venta")

    categories = {normalized(x.name): x for x in db.scalars(select(OperationalCategory).where(OperationalCategory.active.is_(True)))}
    suppliers = {normalized(x.name): x for x in db.scalars(select(Supplier))}
    candidates = []
    for raw in raw_rows:
        values = {target: raw.get(header, "").strip() for header, target in request.mapping.items() if target}
        candidates.append(values)
    counts = Counter(normalized(x.get("barcode", "")) for x in candidates if x.get("barcode", "").strip())
    candidate_codes = list(counts)
    existing = set(db.scalars(select(func.lower(Product.barcode)).where(func.lower(Product.barcode).in_(candidate_codes)))) if candidate_codes else set()

    results = []
    for index, values in enumerate(candidates, start=2):
        errors, warnings = [], []
        barcode, name = values.get("barcode", "").strip(), values.get("name", "").strip()
        if not barcode: errors.append("Código obligatorio")
        elif re.fullmatch(r"\d+(?:[.,]\d+)?[eE][+-]?\d+", barcode): errors.append("El código en notación científica no es seguro")
        elif barcode.startswith(("=", "+", "-", "@")): errors.append("El código contiene una fórmula o valor no permitido")
        elif normalized(barcode) in existing: errors.append(f"El código {barcode} ya existe")
        elif counts[normalized(barcode)] > 1: errors.append(f"El código {barcode} está duplicado dentro del CSV")
        if not name: errors.append("Nombre obligatorio")
        try:
            sale_price = parse_money(values.get("sale_price", ""), "Precio de venta")
            if sale_price <= 0: errors.append("Precio de venta debe ser mayor que cero")
        except ValueError as error: errors.append(str(error)); sale_price = Decimal("0")
        try:
            purchase_cost = parse_money(values.get("purchase_cost", ""), "Costo de compra")
            if purchase_cost < 0: errors.append("Costo de compra no puede ser negativo")
        except ValueError as error: errors.append(str(error)); purchase_cost = Decimal("0")
        try:
            initial_stock = parse_integer(values.get("initial_stock", ""), "Stock inicial")
            if initial_stock < 0: errors.append("Stock inicial no puede ser negativo")
        except ValueError as error: errors.append(str(error)); initial_stock = 0
        try:
            minimum_stock = parse_integer(values.get("minimum_stock", ""), "Stock mínimo")
            if minimum_stock < 0: errors.append("Stock mínimo no puede ser negativo")
        except ValueError as error: errors.append(str(error)); minimum_stock = 0
        try: active = parse_active(values.get("active", ""))
        except ValueError as error: errors.append(str(error)); active = True
        category_name, supplier_name = values.get("category", "").strip(), values.get("supplier", "").strip()
        category = categories.get(normalized(category_name)) if category_name else None
        supplier = suppliers.get(normalized(supplier_name)) if supplier_name else None
        if category_name and not category: warnings.append(f'Categoría "{category_name}" no encontrada; se importará sin clasificar')
        if supplier_name and not supplier: warnings.append(f'Proveedor "{supplier_name}" no encontrado; se importará sin proveedor')
        data = {"barcode": barcode, "name": name, "category_id": str(category.id) if category else None,
                "category": category.name if category else None, "supplier_id": str(supplier.id) if supplier else None,
                "supplier": supplier.name if supplier else None, "purchase_cost": str(purchase_cost),
                "sale_price": str(sale_price), "initial_stock": initial_stock,
                "minimum_stock": minimum_stock, "unit": values.get("unit", "").strip() or "unidad",
                "description": values.get("description", "").strip() or None, "active": active}
        status = "ERROR" if errors else "WARNING" if warnings else "READY"
        results.append(ProductImportRow(row_number=index, parsed_data=data, status=status, warnings=warnings, errors=errors))
    return ProductImportPreview(filename=request.filename, delimiter=delimiter, headers=headers,
        total_rows=len(results), ready_count=sum(x.status == "READY" for x in results),
        warning_count=sum(x.status == "WARNING" for x in results),
        error_count=sum(x.status == "ERROR" for x in results), rows=results)


def confirm_import(db: Session, request: ProductImportRequest) -> ProductImportResult:
    preview = validate_import(db, request)
    if any(int(row.parsed_data.get("initial_stock") or 0) > 0 for row in preview.rows if row.status != "ERROR"):
        require_open_session(db, lock=True)
    valid = [row for row in preview.rows if row.status != "ERROR"]
    imported, failed, stock_total = 0, [], 0
    operator_id = get_operator(db).id
    for offset in range(0, len(valid), BATCH_SIZE):
        batch = valid[offset:offset + BATCH_SIZE]
        batch_stock = 0
        try:
            for row in batch:
                body = ProductCreate(**{key: value for key, value in row.parsed_data.items()
                                        if key not in {"category", "supplier"}})
                add_product_to_session(db, body, operator_id)
                batch_stock += body.initial_stock
            db.commit(); imported += len(batch); stock_total += batch_stock
        except Exception:
            db.rollback()
            for row in batch:
                try:
                    body = ProductCreate(**{key: value for key, value in row.parsed_data.items()
                                            if key not in {"category", "supplier"}})
                    add_product_to_session(db, body, operator_id); db.commit()
                    imported += 1; stock_total += body.initial_stock
                except IntegrityError:
                    db.rollback(); row.status = "ERROR"; row.errors.append("El código fue creado por otro usuario antes de confirmar"); failed.append(row)
                except Exception:
                    db.rollback(); row.status = "ERROR"; row.errors.append("No fue posible importar esta fila por un error interno"); failed.append(row)
    skipped = preview.error_count + len(failed)
    errors = [row for row in preview.rows if row.status == "ERROR"]
    return ProductImportResult(imported_count=imported, skipped_count=skipped,
        warning_count=preview.warning_count, initial_stock_total=stock_total,
        rows=errors)
