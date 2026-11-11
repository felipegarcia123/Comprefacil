"""Experimental, side-effect-free receipt assistance.

The service deliberately returns a reviewable draft.  It never creates a Purchase,
InventoryMovement or Product.  Vision providers can implement the adapter contract
later; the local adapter is deterministic and supports text/CSV fixtures for tests.
"""

import base64
import csv
import hashlib
import io
import json
import re
import unicodedata
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Protocol
from uuid import uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import OperationalCategory, Product
from app.schemas.receipt_ai import (CatalogDiagnostic, ReceiptAnalyzeRequest,
                                    ReceiptCandidate, ReceiptLine)
from app.services.inventory import stock_subquery


class VisionProvider(Protocol):
    """Provider boundary for a future multimodal adapter (OpenAI/local/private)."""

    name: str

    def extract(self, db: Session, content: bytes, *, filename: str, vendor_hint: str | None, region) -> list[ReceiptLine]: ...


class VisionProviderError(RuntimeError):
    pass


def _norm(value: str | None) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _decimal(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip().replace("$", "").replace(" ", "")
    # Colombian documents commonly use 1.234,56; accept plain decimals too.
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    else:
        raw = raw.replace(",", ".")
    try:
        return str(Decimal(raw))
    except InvalidOperation:
        return None


def _number(value: str | None) -> str | None:
    return _decimal(value)


def _catalog(db: Session):
    stocks = stock_subquery()
    rows = db.execute(
        select(Product, OperationalCategory.name, func.coalesce(stocks.c.current_stock, 0))
        .outerjoin(OperationalCategory, OperationalCategory.id == Product.operational_category_id)
        .outerjoin(stocks, stocks.c.product_id == Product.id)
        .where(Product.active.is_(True))
        .order_by(Product.name)
    ).all()
    return rows


def _volume_ml(value: str | None) -> int | None:
    raw = unicodedata.normalize("NFKD", value or "").lower().replace(",", ".")
    match = re.search(r"(\d+(?:\.\d+)?)\s*(ml|l|lt|litro|litros)", raw)
    if not match:
        return None
    amount = Decimal(match.group(1).replace(",", "."))
    return int(amount * (1000 if match.group(2).startswith("l") else 1))


def _profile_tokens(value: str | None) -> set[str]:
    return {token for token in _norm(value).split() if len(token) > 1}


def _brand_tokens(name: str | None, brand: str | None, vendor_hint: str | None) -> set[str]:
    explicit = _profile_tokens(brand)
    if explicit:
        return explicit
    # A supplier hint is stronger than a generic token such as "fresh" in a
    # product description.  Do not let that token match an unrelated brand.
    source = _profile_tokens(vendor_hint) if vendor_hint else _profile_tokens(name)
    known = {"coca", "cola", "quatro", "brisa", "colanta", "postobon", "axe", "fresh"}
    return source & known


def _variant_tokens(name: str | None, variant: str | None) -> set[str]:
    tokens = _profile_tokens(variant) | _profile_tokens(name)
    return tokens & {"zero", "original", "clasica", "clásica", "retornable", "rp", "choice", "citrus"}


def find_candidates(db: Session, name: str | None, barcode: str | None = None, limit: int = 5,
                    *, brand: str | None = None, variant: str | None = None,
                    size: str | None = None, vendor_hint: str | None = None,
                    material_code: str | None = None) -> list[ReceiptCandidate]:
    if material_code and vendor_hint:
        from app.services.receipt_equivalences import active_equivalence
        remembered = active_equivalence(db, vendor_hint, material_code)
        if remembered:
            product = db.scalar(select(Product).where(Product.id == remembered.product_id, Product.active.is_(True)))
            if not product:
                # A remembered target was deactivated or removed: never fall
                # back to a different fuzzy match for the same supplier code.
                return []
            category = db.scalar(select(OperationalCategory.name).where(OperationalCategory.id == product.operational_category_id))
            stocks = stock_subquery()
            stock = db.scalar(select(func.coalesce(stocks.c.current_stock, 0)).where(stocks.c.product_id == product.id)) or 0
            remembered_candidate = ReceiptCandidate(
                product_id=str(product.id), name=product.name, barcode=product.barcode,
                category=category, unit=product.unit, current_stock=str(stock), score=1.0,
                reasons=["equivalencia confirmada manualmente"])
            return [remembered_candidate]
    query = _norm(name)
    source_brand = _brand_tokens(name, brand, vendor_hint)
    source_variant = _variant_tokens(name, variant)
    source_volume = _volume_ml(size) or _volume_ml(name)
    barcode_norm = (barcode or "").strip()
    scored = []
    for product, category, stock in _catalog(db):
        product_name = _norm(product.name)
        score = 0.0
        reasons: list[str] = []
        candidate_tokens = _profile_tokens(product.name)
        candidate_volume = _volume_ml(product.name)
        brand_overlap = source_brand & candidate_tokens
        brand_conflict = bool(source_brand) and not brand_overlap
        volume_conflict = bool(source_volume and candidate_volume and source_volume != candidate_volume)
        if barcode_norm and product.barcode == barcode_norm:
            score = 1.0
            reasons.append("código exacto")
        elif query:
            if query == product_name:
                score = 0.96
                reasons.append("nombre exacto")
            else:
                q_tokens = set(query.split())
                p_tokens = set(product_name.split())
                overlap = len(q_tokens & p_tokens) / max(len(q_tokens), 1)
                if query in product_name:
                    score = max(score, 0.84)
                    reasons.append("nombre contenido")
                if overlap:
                    score = max(score, 0.45 + 0.35 * overlap)
                    reasons.append(f"tokens coincidentes {overlap:.0%}")
            if brand_overlap:
                score += 0.18
                reasons.append("marca coincidente")
            elif brand_conflict:
                score -= 0.55
                reasons.append("marca incompatible")
            if source_volume and candidate_volume:
                if volume_conflict:
                    score -= 0.40
                    reasons.append(f"volumen incompatible ({candidate_volume} ml)")
                else:
                    score += 0.16
                    reasons.append("volumen coincidente")
            if source_variant:
                variant_overlap = source_variant & candidate_tokens
                if variant_overlap:
                    score += 0.12
                    reasons.append("variante coincidente")
                elif "zero" in source_variant and "zero" not in candidate_tokens:
                    score -= 0.35
                    reasons.append("variante incompatible")
        # Below this threshold the candidate is too weak to be useful and is
        # more likely to be a misleading textual association than a review aid.
        if score < 0.5:
            continue
        scored.append((score, product.name, ReceiptCandidate(
            product_id=str(product.id), name=product.name, barcode=product.barcode,
            category=category, unit=product.unit, current_stock=str(stock),
            score=round(min(score, 1.0), 3), reasons=reasons)))
    scored.sort(key=lambda row: (-row[0], _norm(row[1])))
    return [row[2] for row in scored[:limit]]


def _parse_text(content: bytes, db: Session, region) -> list[ReceiptLine]:
    text = content.decode("utf-8-sig", errors="replace")
    first_line = text.splitlines()[0] if text.splitlines() else ""
    reader = csv.DictReader(io.StringIO(text)) if "," in first_line else None
    rows: list[dict[str, str]] = []
    if reader:
        rows = [{(key or "").strip().lower(): (value or "").strip() for key, value in row.items()} for row in reader]
    else:
        for raw in text.splitlines():
            if raw.strip():
                parts = re.split(r"\t|;|\|", raw)
                rows.append({"name": parts[0].strip(), "quantity": parts[1].strip() if len(parts) > 1 else "", "unit_cost": parts[2].strip() if len(parts) > 2 else ""})
    lines: list[ReceiptLine] = []
    for row in rows:
        raw = row.get("original_text") or row.get("text") or row.get("name") or ""
        name = row.get("name") or row.get("product") or row.get("description") or raw
        barcode = row.get("barcode") or row.get("code") or row.get("supplier_code")
        packages = _number(row.get("packages") or row.get("package_quantity") or row.get("quantity"))
        units = _number(row.get("units_per_package") or row.get("pack_size") or row.get("units"))
        total = None
        if packages and units:
            total = str(Decimal(packages) * Decimal(units))
        elif row.get("total_units"):
            total = _number(row.get("total_units"))
        candidates = find_candidates(db, name, barcode, brand=row.get("brand"), variant=row.get("variant"),
                                     size=row.get("size") or row.get("presentation"))
        state = "READY" if total and candidates and candidates[0].score >= 0.9 else ("UNMATCHED" if not candidates else "REVIEW_REQUIRED")
        lines.append(ReceiptLine(
            line_id=str(uuid4()), original_text=raw, supplier_code=barcode,
            interpreted_name=name or None, brand=row.get("brand") or None, variant=row.get("variant") or None,
            size=row.get("size") or row.get("presentation") or None, package_quantity=packages,
            units_per_package=units, total_units=total, unit_cost=_number(row.get("unit_cost") or row.get("cost")),
            discount=_number(row.get("discount")), confidence=0.95 if state == "READY" else 0.55,
            review_state=state, region=region, candidates=candidates))
    return lines


def _vision_schema() -> dict:
    nullable_number = {"type": ["number", "null"]}
    nullable_string = {"type": ["string", "null"]}
    return {
        "type": "object", "additionalProperties": False,
        "properties": {"lines": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "original_text": {"type": "string"}, "supplier_code": nullable_string,
                "interpreted_name": nullable_string, "brand": nullable_string, "variant": nullable_string,
                "size": nullable_string, "package_quantity": nullable_number,
                "units_per_package": nullable_number, "total_units": nullable_number,
                "unit_cost": nullable_number, "discount": nullable_number,
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "ambiguities": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["original_text", "supplier_code", "interpreted_name", "brand", "variant", "size",
                         "package_quantity", "units_per_package", "total_units", "unit_cost", "discount",
                         "confidence", "ambiguities"],
        }}},
        "required": ["lines"],
    }


