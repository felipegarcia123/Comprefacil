"""Internal electronic-invoicing service.

Sale COMPLETED stays the operational source of truth. This module only ever
reads Sale/SaleItem and writes ElectronicInvoice — it never touches Payment,
CashSession, CashMovement, or InventoryMovement. A failure anywhere here
(network, Alegra validation, missing mapping) can only ever leave an
ElectronicInvoice row in ERROR; it can never revert or alter the sale.

Layering, so a future delivery channel never touches KPI/invoice calculation:
  validate_sale_for_invoicing() -> list of blocking reasons (empty = OK)
  build_alegra_invoice_payload() -> pure payload dict, no I/O
  create_electronic_invoice_record() -> idempotent PENDING row (unique sale_id)
  preflight_sale()       -> READY/BLOCKED with every reason, no I/O besides contact lookup
  invoice_plan()         -> what would be sent, catalog included (simulation, never writes)
  attempt_issue()        -> claim + reconcile + ensure items + pre-flight + POST (the only POST path)
  issue_invoice()/retry_invoice() -> thin wrappers over attempt_issue()
  sync_invoice_status()  -> re-fetch from Alegra, update provider/legal status

Anti-duplicate (Alegra has no idempotency key): the POST carries
"POS sale_id=<uuid>" in `observations`; any attempt whose previous POST outcome
is unknown (timeout, 5xx, crash) first searches GET /invoices by the exact
date + client_id sent and matches that marker. Found -> linked, never re-POSTed.
Search failed -> stays ERROR, never re-POSTed. Only "searched and absent" POSTs.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.domain.enums import ElectronicInvoiceStatus, SaleStatus
from app.models import Customer, ElectronicInvoice, Sale
from app.services.alegra_client import (AlegraAuthError, AlegraError, AlegraNotConfigured,
                                        AlegraRateLimitError, AlegraValidationError)
from app.services.alegra_provider import AlegraProvider
from app.services import alegra_catalog_sync as sync
from app.services.alegra_catalog_sync import ensure_sale_items

logger = logging.getLogger(__name__)

LOCAL_TZ = ZoneInfo("America/Bogota")
# A PROCESSING row older than this is a crashed attempt and may be reclaimed (after reconciling).
STALE_PROCESSING = timedelta(minutes=10)
MAX_RECONCILE_PAGES = 20
# Answers that prove Alegra did NOT create the invoice (request rejected before
# processing). Everything else (timeout, 5xx, unreadable answer) is ambiguous.
DEFINITIVE_ERRORS = (AlegraValidationError, AlegraAuthError, AlegraRateLimitError, AlegraNotConfigured)


class InvoicingConfigError(Exception):
    """Alegra disabled/misconfigured — distinct from a per-sale validation error."""


class SaleValidationError(Exception):
    """The sale itself isn't invoiceable yet. `reasons` lists every blocker
    found (not just the first) so the caller can show them all at once."""
    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class AlreadyIssuedError(Exception):
    """Refuse to re-POST an invoice Alegra already accepted."""


class InvoiceInProgressError(Exception):
    """Another attempt currently owns this invoice (atomic claim lost)."""


class RemoteDraftError(Exception):
    """The sale already has a DRAFT invoice in Alegra: it is finished by stamping it
    (stamp_existing_invoice), never by POSTing another invoice."""


class InvoiceHeldForReviewError(Exception):
    """The record is parked for manual review (e.g. a pre-cutover leftover) and
    must never be sent by retry or auto-issue until a human releases it."""


# error_codes that park a BLOCKED record outside the pipeline (no claim, no retry, no auto-issue).
# Alegra created the invoice but left it as a DRAFT (stamp/DIAN step failed, e.g. answered
# 400 after creating it). The sale is completed by stamping THAT invoice, never a new one.
STAMP_FAILED_CODE = "STAMP_FAILED"
REJECTION_KEY = "_rejection_400"          # kept inside provider_response, survives syncs
REVIEW_HOLD_CODE = "LEGACY_PENDING_REVIEW"
DELETED_DRAFT_CODE = "ACCIDENTAL_REMOTE_DRAFT_DELETED"
HOLD_CODES = (REVIEW_HOLD_CODE, DELETED_DRAFT_CODE)


def _connection_missing() -> list[str]:
    """What's needed to talk to Alegra at all (any GET). Does NOT include
    ALEGRA_NUMBER_TEMPLATE_ID — that's only required to build/issue an
    invoice, never to audit the account read-only."""
    from app.core.config import settings
    missing = []
    if not settings.alegra_enabled:
        missing.append("ALEGRA_ENABLED")
    if not settings.alegra_email:
        missing.append("ALEGRA_EMAIL")
    if not settings.alegra_token:
        missing.append("ALEGRA_TOKEN")
    if not settings.alegra_api_url:
        missing.append("ALEGRA_API_URL")
    return missing


def connection_status() -> dict:
    """Gate for read-only operations (audit, list templates/taxes/items/contacts,
    get an invoice). No network calls."""
    from app.core.config import settings
    missing = _connection_missing()
    return {"enabled": settings.alegra_enabled, "configured": not missing, "missing": missing}


def config_status() -> dict:
    """Gate for actually building/issuing an invoice — connection requirements
    PLUS ALEGRA_NUMBER_TEMPLATE_ID. Used by /config-status and by the POS
    invoicing panel to decide whether to show the 'issue' action at all."""
    from app.core.config import settings
    missing = _connection_missing()
    if not settings.alegra_number_template_id:
        missing.append("ALEGRA_NUMBER_TEMPLATE_ID")
    return {"enabled": settings.alegra_enabled, "configured": not missing, "missing": missing}


def audit_account(provider: AlegraProvider | None = None) -> dict:
    """Read-only inspection of the real Alegra account: company info, number
    templates (flagging the isElectronic ones), taxes, a handful of contacts/
    items/recent invoices. Never creates anything. Returns `missing` instead
    of raising when configuration is incomplete — callers show that as-is,
    never inventing values for what isn't there."""
    status = connection_status()
    if not status["configured"]:
        return {**status, "company": None, "number_templates": [], "recommended_number_template_id": None,
                "configured_number_template_valid": None, "taxes": [], "sample_contacts": [],
                "sample_items": [], "recent_invoices": [], "error": None}

    from app.core.config import settings
    provider = provider or AlegraProvider()
    result: dict = {**status}
    try:
        result["company"] = provider.get_company()
        templates = provider.list_number_templates("invoice")
        result["number_templates"] = templates
        electronic_templates = [t for t in templates if t.get("isElectronic")]
        preferred = next((t for t in electronic_templates if t.get("isDefault")), None) or (
            electronic_templates[0] if electronic_templates else None)
        result["recommended_number_template_id"] = preferred.get("id") if preferred else None
        configured_ids = {t.get("id") for t in templates}
        result["configured_number_template_valid"] = (
            settings.alegra_number_template_id in configured_ids
            and any(t.get("id") == settings.alegra_number_template_id and t.get("isElectronic")
                   for t in templates)) if settings.alegra_number_template_id else False
        result["taxes"] = provider.list_taxes()
        result["sample_contacts"] = provider.search_contacts(limit=5)
        result["sample_items"] = provider.search_items(limit=5)
        result["recent_invoices"] = provider.list_recent_invoices(limit=5)
        result["error"] = None
    except AlegraError as exc:
        logger.warning("electronic_invoicing_audit_failed error=%s", type(exc).__name__)
        result.setdefault("company", None); result.setdefault("number_templates", [])
        result.setdefault("recommended_number_template_id", None)
        result.setdefault("configured_number_template_valid", None)
        result.setdefault("taxes", []); result.setdefault("sample_contacts", [])
        result.setdefault("sample_items", []); result.setdefault("recent_invoices", [])
        result["error"] = str(exc)
    return result


