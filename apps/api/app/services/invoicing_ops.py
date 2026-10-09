"""Operational control of electronic invoices (read-mostly). Not a fiscal ERP.

Everything here is local reads plus, at most, GET requests to Alegra (refreshing
signed PDF/XML links, optional readiness probe). Nothing here creates, modifies
or deletes a fiscal document, and nothing here toggles configuration.
"""
import csv
import io
import time
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domain.enums import ElectronicInvoiceStatus, PaymentMethod, SaleStatus
from app.models import CashSession, Customer, ElectronicInvoice, PaymentChannel, Product, Sale
from app.services.alegra_client import AlegraError
from app.services.alegra_provider import AlegraProvider
from app.services.electronic_invoicing import (DELETED_DRAFT_CODE, HOLD_CODES, REVIEW_HOLD_CODE,
                                               config_status, display_status, preflight_sale, sync_invoice_status)

DISPLAY_STATES = ("PENDING", "BLOCKED", "HELD", "PROCESSING", "DRAFT", "SENT", "ACCEPTED", "DIAN_REJECTED",
                  "REJECTED", "ERROR")

# ---- human reasons ------------------------------------------------------------------------------------------

LINE_MESSAGES = {
    # Not a problem by itself since 2026-10-02: the invoice links/creates them on demand.
    "UNMAPPED_PRODUCT": "Productos sin ítem en Alegra todavía: se vincularán o crearán automáticamente al facturar.",
}
CATALOG_OFF_MESSAGE = ("Productos sin ítem en Alegra: se crearán automáticamente al facturar cuando se active "
                       "la creación automática.")
REQUIREMENT_MESSAGES = {
    "EXTERNAL_SCALE": "La venta incluye comida a granel registrada en la báscula; la factura necesita revisión.",
    "PAYMENT_METHOD_MIXED": "La venta usó varios medios de pago y necesita revisión.",
    "PAYMENT_METHOD": "Falta un dato del medio de pago (por ejemplo, si la tarjeta fue débito o crédito).",
    "CONTACT": "No se pudo determinar el cliente de la factura.",
    "CONFIG": "La conexión con Alegra no está completamente configurada.",
    "SALE_NOT_COMPLETED": "La venta no está finalizada.",
    "NO_ITEMS": "La venta no tiene productos.",
}
HOLD_MESSAGES = {
    REVIEW_HOLD_CODE: "Venta anterior apartada para revisión manual.",
    DELETED_DRAFT_CODE: "Se creó un borrador por error en Alegra y se eliminó; esta venta se factura manualmente.",
}
ERROR_MESSAGES = {
    "AlegraNetworkError": "Alegra no respondió a tiempo. Antes de reintentar se verificará si la factura ya existe.",
    "AlegraServerError": "Alegra tuvo un error interno. Antes de reintentar se verificará si la factura ya existe.",
    "NO_INVOICE_ID": "Alegra respondió de forma incompleta. Antes de reintentar se verificará si la factura ya existe.",
    "UNEXPECTED": "Ocurrió un error inesperado durante la emisión. Se verificará en Alegra antes de reintentar.",
    "RECONCILE_FAILED": "No se pudo confirmar en Alegra si la factura ya existe; no se reenvió.",
    "AlegraValidationError": "Alegra rechazó los datos de la factura.",
    "AlegraAuthError": "Alegra rechazó las credenciales configuradas.",
    "AlegraRateLimitError": "Alegra recibió demasiadas solicitudes; se puede reintentar más tarde.",
    "CONFIG": "La conexión con Alegra no está completamente configurada.",
    "DUPLICATE_IN_ALEGRA": "Alegra tiene más de una factura para esta venta; hay que revisarlas y anular las sobrantes.",
    "STAMP_FAILED": "Alegra creó la factura como borrador pero no la timbró ante la DIAN; se completa timbrando ese mismo borrador.",
    "REJECTED_UNVERIFIED": "Alegra respondió con un error y no se pudo confirmar si creó la factura; se verificará antes de cualquier reintento.",
    "CATALOG_PENDING": "No se pudo preparar un producto en Alegra; la factura no se envió y se puede reintentar.",
    "CATALOG": "Un producto necesita revisión antes de poder crearse o vincularse en Alegra.",
    "TOTAL_MISMATCH": "El total a facturar no coincide con el total de la venta; la factura no se envió.",
    "ITEM_UNIT": "Un producto no tiene unidad de medida en Alegra; la DIAN no lo acepta. Corregirlo y reintentar.",
    "UNIT_CHECK_PENDING": "No se pudo verificar en Alegra la unidad de medida; la factura no se envió y se puede reintentar.",
    "CONTACT_PENDING": "No se pudo verificar en Alegra el contacto Consumidor final; la factura no se envió y se puede reintentar.",
}
# Codes whose own message explains the problem better than re-running the local pre-flight.
OWN_MESSAGE_CODES = ("CATALOG", "CATALOG_PENDING", "TOTAL_MISMATCH", "ITEM_UNIT", "UNIT_CHECK_PENDING")