class OpenAIVisionProvider:
    name = "openai-responses"

    def __init__(self, api_key: str, model: str, base_url: str, timeout_seconds: int):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def extract(self, db: Session, content: bytes, *, filename: str, vendor_hint: str | None, region) -> list[ReceiptLine]:
        media_type = "image/png" if filename.lower().endswith(".png") else "image/jpeg"
        region_note = "No hay región seleccionada; revisa toda la imagen."
        if region:
            region_note = (f"Prioriza la región relativa x={region.x}, y={region.y}, "
                           f"ancho={region.width}, alto={region.height}, pero conserva el contexto de la imagen completa.")
        prompt = f"""Eres un extractor de recepción de mercancía para un supermercado colombiano.
Lee únicamente líneas de productos visibles en la imagen. Proveedor sugerido: {vendor_hint or 'desconocido'}.
{region_note}
No inventes valores: usa null cuando el dato no sea legible. No conviertas cajas a unidades salvo que
el documento muestre explícitamente el factor. total_units solo puede calcularse si ambos factores están
explícitos; de lo contrario debe ser null y debes incluir una ambigüedad. Conserva el texto original.
No asignes categorías del inventario ni elijas productos del catálogo. Devuelve solo el JSON solicitado."""
        body = {
            "model": self.model,
            "store": False,
            "input": [{"role": "user", "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": f"data:{media_type};base64,{base64.b64encode(content).decode('ascii')}", "detail": "high"},
            ]}],
            "text": {"format": {"type": "json_schema", "name": "receipt_lines", "strict": True, "schema": _vision_schema()}},
        }
        try:
            response = httpx.post(f"{self.base_url}/responses", headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, json=body, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise VisionProviderError("El proveedor multimodal no respondió correctamente") from exc
        output_text = payload.get("output_text")
        if not output_text:
            for item in payload.get("output", []):
                for part in item.get("content", []):
                    if part.get("type") == "output_text":
                        output_text = part.get("text")
                        break
                if output_text:
                    break
        if not output_text:
            raise VisionProviderError("La respuesta multimodal no contenía datos estructurados")
        try:
            extracted = json.loads(output_text).get("lines", [])
        except (TypeError, json.JSONDecodeError) as exc:
            raise VisionProviderError("La respuesta multimodal no era JSON válido") from exc
        result: list[ReceiptLine] = []
        for raw in extracted:
            name = raw.get("interpreted_name") or raw.get("original_text") or ""
            supplier_code = raw.get("supplier_code")
            barcode = supplier_code if supplier_code and re.fullmatch(r"\d{8,14}", supplier_code) else None
            candidates = find_candidates(db, name, barcode, brand=raw.get("brand"), variant=raw.get("variant"),
                                         size=raw.get("size"), vendor_hint=vendor_hint, material_code=supplier_code)
            ambiguities = raw.get("ambiguities") or []
            total_units = raw.get("total_units")
            state = "READY" if total_units is not None and candidates and candidates[0].score >= 0.9 and not ambiguities else ("UNMATCHED" if not candidates else "REVIEW_REQUIRED")
            result.append(ReceiptLine(
                line_id=str(uuid4()), original_text=raw.get("original_text") or name,
                supplier_code=supplier_code, interpreted_name=raw.get("interpreted_name"), brand=raw.get("brand"),
                variant=raw.get("variant"), size=raw.get("size"), package_quantity=_number(str(raw["package_quantity"])) if raw.get("package_quantity") is not None else None,
                units_per_package=_number(str(raw["units_per_package"])) if raw.get("units_per_package") is not None else None,
                total_units=_number(str(total_units)) if total_units is not None else None,
                unit_cost=_number(str(raw["unit_cost"])) if raw.get("unit_cost") is not None else None,
                discount=_number(str(raw["discount"])) if raw.get("discount") is not None else None,
                confidence=float(raw.get("confidence") or 0), review_state=state, region=region, candidates=candidates))
        return result


def analyze(db: Session, body: ReceiptAnalyzeRequest):
    try:
        content = base64.b64decode(body.content_base64, validate=True)
    except Exception as exc:
        raise ValueError("La imagen no está codificada en base64 válida") from exc
    digest = hashlib.sha256(content).hexdigest()
    is_text = body.content_type.startswith("text/") or body.filename.lower().endswith((".csv", ".txt"))
    if is_text:
        lines = _parse_text(content, db, body.region)
        return digest, lines, "local-text-fixture", body.vendor_hint, "Borrador estructurado; confirme cada línea antes de convertirlo en compra."
    if body.content_type.lower() not in {"image/jpeg", "image/jpg", "image/png"} and not body.filename.lower().endswith((".jpg", ".jpeg", ".png")):
        raise ValueError("Solo se admiten imágenes JPG, JPEG o PNG")
    if not settings.receipt_ai_vision_enabled or not settings.receipt_ai_openai_api_key:
        return digest, [], "vision-provider-not-configured", body.vendor_hint, "Imagen recibida. Active RECEIPT_AI_VISION_ENABLED y configure una clave para procesarla; no se inventaron datos."
    provider = OpenAIVisionProvider(settings.receipt_ai_openai_api_key, settings.receipt_ai_openai_model,
                                    settings.receipt_ai_openai_base_url, settings.receipt_ai_openai_timeout_seconds)
    lines = provider.extract(db, content, filename=body.filename, vendor_hint=body.vendor_hint, region=body.region)
    return digest, lines, provider.name, body.vendor_hint, "Extracción multimodal lista para revisión humana; ninguna línea modifica inventario."


def diagnostic(db: Session) -> CatalogDiagnostic:
    rows = _catalog(db)
    names = Counter(_norm(product.name) for product, _, _ in rows if _norm(product.name))
    duplicates = [name for name, count in names.items() if count > 1]
    return CatalogDiagnostic(
        active_products=len(rows), with_barcode=sum(1 for product, _, _ in rows if product.barcode),
        without_barcode=sum(1 for product, _, _ in rows if not product.barcode),
        duplicate_normalized_names=len(duplicates), ambiguous_name_examples=duplicates[:20],
    )