def get_electronic_invoice_for_sale(db: Session, sale_id: UUID) -> ElectronicInvoice | None:
    return db.scalar(select(ElectronicInvoice).where(ElectronicInvoice.sale_id == sale_id))


def add_pending_invoice(db: Session, sale: Sale) -> None:
    """Inside the sale's OWN transaction (checkout, manual sale), no I/O: a sale whose cashier
    ticked "Factura electrónica" (requires_electronic_invoice) gets its PENDING row, so a lost
    background run is still visible. Independent of ALEGRA_AUTO_ISSUE (off: it waits as PENDING
    for the controlled/administrative issue). Alegra is contacted only after the commit."""
    if sale.requires_electronic_invoice:
        db.add(ElectronicInvoice(sale_id=sale.id, status=ElectronicInvoiceStatus.PENDING,
                                 customer_id=sale.invoice_customer_id))


def schedule_auto_issue(background, sale: Sale) -> None:
    """ALEGRA_AUTO_ISSUE = issue in the background the sales that ASKED for an invoice (never
    "invoice every sale"). After the response: the sale is already committed; Alegra can only
    ever move its ElectronicInvoice row, never the sale, its payments, inventory or cash.
    ALEGRA_AUTO_ISSUE_STAMP keeps its meaning: generateStamp (send to the DIAN)."""
    from app.core.config import settings
    if settings.alegra_auto_issue and sale.requires_electronic_invoice:
        background.add_task(auto_issue_for_sale, sale.id)


def create_electronic_invoice_record(db: Session, sale: Sale) -> ElectronicInvoice:
    """Idempotent: UNIQUE(sale_id) means a second call just returns the
    existing row untouched, in whatever status it already has."""
    existing = get_electronic_invoice_for_sale(db, sale.id)
    if existing:
        return existing
    record = ElectronicInvoice(sale_id=sale.id, status=ElectronicInvoiceStatus.PENDING)
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def validate_sale_for_invoicing(sale: Sale) -> list[str]:
    """Line-level blockers as plain messages (kept for callers/tests); the
    structured version is preflight_sale()."""
    return [line["reason"] for line in _line_blockers(sale)] + (
        ["La venta no está COMPLETED"] if sale.status != SaleStatus.COMPLETED else []) + (
        ["La venta no tiene artículos"] if not sale.items else [])


LEGACY_MANUAL_KIND = {"MANUAL_BULK": "GRANEL", "MANUAL_SACK": "BULTO"}


def manual_kind(sale: Sale, line=None) -> str | None:
    """SUPER / GRANEL / BULTO of a manual line: its own manual_kind (lines added to a normal sale,
    migration 0025) or, for historical manual sales, the sale's source."""
    if line is not None and getattr(line, "manual_kind", None):
        return line.manual_kind
    return LEGACY_MANUAL_KIND.get(getattr(sale.source, "value", sale.source))


def manual_item_id(sale: Sale, line=None) -> str | None:
    """Generic Alegra item for a manual line (no catalog product), by its kind. Configured in
    settings: GRANEL -> ALEGRA_ITEM_MANUAL_BULK (372), BULTO -> ALEGRA_ITEM_MANUAL_SACK (2988),
    SUPER -> ALEGRA_ITEM_MANUAL_SUPER (empty until approved -> the invoice is BLOCKED)."""
    from app.core.config import settings
    return {"GRANEL": settings.alegra_item_manual_bulk, "BULTO": settings.alegra_item_manual_sack,
            "SUPER": settings.alegra_item_manual_super,
            "PET_OTHER": settings.alegra_item_manual_pet_other}.get(manual_kind(sale, line)) or None


def _line_blockers(sale: Sale) -> list[dict]:
    lines: list[dict] = []
    for item in sale.items:
        base = {"sale_item_id": str(item.id), "product_id": str(item.product_id) if item.product_id else None,
                "product_name": item.product_name}
        if item.product_id is None:
            if manual_item_id(sale, item):
                continue  # billed as the generic item (GRANEL 372 / BULTO 2988 / SUPER) at the line's real price
            kind = manual_kind(sale, item)
            reason = ("Venta de bulto: todavía no existe el ítem genérico BULTO en Alegra" if kind == "BULTO" else
                      f"Producto manual Súper '{item.product_name}': sin ítem genérico de Alegra configurado "
                      "(ALEGRA_ITEM_MANUAL_SUPER)" if kind == "SUPER" else
                      f"Producto de mascotas sin código '{item.product_name}': sin ítem genérico de Alegra "
                      "configurado (ALEGRA_ITEM_MANUAL_PET_OTHER)" if kind == "PET_OTHER" else
                      f"Línea '{item.product_name}' no corresponde a un producto del catálogo "
                      "(venta manual) — sin ítem de Alegra configurado")
            lines.append({**base, "code": "MANUAL_LINE", "reason": reason})
            continue
        product = item.product
        if product is None or not product.alegra_item_id:
            lines.append({**base, "code": "UNMAPPED_PRODUCT",
                          "reason": f"Producto '{item.product_name}' no tiene alegra_item_id mapeado"})
        # alegra_tax_id is intentionally optional: this account's real invoices
        # carry tax: [] on every line ("No responsable de IVA"). Not a blocker.
    return lines


def item_unit(provider: AlegraProvider, item_id: str) -> str | None:
    """inventory.unit of an Alegra item (GET /items/{id}); None when it has none.
    Raises AlegraError when Alegra can't be asked: 'unknown' is never 'has a unit'."""
    item = provider.client.get(f"/items/{item_id}") or {}
    return (item.get("inventory") or {}).get("unit") or None


def missing_unit_reasons(sale: Sale, provider: AlegraProvider, skip: set[str] | frozenset = frozenset()) -> list[str]:
    """Every Alegra item of the sale's lines (catalog product or generic GRANEL/BULTO) without
    unit of measure: the DIAN rejects stamping it (code 3061; ERG4622 stamped only once its
    item had unit="unit"). One GET per distinct item; `skip` = items created in this same
    attempt (created with unit="unit"). Lines with no item id yet are not checked here."""
    reasons, seen = [], set()
    for line in sale.items:
        item_id = (line.product.alegra_item_id if line.product else None) if line.product_id else manual_item_id(sale, line)
        if not item_id or item_id in seen or item_id in skip:
            continue
        seen.add(item_id)
        if not item_unit(provider, item_id):
            reasons.append(f"El ítem {item_id} de Alegra ({line.product_name}) no tiene unidad de medida; "
                           "la DIAN no lo timbra")
    return reasons


MIXED_PAYMENT_MESSAGE ="Esta venta usó varios medios de pago y necesita revisión antes de facturarse."