def _manual_line_message(sale: Sale) -> str:
    from app.services.electronic_invoicing import manual_item_id, manual_kind
    kinds = {manual_kind(sale, item) for item in sale.items if item.product_id is None and not manual_item_id(sale, item)}
    if kinds == {"BULTO"}:
        return "La venta de bulto no tiene ítem de facturación configurado."
    if kinds == {"GRANEL"}:
        return "La venta de granel no tiene ítem de facturación configurado."
    if kinds == {"SUPER"}:
        return "El producto manual Súper no tiene ítem de facturación configurado en Alegra."
    if kinds == {"PET_OTHER"}:
        return "El producto de mascotas sin código (otro) no tiene ítem de facturación configurado en Alegra."
    return "La venta tiene productos manuales sin ítem de facturación configurado."


def preflight_reasons(db: Session, sale: Sale) -> list[dict]:
    """Current blockers of a sale as {code, message, detail} — messages for people,
    detail lists what exactly (e.g. which products). Local only (no Alegra call)."""
    result = preflight_sale(db, sale, allow_remote=False)
    reasons: list[dict] = []
    unmapped = [l["product_name"] for l in result["lines"] if l["code"] == "UNMAPPED_PRODUCT"]
    if unmapped and not settings.alegra_catalog_create_enabled:
        # With automatic creation on, a product without item is resolved at invoicing time
        # (not a blocker); with it off, the sale can't be invoiced yet.
        reasons.append({"code": "UNMAPPED_PRODUCT", "message": CATALOG_OFF_MESSAGE, "detail": ", ".join(unmapped)})
    if any(l["code"] == "MANUAL_LINE" for l in result["lines"]):
        reasons.append({"code": "MANUAL", "message": _manual_line_message(sale), "detail": None})
    for req in result["requirements"]:
        detail = req["message"] if req["code"] in ("PAYMENT_METHOD", "CONTACT", "CONFIG") else None
        reasons.append({"code": req["code"], "message": REQUIREMENT_MESSAGES.get(req["code"], req["message"]),
                        "detail": detail})
    return reasons


def invoice_reasons(db: Session, record: ElectronicInvoice, sale: Sale) -> list[dict]:
    if record.error_code in HOLD_CODES:
        return [{"code": record.error_code, "message": HOLD_MESSAGES[record.error_code], "detail": record.error_message}]
    if record.error_code in OWN_MESSAGE_CODES:
        return [{"code": record.error_code, "message": ERROR_MESSAGES[record.error_code], "detail": record.error_message}]
    if record.status == ElectronicInvoiceStatus.BLOCKED:
        return preflight_reasons(db, sale) or [{"code": "BLOCKED", "message": "La factura quedó bloqueada.",
                                                "detail": record.error_message}]
    if record.status == ElectronicInvoiceStatus.ERROR or record.error_code:
        return [{"code": record.error_code or "ERROR",
                 "message": ERROR_MESSAGES.get(record.error_code or "", "La emisión falló."),
                 "detail": record.error_message}]
    return []


def retryable(record: ElectronicInvoice) -> bool:
    """Only with automatic issue on (after the cutover); never held rows. Not yet in Alegra:
    PENDING (a lost background run), ERROR, BLOCKED -> claim + reconcile + pre-flight. In
    Alegra as a DRAFT of this sale -> the retry stamps that draft (never a new invoice);
    an invoice duplicated in Alegra is for a person, never retried."""
    if not settings.alegra_auto_issue or record.error_code in HOLD_CODES:
        return False
    if record.provider_invoice_id:
        return (record.provider_status or "").lower() == "draft" and not record.cufe and \
            record.error_code != "DUPLICATE_IN_ALEGRA"
    return record.status in (ElectronicInvoiceStatus.PENDING, ElectronicInvoiceStatus.ERROR,
                             ElectronicInvoiceStatus.BLOCKED)