def payment_terms(sale: Sale) -> tuple[dict | None, dict | None]:
    """Alegra's paymentForm/paymentMethod for the sale, or why it can't be set.

    Per Alegra's docs (Colombia, e-invoicing 2.1) paymentMethod is mandatory when
    paymentForm is CASH. Each payment resolves through services/payment_methods.py
    (channel mapping, or Débito/Crédito for card channels); anything unresolved
    blocks instead of sending an incomplete or guessed payload."""
    from app.services.payment_methods import method_for_payment
    payments = list(sale.payments or [])
    if not payments:
        return None, {"code": "PAYMENT_METHOD",
                      "message": "La venta no tiene pagos registrados; no se puede determinar el medio de pago"}
    from sqlalchemy.orm import object_session
    from app.services.effective_payments import effective_channels
    effective = effective_channels(object_session(sale), payments)
    methods, problems = set(), []
    for payment in payments:
        method, problem = method_for_payment(payment, effective[payment.id])
        if problem:
            problems.append(problem)
        else:
            methods.add(method)
    if problems:
        return None, {"code": "PAYMENT_METHOD", "message": "Medio de pago sin definir: " + "; ".join(problems)}
    if len(methods) > 1:
        # Alegra takes ONE paymentMethod per invoice. Provisional rule (approved
        # 2026-09-28): same fiscal method -> READY; different -> BLOCKED for review.
        # Never pick one automatically (e.g. the largest amount).
        return None, {"code": "PAYMENT_METHOD_MIXED", "message": MIXED_PAYMENT_MESSAGE}
    return {"paymentForm": "CASH", "paymentMethod": methods.pop()}, None


def invoice_date(sale: Sale) -> str:
    """The sale's LOCAL (Colombia) calendar date. completed_at is UTC: after
    19:00 in Bogotá its UTC date is already tomorrow."""
    moment = sale.completed_at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(LOCAL_TZ).date().isoformat()


def _money_json(value: Decimal) -> float:
    """Decimal -> JSON number, only at this wire boundary. All calculation
    upstream stays Decimal; this never does arithmetic, only representation.
    Python's json encoder emits the shortest decimal string that round-trips
    exactly back to the resulting float (repr algorithm), so for POS-scale
    amounts this introduces no rounding drift versus the Decimal value."""
    return float(value)


def payload_total(payload: dict) -> Decimal:
    """What Alegra will bill: sum of price x quantity of the lines, to the cent."""
    total = sum((Decimal(str(i["price"])) * Decimal(str(i["quantity"])) for i in payload["items"]), Decimal("0"))
    return total.quantize(Decimal("0.01"))


def sale_marker(sale_id) -> str:
    """Deterministic link POS Sale -> Alegra invoice, carried in `observations`
    (returned by GET /invoices). The only reconciliation key Alegra allows."""
    return f"POS sale_id={sale_id}"


def build_alegra_invoice_payload(sale: Sale, alegra_contact_id: str, number_template_id: str,
                                 generate_stamp: bool = False) -> dict:
    """Pure function, no I/O. Prices exclude tax per Alegra's documented
    convention (unit_price is already tax-exclusive in this POS's model).
    All money/quantity values are Decimal until _money_json at the very end --
    never float arithmetic."""
    date = invoice_date(sale)
    items = []
    for item in sale.items:
        product = item.product
        price: Decimal = item.unit_price.quantize(Decimal("0.01"))
        quantity: Decimal = item.quantity.quantize(Decimal("0.001"))
        if item.product_id is None:
            # Manual line -> generic item (GRANEL 372) at the line's REAL price (never
            # the item's list price), with the cashier's description kept on the line.
            items.append({"id": manual_item_id(sale, item), "price": _money_json(price),
                          "quantity": _money_json(quantity), "tax": [],
                          "description": item.product_name[:500]})
            continue
        items.append({
            "id": product.alegra_item_id,
            "price": _money_json(price),
            "quantity": _money_json(quantity),
            # Empty when the product has no alegra_tax_id: matches this
            # account's real invoices exactly (tax: [] on every line seen).
            "tax": [{"id": product.alegra_tax_id}] if product.alegra_tax_id else [],
        })
    terms, _ = payment_terms(sale)
    payload = {
        "date": date,
        "dueDate": date,
        "client": {"id": alegra_contact_id},
        "items": items,
        "numberTemplate": {"id": number_template_id},
        # Paid in full, no credit terms (this POS never sells on credit). When the
        # payment method can't be determined pre-flight blocks the send; the bare
        # CASH form here only serves previews.
        **(terms or {"paymentForm": "CASH"}),
        "observations": f"{sale_marker(sale.id)} number={sale.number}",
    }
    if generate_stamp:
        payload["stamp"] = {"generateStamp": True}
    return payload


# ---- pre-flight -----------------------------------------------------------------------------------------------

def preflight_sale(db: Session, sale: Sale, provider: AlegraProvider | None = None,
                   contact_override: tuple[str, Customer | None] | None = None,
                   allow_remote: bool = True) -> dict:
    """READY or BLOCKED, with every reason at once. No POST, no invoice row; at
    most it caches a looked-up Consumidor Final id. Never touches the sale."""
    requirements: list[dict] = []
    for name in config_status()["missing"]:
        requirements.append({"code": "CONFIG", "message": f"Falta configurar {name}"})
    if sale.status != SaleStatus.COMPLETED:
        requirements.append({"code": "SALE_NOT_COMPLETED", "message": "La venta no está COMPLETED"})
    if not sale.items:
        requirements.append({"code": "NO_ITEMS", "message": "La venta no tiene artículos"})
    lines = _line_blockers(sale)
    if getattr(sale, "external_items", None):
        # Comida a granel settled by the external scale is part of the customer's bill but not of
        # the POS sale: billing only the POS part, or adding a line nobody paid in the POS, would
        # both be invented fiscal behaviour. Blocked until that case is defined.
        requirements.append({"code": "EXTERNAL_SCALE", "message":
                             "La venta incluye comida a granel registrada en la báscula; la factura de esta "
                             "venta necesita revisión y no se envía automáticamente"})
    _, payment_blocker = payment_terms(sale)
    if payment_blocker:
        requirements.append(payment_blocker)
    contact_id, customer = None, None
    if contact_override:
        contact_id, customer = contact_override
    else:
        try:
            if allow_remote or invoice_recipient_id(sale):
                contact_id, customer = resolve_contact_for_sale(db, sale, provider)
            else:
                from app.services.customers import resolve_final_consumer_contact_id
                contact_id = resolve_final_consumer_contact_id(db, allow_remote=False)
                if not contact_id:
                    raise SaleValidationError(["Consumidor Final sin resolver; sincroniza clientes"])
        except SaleValidationError as exc:
            requirements += [{"code": "CONTACT", "message": reason} for reason in exc.reasons]
        except AlegraError as exc:
            # Unknown, not wrong: attempt_issue keeps it as a retryable error, never sends.
            requirements.append({"code": "CONTACT_UNREACHABLE", "message": f"No se pudo consultar Alegra: {exc}"})
    return {"sale_id": sale.id, "sale_number": sale.number,
            "status": "BLOCKED" if lines or requirements else "READY",
            "lines": lines, "requirements": requirements,
            "alegra_contact_id": contact_id, "customer": customer}


def _preflight_reasons(result: dict) -> list[str]:
    return [line["reason"] for line in result["lines"]] + [req["message"] for req in result["requirements"]]