# ---- summary / list / detail --------------------------------------------------------------------------------

def current_window(db: Session) -> tuple[datetime, datetime, str]:
    from app.services.operations import resolve_operational_window
    return resolve_operational_window(db)


def _customer_label(db: Session, record: ElectronicInvoice) -> str:
    """Who the invoice goes to: the frozen snapshot once issued, else the recipient chosen at
    checkout (ElectronicInvoice.customer_id; None = Consumidor final) — never just the sale's customer."""
    snapshot = record.customer_snapshot if record else None
    if snapshot:
        return "Consumidor final" if snapshot.get("final_consumer") else snapshot.get("name") or "—"
    customer = db.get(Customer, record.customer_id) if record.customer_id else None
    return customer.name if customer else "Consumidor final"


def _between(stmt, date_from: datetime | None, date_to: datetime | None):
    if date_from:
        stmt = stmt.where(Sale.completed_at >= date_from)
    if date_to:
        stmt = stmt.where(Sale.completed_at <= date_to)
    return stmt


def summary(db: Session, date_from: datetime | None, date_to: datetime | None) -> dict:
    rows = db.execute(_between(select(ElectronicInvoice, Sale).join(Sale, Sale.id == ElectronicInvoice.sale_id),
                               date_from, date_to)).all()
    counts = Counter(display_status(record) for record, _ in rows)
    completed = db.scalar(_between(select(func.count()).select_from(Sale).where(Sale.status == SaleStatus.COMPLETED),
                                   date_from, date_to)) or 0
    return {"date_from": date_from, "date_to": date_to, "counts": {s: counts.get(s, 0) for s in DISPLAY_STATES},
            "completed_sales": completed, "sales_without_invoice": completed - len(rows)}