def invoice_plan(db: Session, sale: Sale, provider: AlegraProvider | None = None, generate_stamp: bool = False,
                 contact_override: tuple[str, Customer | None] | None = None) -> dict:
    """What WOULD happen if this sale were invoiced now, catalog included: which products
    would be linked or created in Alegra (simulation: GET only, writes nothing) and the
    invoice payload. Lines whose item doesn't exist yet carry `id: None` + `pending_reference`
    (the real id only exists after the POST /items done by attempt_issue)."""
    from app.core.config import settings
    provider = provider or AlegraProvider()
    result = preflight_sale(db, sale, provider, contact_override=contact_override)
    catalog = ensure_sale_items(db, sale, provider, dry_run=True)
    plan = {r["product_id"]: r for r in catalog["products"]}
    # Unmapped lines the catalog step resolves are not blockers; everything else still is.
    unresolved = {r["product_id"] for r in catalog["unresolved"]}
    lines = [line for line in result["lines"]
             if not (line["code"] == "UNMAPPED_PRODUCT" and line["product_id"] in plan
                     and line["product_id"] not in unresolved)]
    reasons = [line["reason"] for line in lines] + [req["message"] for req in result["requirements"]] + \
        [f"{r.get('product_name')}: {r['reason']}" for r in catalog["unresolved"]]
    creating = [r for r in catalog["products"] if r["status"] == sync.READY_TO_CREATE]
    if creating and not settings.alegra_catalog_create_enabled:
        reasons.append(f"{len(creating)} producto(s) se crearían en Alegra, pero la creación automática está deshabilitada")
    try:
        reasons += missing_unit_reasons(sale, provider)        # already-linked / generic items only
    except AlegraError as exc:
        reasons.append(f"No se pudo verificar la unidad de medida en Alegra: {exc}")
    contact_id = result["alegra_contact_id"]
    payload = None
    if contact_id and not lines:
        payload = build_alegra_invoice_payload(sale, contact_id, settings.alegra_number_template_id, generate_stamp)
        for line, sale_item in zip(payload["items"], sale.items):
            step = plan.get(str(sale_item.product_id)) if sale_item.product_id else None
            if step and not line.get("id"):
                line["id"] = step.get("alegra_item_id")          # LINK target, or None until created
                if step["status"] == sync.READY_TO_CREATE:
                    line["pending_reference"] = step["reference"]
    return {"result": result, "catalog": catalog, "reasons": reasons, "ready": not reasons,
            "payload": payload, "alegra_contact_id": contact_id}


def preview_invoice(db: Session, sale: Sale, provider: AlegraProvider | None = None,
                    generate_stamp: bool = False) -> dict:
    """What WOULD be sent to Alegra for this sale (catalog plan + pre-flight + payload).
    Never POSTs and never writes."""
    from app.services.customers import customer_snapshot
    plan = invoice_plan(db, sale, provider, generate_stamp)
    contact_id = plan["alegra_contact_id"]
    return {"sale_id": sale.id, "sale_number": sale.number, "ready": plan["ready"],
            "reasons": plan["reasons"], "alegra_contact_id": contact_id,
            "customer": customer_snapshot(plan["result"]["customer"], contact_id) if contact_id else None,
            "generate_stamp": generate_stamp, "payload": plan["payload"],
            "catalog": [{k: r.get(k) for k in ("product_id", "product_name", "reference", "status", "alegra_item_id",
                                                "reason", "payload")} for r in plan["catalog"]["products"]]}


# ---- reconciliation (anti-duplicate) --------------------------------------------------------------------

def find_remote_invoices(sale: Sale, payload: dict, provider: AlegraProvider) -> list[dict]:
    """Every Alegra invoice carrying this sale's marker, searched with the exact
    `date` + `client_id` that were POSTed (GET /invoices, paginated). Raises
    AlegraError when Alegra can't be queried: 'unknown' is never 'not found'."""
    marker = re.compile(rf"(?:^|\s){re.escape(sale_marker(sale.id))}(?:\s|$)")
    found, start = [], 0
    for _ in range(MAX_RECONCILE_PAGES):
        page, total = provider.list_invoices_page(date=payload["date"], client_id=str(payload["client"]["id"]),
                                                  start=start, limit=30)
        found += [inv for inv in page if marker.search(str(inv.get("observations") or ""))]
        start += len(page)
        if not page or len(page) < 30 or (total is not None and start >= total):
            return found
    raise AlegraError("Demasiadas facturas para esa fecha y cliente; no se pudo completar la verificación")


def _apply_remote(record: ElectronicInvoice, response: dict) -> None:
    now = datetime.now(timezone.utc)
    record.status = ElectronicInvoiceStatus.ISSUED
    record.provider_invoice_id = str(response.get("id"))
    record.provider_number = _format_number(response)
    record.provider_status = response.get("status")
    record.legal_status = _extract_legal_status(response) or record.legal_status
    record.cufe = _extract_cufe(response) or record.cufe
    record.provider_response = response
    record.issued_at = record.issued_at or now
    record.last_synced_at = now
    record.needs_reconciliation = False
    record.error_code = None
    record.error_message = None


def _reconcile(db: Session, record: ElectronicInvoice, sale: Sale, provider: AlegraProvider) -> bool:
    """True when Alegra already has this sale's invoice (record now ISSUED, no
    POST). False only when the search SUCCEEDED and found nothing."""
    payload = record.provider_payload
    if not payload:
        return False  # never reached the POST step
    matches = find_remote_invoices(sale, payload, provider)
    if not matches:
        return False
    matches.sort(key=lambda inv: int(inv["id"]) if str(inv.get("id", "")).isdigit() else 0)
    _apply_remote(record, matches[0])
    if len(matches) > 1:
        record.error_code = "DUPLICATE_IN_ALEGRA"
        record.error_message = (f"Alegra tiene {len(matches)} facturas para esta venta "
                                f"(ids {', '.join(str(m['id']) for m in matches)}); revisar y anular las sobrantes")[:500]
    elif (payload.get("stamp") or {}).get("generateStamp") and _is_remote_draft(record):
        record.error_code = STAMP_FAILED_CODE
        record.error_message = "La factura quedó en borrador en Alegra (sin timbrar ante la DIAN)"
    db.commit()
    logger.warning("electronic_invoice_reconciled sale_id=%s provider_invoice_id=%s matches=%s",
                   sale.id, record.provider_invoice_id, len(matches))
    try:
        sync_invoice_status(db, record, provider)
    except AlegraError:
        logger.info("electronic_invoice_reconcile_sync_skipped invoice_id=%s", record.id)
    return True


# ---- issue / retry ------------------------------------------------------------------------------------------

def _claim(db: Session, record: ElectronicInvoice) -> bool:
    """Atomic single-UPDATE claim: only one caller can move a record into
    PROCESSING. Claimable: PENDING, BLOCKED, ERROR, or a PROCESSING whose last
    attempt is stale (a crashed worker)."""
    now = datetime.now(timezone.utc)
    claimable = or_(
        ElectronicInvoice.status.in_([ElectronicInvoiceStatus.PENDING, ElectronicInvoiceStatus.BLOCKED,
                                      ElectronicInvoiceStatus.ERROR]),
        and_(ElectronicInvoice.status == ElectronicInvoiceStatus.PROCESSING,
             or_(ElectronicInvoice.last_attempt_at.is_(None),
                 ElectronicInvoice.last_attempt_at < now - STALE_PROCESSING)))
    claimed = db.execute(update(ElectronicInvoice)
                         .where(ElectronicInvoice.id == record.id, ElectronicInvoice.provider_invoice_id.is_(None),
                                or_(ElectronicInvoice.error_code.is_(None),
                                    ElectronicInvoice.error_code.not_in(HOLD_CODES)),
                                claimable)
                         .values(status=ElectronicInvoiceStatus.PROCESSING, last_attempt_at=now,
                                 attempt_count=ElectronicInvoice.attempt_count + 1)
                         .execution_options(synchronize_session=False)).rowcount
    db.commit()
    if claimed:
        db.refresh(record)
    return bool(claimed)


def _finish(db: Session, record: ElectronicInvoice, status: ElectronicInvoiceStatus, code: str | None,
            message: str | None, needs_reconciliation: bool) -> ElectronicInvoice:
    record.status = status
    record.error_code = code[:60] if code else None
    record.error_message = message[:500] if message else None
    record.needs_reconciliation = needs_reconciliation
    db.commit(); db.refresh(record)
    return record


def attempt_issue(db: Session, sale: Sale, record: ElectronicInvoice, provider: AlegraProvider | None = None,
                  generate_stamp: bool = False,
                  contact_override: tuple[str, Customer | None] | None = None) -> ElectronicInvoice:
    """The only path that POSTs an invoice. Order is the whole point:
      1. atomic claim (concurrent retries: one wins, the rest get InvoiceInProgressError)
      2. if a previous POST's outcome is unknown -> reconcile with Alegra first;
         found = ISSUED without POST; search failure = stay ERROR, no POST
      3. pre-flight; BLOCKED = nothing sent
      4. needs_reconciliation=True committed BEFORE the POST (a crash mid-POST
         is then reconciled next time), cleared only when Alegra's answer is known.
    Raises SaleValidationError when BLOCKED (after recording it)."""
    status = config_status()
    if not status["configured"]:
        raise InvoicingConfigError(f"Alegra no está configurado: faltan {', '.join(status['missing'])}")
    if record.provider_invoice_id and _is_remote_draft(record):
        raise RemoteDraftError(f"Alegra ya tiene el borrador {record.provider_number or record.provider_invoice_id} de la "
                               f"venta {sale.number}; se completa timbrándolo, nunca con otra factura")
    if record.status == ElectronicInvoiceStatus.ISSUED or record.provider_invoice_id:
        raise AlreadyIssuedError(f"La venta {sale.number} ya tiene una factura electrónica emitida ({record.provider_invoice_id})")
    if record.error_code in HOLD_CODES:
        raise InvoiceHeldForReviewError("Esta factura está apartada para revisión manual y no se envía a Alegra")
    was_processing = record.status == ElectronicInvoiceStatus.PROCESSING
    if not _claim(db, record):
        db.refresh(record)
        if record.provider_invoice_id or record.status == ElectronicInvoiceStatus.ISSUED:
            raise AlreadyIssuedError(f"La venta {sale.number} ya tiene una factura electrónica emitida ({record.provider_invoice_id})")
        raise InvoiceInProgressError("Esta factura ya se está procesando; espera unos segundos y actualiza")

    from app.core.config import settings
    from app.services.customers import customer_snapshot
    provider = provider or AlegraProvider()
    try:
        if record.needs_reconciliation or was_processing:
            try:
                if _reconcile(db, record, sale, provider):
                    return record
            except AlegraError as exc:
                return _finish(db, record, ElectronicInvoiceStatus.ERROR, "RECONCILE_FAILED",
                               f"No se pudo verificar en Alegra si la factura ya existe; no se reenvió. {exc}", True)

        # Products without alegra_item_id are linked or created right here (Punto Red
        # experience: no prior catalog sync). Only when every line has its item id does the
        # invoice go on. A problem here never touches the sale: the invoice waits, retryable.
        catalog = ensure_sale_items(db, sale, provider)
        if catalog["status"] != "READY":
            detail = "; ".join(f"{r.get('product_name') or r['product_id']}: {r['reason']}" for r in catalog["unresolved"])
            if catalog["status"] == "PENDING":
                return _finish(db, record, ElectronicInvoiceStatus.ERROR, "CATALOG_PENDING",
                               f"No se pudo preparar el producto en Alegra; la factura no se envió. {detail}", False)
            _finish(db, record, ElectronicInvoiceStatus.BLOCKED, "CATALOG", detail, False)
            raise SaleValidationError([r["reason"] for r in catalog["unresolved"]])

        result = preflight_sale(db, sale, provider, contact_override=contact_override)
        if result["status"] == "BLOCKED" and not result["lines"] and                 all(req["code"] == "CONTACT_UNREACHABLE" for req in result["requirements"]):
            return _finish(db, record, ElectronicInvoiceStatus.ERROR, "CONTACT_PENDING",
                           "; ".join(_preflight_reasons(result)) + ". La factura no se envió; se puede reintentar.", False)
        if result["status"] == "BLOCKED":
            reasons = _preflight_reasons(result)
            _finish(db, record, ElectronicInvoiceStatus.BLOCKED, "PREFLIGHT", "; ".join(reasons), False)
            raise SaleValidationError(reasons)

        created = {r["alegra_item_id"] for r in catalog["products"] if r["status"] == sync.CREATED}
        try:
            missing_unit = missing_unit_reasons(sale, provider, skip=created)
        except AlegraError as exc:
            return _finish(db, record, ElectronicInvoiceStatus.ERROR, "UNIT_CHECK_PENDING",
                           f"No se pudo verificar la unidad de medida en Alegra; la factura no se envió. {exc}", False)
        if missing_unit:
            _finish(db, record, ElectronicInvoiceStatus.BLOCKED, "ITEM_UNIT", "; ".join(missing_unit), False)
            raise SaleValidationError(missing_unit)

        contact_id, customer = result["alegra_contact_id"], result["customer"]
        payload = build_alegra_invoice_payload(sale, contact_id, settings.alegra_number_template_id, generate_stamp)
        billed = payload_total(payload)
        if billed != Decimal(sale.total).quantize(Decimal("0.01")):
            reason = (f"El total a facturar ({billed}) no coincide con el total de la venta ({sale.total}); "
                      "la factura no se envió")
            _finish(db, record, ElectronicInvoiceStatus.BLOCKED, "TOTAL_MISMATCH", reason, False)
            raise SaleValidationError([reason])
        record.alegra_contact_id = contact_id
        record.customer_id = customer.id if customer else None
        record.customer_snapshot = customer_snapshot(customer, contact_id)
        record.provider_payload = payload
        record.error_code = None
        record.error_message = None
        record.needs_reconciliation = True
        db.commit()

        try:
            response = provider.create_invoice(payload)
        except AlegraValidationError as exc:
            # A 400 does NOT prove nothing was created: Alegra can create the invoice
            # (as a draft) and then fail the DIAN step with a 400. Reconcile first.
            return _after_rejection(db, record, sale, payload, provider, exc)
        except DEFINITIVE_ERRORS as exc:
            logger.warning("electronic_invoice_rejected sale_id=%s error=%s", sale.id, type(exc).__name__)
            return _finish(db, record, ElectronicInvoiceStatus.ERROR, type(exc).__name__, str(exc), False)
        except AlegraError as exc:
            # Timeout / 5xx / unreadable answer: Alegra may or may not have created it.
            logger.warning("electronic_invoice_outcome_unknown sale_id=%s error=%s", sale.id, type(exc).__name__)
            return _finish(db, record, ElectronicInvoiceStatus.ERROR, type(exc).__name__,
                           f"{exc}. Se verificará en Alegra antes de reintentar", True)

        if not isinstance(response, dict) or response.get("id") in (None, ""):
            return _finish(db, record, ElectronicInvoiceStatus.ERROR, "NO_INVOICE_ID",
                           "Alegra respondió sin id de factura; se verificará antes de reintentar", True)
        _apply_remote(record, response)
        db.commit(); db.refresh(record)
        logger.info("electronic_invoice_issued sale_id=%s invoice_id=%s provider_invoice_id=%s",
                    sale.id, record.id, record.provider_invoice_id)
        return _sync_after_issue(db, record, provider)
    except (SaleValidationError, AlreadyIssuedError, InvoiceInProgressError, InvoiceHeldForReviewError,
            RemoteDraftError):
        raise
    except Exception:
        # Unexpected failure after the claim: never leave it PROCESSING forever,
        # and assume the worst about a possible POST (reconcile next time).
        db.rollback()
        _finish(db, record, ElectronicInvoiceStatus.ERROR, "UNEXPECTED",
                "Error inesperado durante la emisión; se verificará en Alegra antes de reintentar", True)
        raise