def list_invoices(db: Session, *, state: str | None = None, date_from: datetime | None = None,
                  date_to: datetime | None = None, sale_number: str = "", customer: str = "",
                  page: int = 1, page_size: int = 50) -> dict:
    stmt = (select(ElectronicInvoice, Sale).join(Sale, Sale.id == ElectronicInvoice.sale_id)
            .outerjoin(Customer, Customer.id == ElectronicInvoice.customer_id))
    if date_from:
        stmt = stmt.where(Sale.completed_at >= date_from)
    if date_to:
        stmt = stmt.where(Sale.completed_at <= date_to)
    if sale_number.strip():
        stmt = stmt.where(func.upper(Sale.number).like(f"%{sale_number.strip().upper()}%"))
    if customer.strip():
        term = f"%{customer.strip().lower()}%"
        final = "consumidor final".startswith(customer.strip().lower()) or "consumidor" in customer.strip().lower()
        conditions = [func.lower(Customer.name).like(term), Customer.identification_number.like(term)]
        if final:
            conditions.append(ElectronicInvoice.customer_id.is_(None))
        stmt = stmt.where(or_(*conditions))
    rows = db.execute(stmt.order_by(Sale.completed_at.desc())).all()
    if state:
        rows = [(r, s) for r, s in rows if display_status(r) == state]
    total = len(rows)
    page_rows = rows[(page - 1) * page_size: page * page_size]
    items = []
    for record, sale in page_rows:
        reasons = invoice_reasons(db, record, sale)
        items.append({"id": record.id, "sale_id": sale.id, "sale_number": sale.number,
                      "completed_at": sale.completed_at, "customer": _customer_label(db, record),
                      "total": sale.total, "display_status": display_status(record),
                      "provider_number": record.provider_number,
                      "reason": reasons[0]["message"] if reasons else None})
    return {"items": items, "total": total, "page": page, "page_size": page_size,
            "total_pages": max(1, -(-total // page_size))}


def detail(db: Session, record: ElectronicInvoice) -> dict:
    sale = db.get(Sale, record.sale_id)
    from app.services.effective_payments import effective_channels
    effective = effective_channels(db, sale.payments)
    return {
        "id": record.id, "sale_id": sale.id, "sale_number": sale.number, "completed_at": sale.completed_at,
        "source": getattr(sale.source, "value", sale.source), "customer": _customer_label(db, record),
        "customer_snapshot": record.customer_snapshot, "total": sale.total,
        "items": [{"product_name": i.product_name, "quantity": i.quantity, "unit_price": i.unit_price,
                   "line_total": i.line_total} for i in sale.items],
        "payments": [{"channel_name": effective[p.id].name, "amount": p.amount, "card_type": p.card_type}
                     for p in sale.payments],
        "status": record.status.value, "display_status": display_status(record),
        "provider_status": record.provider_status, "legal_status": record.legal_status, "cufe": record.cufe,
        "provider_invoice_id": record.provider_invoice_id, "provider_number": record.provider_number,
        "attempt_count": record.attempt_count, "last_attempt_at": record.last_attempt_at,
        "needs_reconciliation": record.needs_reconciliation, "error_code": record.error_code,
        "error_message": record.error_message, "reasons": invoice_reasons(db, record, sale),
        "has_pdf": bool(record.provider_invoice_id), "has_xml": bool(record.provider_invoice_id and record.cufe),
        "retryable": retryable(record),
    }


# ---- PDF / XML (signed links expire) -------------------------------------------------------------------------

def _link_expired(url: str | None, margin_seconds: int = 60) -> bool:
    """Alegra hands out signed CloudFront/S3 links with `Expires=<epoch>` (seen on
    2026-09-28 for both PDF and XML). Unknown format -> treat as expired (refresh)."""
    if not url:
        return True
    query = parse_qs(urlparse(url).query)
    raw = (query.get("Expires") or query.get("expires") or [None])[0]
    if not raw or not str(raw).isdigit():
        return True
    return int(raw) <= int(time.time()) + margin_seconds


def document_link(db: Session, record: ElectronicInvoice, kind: str, provider: AlegraProvider | None = None,
                  force: bool = False) -> dict:
    """The permanent source is provider_invoice_id; the stored URL is a cache.
    Refresh = GET /invoices/{id} (read only). Never creates or alters a document."""
    if kind not in ("pdf", "xml"):
        raise ValueError("kind debe ser pdf o xml")
    if not record.provider_invoice_id:
        raise LookupError("Esta factura no existe en Alegra; no tiene PDF ni XML")
    url = record.pdf_url if kind == "pdf" else record.xml_url
    refreshed = False
    if force or _link_expired(url):
        sync_invoice_status(db, record, provider)
        url = record.pdf_url if kind == "pdf" else record.xml_url
        refreshed = True
    if not url:
        raise LookupError(f"Alegra no tiene {kind.upper()} para esta factura todavía")
    return {"url": url, "refreshed": refreshed}


# ---- blocked sales / catalog coverage ------------------------------------------------------------------------

def blocked_sales(db: Session, date_from: datetime, date_to: datetime) -> dict:
    """How many COMPLETED sales of the window could NOT be invoiced automatically
    right now, by main cause. Sales already handled (issued / parked) are counted apart."""
    sales = db.scalars(select(Sale).where(Sale.status == SaleStatus.COMPLETED, Sale.completed_at >= date_from,
                                          Sale.completed_at <= date_to)).unique().all()
    groups = Counter()
    handled = Counter()
    for sale in sales:
        record = db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale.id))
        if record is not None and (record.provider_invoice_id or record.error_code in HOLD_CODES):
            handled["INVOICED" if record.provider_invoice_id else "HELD"] += 1
            continue
        codes = {r["code"] for r in preflight_reasons(db, sale)}
        if not codes:
            groups["READY"] += 1
        elif "UNMAPPED_PRODUCT" in codes:
            groups["UNMAPPED_PRODUCT"] += 1
        elif "MANUAL" in codes:
            groups["MANUAL"] += 1
        elif codes & {"PAYMENT_METHOD", "PAYMENT_METHOD_MIXED"}:
            groups["PAYMENT_METHOD"] += 1
        else:
            groups["OTHER"] += 1
    keys = ("READY", "UNMAPPED_PRODUCT", "MANUAL", "PAYMENT_METHOD", "OTHER")
    return {"date_from": date_from, "date_to": date_to, "completed_sales": len(sales),
            "groups": {k: groups.get(k, 0) for k in keys},
            "blocked": sum(groups.get(k, 0) for k in keys if k != "READY"),
            "already_invoiced": handled.get("INVOICED", 0), "held": handled.get("HELD", 0)}


def catalog_coverage(db: Session) -> dict:
    from app.services.alegra_item_mapping import is_valid_gtin
    active = db.execute(select(Product.barcode, Product.alegra_item_id).where(Product.active.is_(True))).all()
    mapped = sum(1 for _, item in active if item)
    unmapped = [code for code, item in active if not item]
    invalid = sum(1 for code in unmapped if not is_valid_gtin((code or "").strip()))
    return {"active": len(active), "mapped": mapped, "unmapped": len(unmapped), "unmapped_invalid_barcode": invalid,
            "unmapped_valid_barcode": len(unmapped) - invalid,
            "mapped_pct": round(mapped * 100 / len(active), 1) if active else 0.0}


def unmapped_products(db: Session, q: str = "", page: int = 1, page_size: int = 50) -> dict:
    from app.services.alegra_item_mapping import is_valid_gtin
    stmt = select(Product).where(Product.active.is_(True), Product.alegra_item_id.is_(None))
    if q.strip():
        term = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(Product.name).like(term), Product.barcode.like(term)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Product.name).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [{"id": p.id, "barcode": p.barcode, "name": p.name, "sale_price": p.sale_price,
                       "valid_barcode": is_valid_gtin((p.barcode or "").strip())} for p in rows],
            "total": total, "page": page, "page_size": page_size, "total_pages": max(1, -(-total // page_size))}


def unmapped_products_csv(db: Session) -> str:
    from app.services.alegra_item_mapping import is_valid_gtin
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["product_id", "barcode", "name", "sale_price", "valid_barcode"])
    for p in db.scalars(select(Product).where(Product.active.is_(True), Product.alegra_item_id.is_(None))
                        .order_by(Product.name)):
        writer.writerow([p.id, p.barcode, p.name, p.sale_price, is_valid_gtin((p.barcode or "").strip())])
    return buffer.getvalue()


# ---- cutover readiness ---------------------------------------------------------------------------------------

def cutover_readiness(db: Session, provider: AlegraProvider | None = None, remote: bool = False) -> dict:
    """READY_FOR_CUTOVER or NOT_READY with reasons. Read-only: it never changes
    configuration nor flags. `remote=True` adds GET-only checks against Alegra."""
    from app.services.customers import resolve_final_consumer_contact_id
    checks: list[dict] = []

    def check(code, ok, message, detail=None, blocking=True):
        checks.append({"code": code, "ok": bool(ok), "blocking": blocking, "message": message, "detail": detail})

    status = config_status()
    check("ALEGRA_CONFIGURED", status["configured"], "Integración con Alegra configurada",
          None if status["configured"] else "Faltan datos de conexión o de numeración")
    check("NUMBER_TEMPLATE", bool(settings.alegra_number_template_id), "Numeración de facturación configurada")
    final_consumer = resolve_final_consumer_contact_id(db, allow_remote=False)
    check("FINAL_CONSUMER", bool(final_consumer), "Consumidor Final resuelto",
          None if final_consumer else "Sincroniza clientes en Clientes")
    channels = db.scalars(select(PaymentChannel).where(PaymentChannel.active.is_(True))).all()
    missing = [c.name for c in channels if c.payment_type != PaymentMethod.CARD and not c.alegra_payment_method]
    check("PAYMENT_MAPPINGS", not missing, "Medios de pago vinculados a la factura",
          ("Sin configurar: " + ", ".join(missing)) if missing else None)
    check("GRANEL_ITEM", bool(settings.alegra_item_manual_bulk), "Ítem de facturación para granel configurado")
    check("BULTO_ITEM", bool(settings.alegra_item_manual_sack), "Ítem de facturación para bulto configurado")
    check("MANUAL_SUPER_ITEM", bool(settings.alegra_item_manual_super),
          "Ítem de facturación para productos manuales Súper configurado",
          None if settings.alegra_item_manual_super else
          "Sin configurar (ALEGRA_ITEM_MANUAL_SUPER): una venta con producto manual Súper no se puede facturar.",
          blocking=False)
    check("MANUAL_PET_OTHER_ITEM", bool(settings.alegra_item_manual_pet_other),
          "Ítem de facturación para productos de mascotas sin código (otro) configurado",
          None if settings.alegra_item_manual_pet_other else
          "Sin configurar (ALEGRA_ITEM_MANUAL_PET_OTHER): una venta con ese producto no se puede facturar.",
          blocking=False)
    processing = db.scalar(select(func.count()).select_from(ElectronicInvoice).where(
        ElectronicInvoice.status == ElectronicInvoiceStatus.PROCESSING)) or 0
    check("NO_PROCESSING", processing == 0, "Ninguna factura atascada procesándose",
          f"{processing} en proceso" if processing else None)
    pending_reconcile = db.scalar(select(func.count()).select_from(ElectronicInvoice).where(
        ElectronicInvoice.needs_reconciliation.is_(True))) or 0
    check("NO_PENDING_RECONCILIATION", pending_reconcile == 0, "Ninguna factura con resultado incierto por verificar",
          f"{pending_reconcile} por verificar en Alegra" if pending_reconcile else None)
    open_session = db.scalar(select(CashSession).where(CashSession.closed_at.is_(None)))
    check("CASH_CLOSED", open_session is None, "Caja cerrada",
          "El cambio debe realizarse con la caja cerrada." if open_session else None)
    stale_pending = db.scalar(select(func.count()).select_from(ElectronicInvoice).where(
        ElectronicInvoice.status == ElectronicInvoiceStatus.PENDING)) or 0
    check("NO_STALE_PENDING", stale_pending == 0, "Sin facturas pendientes anteriores al cambio",
          f"{stale_pending} pendientes: apartarlas o resolverlas antes del cambio" if stale_pending else None)
    coverage = catalog_coverage(db)
    check("CATALOG_COVERAGE", True, "Cobertura del catálogo en Alegra",
          f"{coverage['mapped']} de {coverage['active']} productos activos ({coverage['mapped_pct']} %). "
          "Los pendientes se crearán automáticamente cuando sean necesarios para facturación.", blocking=False)
    check("CATALOG_AUTO_CREATE", bool(settings.alegra_catalog_create_enabled),
          "Creación automática de productos en Alegra al facturar",
          None if settings.alegra_catalog_create_enabled else
          "Desactivada (ALEGRA_CATALOG_CREATE_ENABLED). Sin ella, una venta con un producto nuevo queda bloqueada.",
          blocking=False)
    # External fiscal items (2026-10-02, ERG4613/ERG4622 accepted WITH OBSERVATIONS). Not fixable
    # from the POS: a person reviews them in Alegra/RUT. Never auto-marked as resolved.
    check("FISCAL_FAJ44B", False, "Revisar NIT y razón social del emisor en Alegra y en el RUT",
          "DIAN FAJ44b: el NIT informado no corresponde a la razón social o nombre comercial del RUT.", blocking=False)
    check("FISCAL_FAZ09", False, "Identificación estándar de los productos en la factura (DIAN FAZ09)",
          "Toda factura (POS y manual en Alegra) sale con FAZ09: el XML no lleva StandardItemIdentification. "
          "Pendiente de confirmar con Alegra qué campo lo produce.", blocking=False)
    if remote:
        _remote_checks(check, provider or AlegraProvider(), final_consumer)
    ready = all(c["ok"] for c in checks if c["blocking"])
    return {"status": "READY_FOR_CUTOVER" if ready else "NOT_READY",
            "auto_issue": bool(settings.alegra_auto_issue), "checks": checks,
            "reasons": [c["detail"] or c["message"] for c in checks if c["blocking"] and not c["ok"]],
            "checked_at": datetime.now(timezone.utc)}


def _remote_checks(check, provider: AlegraProvider, final_consumer: str | None) -> None:
    try:
        templates = provider.list_number_templates("invoice")
        template = next((t for t in templates if str(t.get("id")) == str(settings.alegra_number_template_id)), None)
        check("REMOTE_TEMPLATE", bool(template and template.get("isElectronic") and
                                      (template.get("status") in (None, "active"))),
              "La numeración existe en Alegra y es electrónica")
        for code, item_id in (("REMOTE_GRANEL", settings.alegra_item_manual_bulk),
                              ("REMOTE_BULTO", settings.alegra_item_manual_sack),
                              ("REMOTE_MANUAL_SUPER", settings.alegra_item_manual_super)):
            if item_id:
                item = provider.client.get(f"/items/{item_id}") or {}
                check(code, item.get("status") == "active", f"El ítem {item_id} existe y está activo en Alegra")
                unit = (item.get("inventory") or {}).get("unit")
                check(f"{code}_UNIT", bool(unit), f"El ítem {item_id} tiene unidad de medida (la DIAN la exige)",
                      None if unit else f"Ítem {item_id} sin inventory.unit: la DIAN rechaza timbrarlo")
        if final_consumer:
            from app.services.customers import final_consumer_problem
            problem = final_consumer_problem(provider.client.get(f"/contacts/{final_consumer}"), final_consumer)
            check("REMOTE_FINAL_CONSUMER", problem is None,
                  "Consumidor Final existe en Alegra, activo y con identificación 222222222222", problem)
    except AlegraError as exc:
        check("REMOTE_REACHABLE", False, "Alegra respondió a las consultas de verificación", str(exc))


# ---- factual cutover report (no score) -----------------------------------------------------------------------

def payment_readiness(db: Session, since=None) -> dict:
    """Which payment cases produce a complete invoice today, straight from the
    same rules pre-flight uses — plus how often each blocked case happened in real sales."""
    from app.services.catalog_coverage import catalog_since, real_sales
    from app.services.electronic_invoicing import payment_terms
    from app.services.payment_methods import CARD_TYPE_METHODS, requires_card_type
    channels = db.scalars(select(PaymentChannel).where(PaymentChannel.active.is_(True))
                          .order_by(PaymentChannel.display_order)).all()
    cases = []
    for channel in channels:
        if requires_card_type(channel.payment_type):
            cases.append({"case": f"{channel.name} · Débito", "ready": True, "method": CARD_TYPE_METHODS["DEBIT"]})
            cases.append({"case": f"{channel.name} · Crédito", "ready": True, "method": CARD_TYPE_METHODS["CREDIT"]})
            cases.append({"case": f"{channel.name} sin Débito/Crédito", "ready": False, "method": None})
        else:
            cases.append({"case": channel.name, "ready": bool(channel.alegra_payment_method),
                          "method": channel.alegra_payment_method})
    cases.append({"case": "Pago dividido con el mismo medio fiscal (p. ej. Nequi + Daviplata)", "ready": True, "method": "único"})
    cases.append({"case": "Pago dividido con medios fiscales distintos (Efectivo + Nequi, Débito + Crédito)",
                  "ready": False, "method": None,
                  "note": "La API de Alegra admite un solo paymentMethod por factura; falta decisión contable"})
    sales, _ = real_sales(db, since if since is not None else catalog_since(db))
    blocked = Counter()
    for sale in sales:
        _, problem = payment_terms(sale)
        if problem:
            blocked[problem["code"]] += 1
    return {"cases": cases, "real_sales": len(sales), "real_sales_blocked_by_payment": dict(blocked)}


def cutover_report(db: Session) -> dict:
    from app.services.catalog_coverage import coverage
    readiness = cutover_readiness(db)
    cov = coverage(db)
    open_session = db.scalar(select(CashSession).where(CashSession.closed_at.is_(None)))
    technical_fail = [c for c in readiness["checks"] if c["blocking"] and not c["ok"] and c["code"] != "CASH_CLOSED"]
    return {
        "technical": {"pass": not technical_fail, "reasons": [c["detail"] or c["message"] for c in technical_fail]},
        "catalog": cov,
        "payments": payment_readiness(db, cov["since"]),
        "manual": {"granel": {"ready": bool(settings.alegra_item_manual_bulk), "item_id": settings.alegra_item_manual_bulk or None},
                   "bulto": {"ready": bool(settings.alegra_item_manual_sack), "item_id": settings.alegra_item_manual_sack or None}},
        "operational": {"cash": "OPEN" if open_session else "CLOSED",
                        "note": "El cambio debe realizarse con la caja cerrada." if open_session else None,
                        "auto_issue": bool(settings.alegra_auto_issue)},
        "generated_at": datetime.now(timezone.utc),
    }