def issue_invoice(db: Session, sale: Sale, alegra_contact_id: str | None = None,
                  provider: AlegraProvider | None = None, generate_stamp: bool = False,
                  customer: Customer | None = None) -> ElectronicInvoice:
    """`alegra_contact_id` None = resolve from the sale (its customer, or
    Consumidor Final). The fiscal identity is frozen in customer_snapshot."""
    record = create_electronic_invoice_record(db, sale)
    override = (alegra_contact_id, customer) if alegra_contact_id else None
    return attempt_issue(db, sale, record, provider, generate_stamp, contact_override=override)


def _sync_after_issue(db: Session, record: ElectronicInvoice, provider: AlegraProvider) -> ElectronicInvoice:
    """GET right after Alegra accepted the invoice: number, DIAN status, CUFE, PDF, XML.
    Best effort: the invoice IS issued; a failure here only leaves it to sync later
    (POST …/sync) and never causes another issue."""
    try:
        return sync_invoice_status(db, record, provider)
    except Exception:  # noqa: BLE001 - the issue already succeeded
        db.rollback()
        logger.warning("electronic_invoice_post_issue_sync_failed invoice_id=%s", record.id, exc_info=True)
        db.refresh(record)
        return record


STAMP_LEASE = timedelta(minutes=2)   # > the worst case of one stamp attempt (GETs + POST, 15 s timeout each)


def _claim_stamp(db: Session, record: ElectronicInvoice) -> bool:
    """Atomic single-UPDATE claim for stamping a remote draft: two retries at once -> only
    one reaches POST /invoices/stamp. Only THIS record's draft (same provider id, no CUFE),
    ISSUED -> PROCESSING; a PROCESSING left by a crashed stamp is reclaimable after the lease."""
    now = datetime.now(timezone.utc)
    claimable = or_(ElectronicInvoice.status == ElectronicInvoiceStatus.ISSUED,
                    and_(ElectronicInvoice.status == ElectronicInvoiceStatus.PROCESSING,
                         or_(ElectronicInvoice.last_attempt_at.is_(None),
                             ElectronicInvoice.last_attempt_at < now - STAMP_LEASE)))
    claimed = db.execute(update(ElectronicInvoice)
                         .where(ElectronicInvoice.id == record.id,
                                ElectronicInvoice.provider_invoice_id == record.provider_invoice_id,
                                ElectronicInvoice.cufe.is_(None), claimable)
                         .values(status=ElectronicInvoiceStatus.PROCESSING, last_attempt_at=now,
                                 attempt_count=ElectronicInvoice.attempt_count + 1)
                         .execution_options(synchronize_session=False)).rowcount
    db.commit()
    if claimed:
        db.refresh(record)
    return bool(claimed)


def _retry_remote_draft(db: Session, record: ElectronicInvoice, sale: Sale,
                        provider: AlegraProvider | None) -> ElectronicInvoice:
    """Retry of a sale whose invoice Alegra left as a DRAFT: stamp THAT draft
    (stamp_existing_invoice: marker of this sale, still a draft without CUFE, units). Never
    POSTs another invoice. Already stamped remotely -> synced and treated as issued."""
    if record.error_code in HOLD_CODES:
        raise InvoiceHeldForReviewError("Esta factura está apartada para revisión manual y no se envía a Alegra")
    if record.error_code == "DUPLICATE_IN_ALEGRA":
        raise SaleValidationError(["Alegra tiene más de una factura para esta venta; requiere revisión manual, "
                                   "no se timbra ninguna"])
    if not _claim_stamp(db, record):
        raise InvoiceInProgressError("Esta factura ya se está procesando; espera unos segundos y actualiza")
    try:
        plan = stamp_existing_invoice(db, record, sale, provider or AlegraProvider(), confirm=True)
    finally:
        # The document exists in Alegra whatever happened: back to ISSUED (draft or stamped).
        db.rollback()
        db.execute(update(ElectronicInvoice).where(ElectronicInvoice.id == record.id,
                                                   ElectronicInvoice.status == ElectronicInvoiceStatus.PROCESSING)
                   .values(status=ElectronicInvoiceStatus.ISSUED).execution_options(synchronize_session=False))
        db.commit(); db.refresh(record)
    if plan["problems"]:
        record.error_message = "; ".join(plan["problems"])[:500]
        db.commit(); db.refresh(record)
        raise SaleValidationError(plan["problems"])
    return record


def retry_invoice(db: Session, record: ElectronicInvoice, sale: Sale, alegra_contact_id: str | None = None,
                  provider: AlegraProvider | None = None, generate_stamp: bool = False) -> ElectronicInvoice:
    """Same guarded path as the first attempt: reconcile before any re-POST. A sale whose
    invoice is a remote DRAFT is finished by stamping that draft, never with another invoice."""
    if record.provider_invoice_id and _is_remote_draft(record):
        return _retry_remote_draft(db, record, sale, provider)
    if record.provider_invoice_id or record.status == ElectronicInvoiceStatus.ISSUED:
        raise AlreadyIssuedError("Esta factura ya fue enviada a Alegra; usa sync en vez de retry")
    # Keep the contact of the previous attempt (the one a pending reconciliation
    # searches under); only resolve from the sale when none was recorded yet.
    contact_id = alegra_contact_id or record.alegra_contact_id
    recipient_id = invoice_recipient_id(sale)
    recipient = db.get(Customer, recipient_id) if recipient_id else None
    customer = recipient if recipient and recipient.alegra_contact_id == contact_id else None
    override = (contact_id, customer) if contact_id else None
    return attempt_issue(db, sale, record, provider, generate_stamp, contact_override=override)


def sync_invoice_status(db: Session, record: ElectronicInvoice,
                        provider: AlegraProvider | None = None) -> ElectronicInvoice:
    if not record.provider_invoice_id:
        raise SaleValidationError(["Esta factura no tiene provider_invoice_id todavía; nada que sincronizar"])
    provider = provider or AlegraProvider()
    response = provider.get_invoice(record.provider_invoice_id, fields="pdf,xml,events")
    record.provider_status = response.get("status")
    record.legal_status = _extract_legal_status(response)
    record.cufe = _extract_cufe(response) or record.cufe
    record.pdf_url = response.get("pdf")
    record.xml_url = response.get("xml")
    rejection = (record.provider_response or {}).get(REJECTION_KEY) if isinstance(record.provider_response, dict) else None
    record.provider_response = {**response, REJECTION_KEY: rejection} if rejection else response
    record.last_synced_at = datetime.now(timezone.utc)
    db.commit(); db.refresh(record)
    logger.info("electronic_invoice_synced invoice_id=%s provider_status=%s legal_status=%s",
               record.id, record.provider_status, record.legal_status)
    return record


def _is_remote_draft(record: ElectronicInvoice) -> bool:
    return (record.provider_status or "").lower() == "draft" and not record.cufe


def _invoice_id_hint(body) -> str | None:
    """An invoice id inside a 400 body, if Alegra included one (shape not documented)."""
    if not isinstance(body, dict):
        return None
    for node in (body, body.get("invoice"), body.get("data"), body.get("error")):
        if isinstance(node, dict) and str(node.get("id") or "").isdigit():
            return str(node["id"])
    return None


def _after_rejection(db: Session, record: ElectronicInvoice, sale: Sale, payload: dict,
                     provider: AlegraProvider, exc: AlegraValidationError) -> ElectronicInvoice:
    """POST /invoices answered 400. Keep the whole body; then search Alegra by this
    sale's marker (plus any id the body names). Found -> link it (a draft becomes
    STAMP_FAILED: finished later by stamping it). Search failed -> ERROR that must be
    reconciled before anything else. Searched and absent -> real rejection."""
    body = getattr(exc, "details", None)
    rejection = {"http_status": 400, "message": str(exc), "body": body,
                 "at": datetime.now(timezone.utc).isoformat()}
    record.provider_response = {REJECTION_KEY: rejection}
    db.commit()
    logger.warning("electronic_invoice_rejected_400 sale_id=%s message=%s", sale.id, str(exc)[:200])
    try:
        matches = find_remote_invoices(sale, payload, provider)
        hinted = _invoice_id_hint(body)
        if not matches and hinted:
            remote = provider.get_invoice(hinted)
            if sale_marker(sale.id) in str((remote or {}).get("observations") or ""):
                matches = [remote]
    except AlegraError as search_exc:
        return _finish(db, record, ElectronicInvoiceStatus.ERROR, "REJECTED_UNVERIFIED",
                       f"Alegra respondió 400 ({exc}) y no se pudo verificar si creó la factura; no se reenvía. {search_exc}",
                       True)
    if not matches:
        return _finish(db, record, ElectronicInvoiceStatus.ERROR, "AlegraValidationError", str(exc), False)
    matches.sort(key=lambda inv: int(inv["id"]) if str(inv.get("id", "")).isdigit() else 0)
    _apply_remote(record, matches[0])
    record.provider_response = {**(record.provider_response or {}), REJECTION_KEY: rejection}
    if len(matches) > 1:
        record.error_code = "DUPLICATE_IN_ALEGRA"
        record.error_message = (f"Alegra tiene {len(matches)} facturas para esta venta "
                                f"(ids {', '.join(str(m['id']) for m in matches)}); revisar y anular las sobrantes")[:500]
    elif _is_remote_draft(record):
        record.error_code = STAMP_FAILED_CODE
        record.error_message = f"Alegra creó el borrador {record.provider_number} pero no lo timbró: {exc}"[:500]
    db.commit(); db.refresh(record)
    logger.warning("electronic_invoice_400_reconciled sale_id=%s provider_invoice_id=%s status=%s",
                   sale.id, record.provider_invoice_id, record.provider_status)
    return record


def stamp_existing_invoice(db: Session, record: ElectronicInvoice, sale: Sale,
                           provider: AlegraProvider | None = None, confirm: bool = False) -> dict:
    """Recovery of a remote DRAFT: stamp THAT invoice (POST /invoices/stamp), never a new
    one. Without confirm it only checks (GET): the invoice is this sale's (marker), still a
    draft without CUFE, and every line's item has a unit of measure (a line without unit
    is the leading suspect of the failed stamp). Returns the plan / result."""
    provider = provider or AlegraProvider()
    plan: dict = {"sale_number": sale.number, "invoice_id": record.provider_invoice_id,
                  "number": record.provider_number, "problems": [], "stamped": False}
    if not record.provider_invoice_id:
        plan["problems"].append("La venta no tiene factura en Alegra; no hay borrador que timbrar")
        return plan
    remote = provider.get_invoice(record.provider_invoice_id)
    plan["remote_status"] = remote.get("status")
    if sale_marker(sale.id) not in str(remote.get("observations") or ""):
        plan["problems"].append("La factura remota no lleva la marca de esta venta")
    elif _extract_cufe(remote):
        # Already stamped (Alegra moves it draft -> open and adds stamp.cufe): a re-run is
        # not an error and never POSTs again. Only syncs what GET reports.
        sync_invoice_status(db, record, provider)
        record.error_code, record.error_message = None, None
        db.commit()
        plan.update(already_stamped=True, stamped=True, legal_status=record.legal_status, cufe=record.cufe)
        return plan
    if (remote.get("status") or "").lower() != "draft" or _extract_cufe(remote):
        plan["problems"].append(f"La factura remota no es un borrador sin timbrar (estado {remote.get('status')})")
    lines, plan["warnings"] = [], []
    for line in remote.get("items") or []:
        unit = item_unit(provider, line["id"]) if line.get("id") else None
        lines.append({"item_id": line.get("id"), "name": line.get("name"), "line_unit": line.get("unit"),
                      "item_unit": unit})
        if not unit:
            plan["problems"].append(f"El ítem {line.get('id')} ({line.get('name')}) no tiene unidad de medida")
        elif not line.get("unit"):
            # The draft stores each line's unit when it is created; fixing the item may not
            # reach this line. Not blocking (unknown), but must be checked before stamping.
            plan["warnings"].append(f"La línea del ítem {line.get('id')} en el borrador conserva unit=null aunque el "
                                    "ítem ya tiene unidad; verificar en Alegra antes de timbrar")
    plan["lines"] = lines
    plan["request"] = {"method": "POST", "path": "/invoices/stamp", "json": {"ids": [record.provider_invoice_id]}}
    if not confirm or plan["problems"]:
        return plan
    try:
        plan["response"] = provider.stamp_invoices([record.provider_invoice_id])
    except AlegraError as exc:
        record.error_message = f"El timbrado del borrador falló: {exc}"[:500]
        db.commit()
        plan["problems"].append(f"Alegra respondió al timbrado: {exc}")
        plan["error_details"] = getattr(exc, "details", None)
        return plan
    sync_invoice_status(db, record, provider)
    if record.cufe:
        record.error_code, record.error_message = None, None
        db.commit()
    plan.update(stamped=bool(record.cufe), legal_status=record.legal_status, cufe=record.cufe)
    return plan


def invoice_recipient_id(sale: Sale):
    """The Customer the invoice goes to (None = Consumidor final). Sales with the checkbox
    (migration 0024) carry it in invoice_customer_id, independent of customer_id; historical
    sales/invoices keep the rule they were created with (customer_id)."""
    return sale.invoice_customer_id if sale.requires_electronic_invoice else sale.customer_id


def resolve_contact_for_sale(db: Session, sale: Sale,
                             provider: AlegraProvider | None = None) -> tuple[str, Customer | None]:
    """Sale.customer_id set -> that customer's Alegra contact (unchanged). Otherwise the
    Consumidor Final contact, never typed by the cashier and VERIFIED in Alegra to be
    identification 222222222222 (customers.verified_final_consumer_contact_id). Not provable ->
    SaleValidationError (blocked); Alegra unreachable -> FinalConsumerUnverified (an AlegraError)."""
    from app.services.customers import CustomerError, verified_final_consumer_contact_id
    recipient_id = invoice_recipient_id(sale)
    if recipient_id:
        customer = db.get(Customer, recipient_id)
        if customer is None or not customer.alegra_contact_id:
            raise SaleValidationError(["El cliente de la venta no está vinculado a Alegra"])
        return customer.alegra_contact_id, customer
    try:
        return verified_final_consumer_contact_id(db, provider), None
    except CustomerError as exc:
        raise SaleValidationError([str(exc)]) from exc


def auto_issue_for_sale(sale_id: UUID, session_factory=None, provider: AlegraProvider | None = None) -> None:
    """Background step after checkout: Sale COMPLETED -> ElectronicInvoice ->
    Alegra. Runs in its own DB session AFTER the sale committed, so nothing here
    can revert the sale, its payments, inventory or cash. Never raises: every
    outcome ends as ISSUED, BLOCKED or ERROR (retryable) plus a log line."""
    from app.core.config import settings
    from app.core.db import SessionLocal
    factory = session_factory or SessionLocal
    with factory() as db:
        try:
            sale = db.get(Sale, sale_id)
            if sale is None or sale.status != SaleStatus.COMPLETED:
                return
            if not sale.requires_electronic_invoice and get_electronic_invoice_for_sale(db, sale.id) is None:
                return  # nobody asked for an invoice: never create one here
            record = create_electronic_invoice_record(db, sale)
            if record.status != ElectronicInvoiceStatus.PENDING:
                return  # someone else already handled it
            attempt_issue(db, sale, record, provider, generate_stamp=settings.alegra_auto_issue_stamp)
        except (SaleValidationError, InvoiceInProgressError, AlreadyIssuedError, InvoiceHeldForReviewError,
                RemoteDraftError):
            pass  # BLOCKED already recorded / another worker owns it / done / parked / draft to stamp
        except InvoicingConfigError as exc:
            _mark_error_by_sale(db, sale_id, "CONFIG", str(exc))
        except Exception:  # noqa: BLE001 - a background job must never propagate
            logger.exception("electronic_invoice_auto_issue_failed sale_id=%s", sale_id)


def _mark_error_by_sale(db: Session, sale_id: UUID, code: str, message: str) -> None:
    try:
        record = get_electronic_invoice_for_sale(db, sale_id)
        if record and not record.provider_invoice_id and record.status != ElectronicInvoiceStatus.ISSUED:
            _finish(db, record, ElectronicInvoiceStatus.ERROR, code, message, record.needs_reconciliation)
    except Exception:  # noqa: BLE001
        logger.exception("electronic_invoice_mark_error_failed sale_id=%s", sale_id)
        db.rollback()


def _format_number(response: dict) -> str | None:
    template = response.get("numberTemplate") or {}
    if template.get("fullNumber"):
        return str(template["fullNumber"])
    prefix = template.get("prefix") or ""
    number = template.get("number") or response.get("number")
    return f"{prefix}{number}" if number else None


def _extract_cufe(response: dict) -> str | None:
    stamp = response.get("stamp") or {}
    return str(stamp["cufe"]) if isinstance(stamp, dict) and stamp.get("cufe") else None


def hold_for_review(db: Session, record: ElectronicInvoice, note: str) -> ElectronicInvoice:
    """Park a record outside the pipeline (BLOCKED + REVIEW_HOLD_CODE). Only allowed
    when it provably never reached Alegra: no provider id, no payload, 0 attempts."""
    if record.provider_invoice_id or record.provider_payload or record.attempt_count:
        raise SaleValidationError(["Esta factura ya tuvo intentos de envío; debe reconciliarse, no apartarse"])
    return _finish(db, record, ElectronicInvoiceStatus.BLOCKED, REVIEW_HOLD_CODE, note, False)


def mark_remote_draft_deleted(db: Session, record: ElectronicInvoice, evidence: dict) -> ElectronicInvoice:
    """The POS created a draft in Alegra by accident and it was deleted there.
    The local row must not look issued nor ever be retried/auto-issued: park it
    (BLOCKED + DELETED_DRAFT_CODE), clear the remote ids (they no longer exist)
    and keep what was deleted in provider_response for the audit trail."""
    if record.cufe or (record.legal_status or ""):
        raise SaleValidationError(["La factura tiene datos DIAN; no es un borrador"])
    record.provider_response = {"deleted_remote_draft": evidence, "previous_response": record.provider_response}
    record.provider_invoice_id = None
    record.provider_number = None
    record.provider_status = "deleted"
    record.issued_at = None
    return _finish(db, record, ElectronicInvoiceStatus.BLOCKED, DELETED_DRAFT_CODE,
                   f"Borrador creado por error en Alegra ({evidence.get('number')}, id {evidence.get('id')}) y eliminado "
                   f"el {evidence.get('deleted_at')}. Nunca fue a la DIAN. Queda fuera de la emisión del POS; "
                   "se factura por el procedimiento manual, una sola vez.", False)


def display_status(record: ElectronicInvoice) -> str:
    """What the invoice really is, for people. ISSUED only means Alegra has the
    document: a draft (no stamp/CUFE) is NOT an issued electronic invoice."""
    if record.error_code in HOLD_CODES:
        return "HELD"
    if record.status != ElectronicInvoiceStatus.ISSUED:
        return record.status.value
    legal = (record.legal_status or "").upper()
    if "REJECT" in legal:
        return "DIAN_REJECTED"
    if "ACCEPT" in legal:
        return "ACCEPTED"
    if (record.provider_status or "").lower() == "draft" and not record.cufe:
        return "DRAFT"
    return "SENT"


def _extract_legal_status(response: dict) -> str | None:
    """DIAN legal status is not a flat field — Alegra exposes it through the
    `events` list (fields=events). We surface it as-is, never guessing an
    equivalence the API response doesn't state."""
    stamp = response.get("stamp") or {}
    if isinstance(stamp, dict) and stamp.get("legalStatus"):
        return stamp.get("legalStatus")
    events = response.get("events") or []
    dian_events = [e for e in events if isinstance(e, dict) and "DIAN" in str(e.get("type", "")).upper()]
    if dian_events:
        return dian_events[-1].get("status")
    return None
