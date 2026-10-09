"""Customers: minimal fiscal contacts for electronic invoicing via Alegra.

The POS never talks to DIAN — only POS -> Alegra -> DIAN. A Customer always
mirrors an Alegra contact: creation goes to Alegra FIRST and only a contact
Alegra accepted (or already had) is stored locally, with its alegra_contact_id.

Identity is (identification_type, identification_number), normalized; never
the name. alegra_contact_id is unique too. Searching during a sale is local
only — Alegra is contacted to create/link a customer or to sync, never per key.
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.models import Customer
from app.services.alegra_client import AlegraError, AlegraNetworkError, AlegraRateLimitError, AlegraServerError
from app.services.alegra_provider import AlegraProvider

logger = logging.getLogger(__name__)

# DIAN's generic identification for "consumidor final" (verified on the real
# Alegra account: contact id 1, CC 222222222222).
FINAL_CONSUMER_IDENTIFICATION = "222222222222"

IDENTIFICATION_TYPES = ("CC", "NIT", "CE", "PP", "TI", "RC", "TE", "DIE", "NUIP", "FOREIGN_NIT")
KINDS_OF_PERSON = ("PERSON_ENTITY", "LEGAL_ENTITY")
NIT_WEIGHTS = (3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71)
# Fields a sync overwrites from Alegra. `active` is handled apart: a contact
# inactive in Alegra becomes inactive here, but a customer hidden locally is
# not re-shown by a sync.
SYNCED_FIELDS = ("identification_type", "identification_number", "dv", "kind_of_person", "name",
                 "first_name", "last_name", "email", "phone", "address")


class CustomerError(Exception):
    """Business-rule failure; `status` is the HTTP code the API should use."""
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


# ---- normalization ------------------------------------------------------------------------------------

def normalize_type(value: str | None) -> str:
    kind = (value or "").strip().upper()
    if kind not in IDENTIFICATION_TYPES:
        raise CustomerError(f"Tipo de documento no válido: {value!r}")
    return kind


def normalize_number(value: str | None) -> str:
    """'900.123.456-7' -> '9001234567' is wrong for a NIT (the -7 is the DV), so
    callers split the DV first with split_nit(). This only strips separators."""
    number = re.sub(r"[^0-9A-Za-z]", "", value or "").upper()
    if not number:
        raise CustomerError("El número de documento es obligatorio")
    return number


def split_nit(raw: str) -> tuple[str, str | None]:
    """'900123456-7' -> ('900123456', '7'). Without an explicit '-' the whole
    value is the NIT (the DV is then computed, never guessed from the digits)."""
    text = (raw or "").strip()
    match = re.fullmatch(r"([\d.\s]+)-\s*(\d)", text)
    if match:
        return normalize_number(match.group(1)), match.group(2)
    return normalize_number(text), None


def nit_check_digit(number: str) -> str:
    """DIAN's published modulo-11 check digit for a NIT."""
    if not number.isdigit() or len(number) > len(NIT_WEIGHTS):
        raise CustomerError("El NIT debe tener solo dígitos (máximo 15)")
    digits = [int(d) for d in reversed(number)]
    remainder = sum(d * w for d, w in zip(digits, NIT_WEIGHTS)) % 11
    return str(remainder if remainder in (0, 1) else 11 - remainder)


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


# ---- Alegra <-> local mapping ------------------------------------------------------------------------------

def fields_from_alegra(contact: dict) -> dict:
    """Alegra contact -> Customer fields. Raises CustomerError when the contact
    has no identification (it can't be keyed, so it is reported, not stored)."""
    ident = contact.get("identificationObject") or {}
    raw_number = ident.get("number") or contact.get("identification")
    if not raw_number:
        raise CustomerError(f"El contacto {contact.get('id')} de Alegra no tiene identificación")
    kind = (ident.get("type") or "CC").strip().upper()
    name_obj = contact.get("nameObject") or {}
    first = " ".join(p for p in (name_obj.get("firstName"), name_obj.get("secondName")) if p) or None
    last = " ".join(p for p in (name_obj.get("lastName"), name_obj.get("secondLastName")) if p) or None
    name = _clean(contact.get("name")) or " ".join(p for p in (first, last) if p) or str(raw_number)
    address = contact.get("address")
    return {
        "identification_type": kind if kind in IDENTIFICATION_TYPES else kind[:20],
        "identification_number": normalize_number(str(raw_number)),
        "dv": _clean(str(ident["dv"])) if ident.get("dv") not in (None, "") else None,
        "kind_of_person": (contact.get("kindOfPerson") or "PERSON_ENTITY")[:20],
        "name": name[:200],
        "first_name": first[:100] if first else None,
        "last_name": last[:100] if last else None,
        "email": _clean(contact.get("email")),
        "phone": _clean(contact.get("phonePrimary")) or _clean(contact.get("mobile")),
        "address": _clean(address.get("address")) if isinstance(address, dict) else _clean(address),
        "active": (contact.get("status") or "active") == "active",
    }


def alegra_contact_payload(data: dict) -> dict:
    """Only what Alegra requires (identificationObject, kindOfPerson, name or
    nameObject) plus what the cashier actually typed. No regime/fiscal
    responsibilities are invented — Alegra applies its own defaults."""
    ident: dict = {"type": data["identification_type"], "number": data["identification_number"]}
    if data.get("dv"):
        ident["dv"] = data["dv"]
    payload: dict = {"identificationObject": ident, "kindOfPerson": data["kind_of_person"], "type": ["client"]}
    if data["kind_of_person"] == "PERSON_ENTITY":
        payload["nameObject"] = {"firstName": data["first_name"], "lastName": data["last_name"]}
    else:
        payload["name"] = data["name"]
    if data.get("email"):
        payload["email"] = data["email"]
    if data.get("phone"):
        payload["phonePrimary"] = data["phone"]
    if data.get("address"):
        payload["address"] = {"address": data["address"]}
    return payload


def prepare_new_customer(identification_type: str, identification_number: str, kind_of_person: str | None,
                         name: str | None, first_name: str | None, last_name: str | None,
                         email: str | None, phone: str | None, address: str | None) -> dict:
    kind = normalize_type(identification_type)
    dv = None
    if kind == "NIT":
        number, dv = split_nit(identification_number)
        computed = nit_check_digit(number)
        if dv is not None and dv != computed:
            raise CustomerError(f"El dígito de verificación no corresponde a ese NIT (debería ser {computed})")
        dv = computed
    else:
        number = normalize_number(identification_number)
    person = (kind_of_person or ("LEGAL_ENTITY" if kind == "NIT" else "PERSON_ENTITY")).upper()
    if person not in KINDS_OF_PERSON:
        raise CustomerError("Tipo de persona no válido")
    first, last = _clean(first_name), _clean(last_name)
    if person == "PERSON_ENTITY":
        if not first or not last:
            raise CustomerError("Para una persona natural escribe nombres y apellidos")
        full = f"{first} {last}"
    else:
        full = _clean(name)
        if not full:
            raise CustomerError("Escribe la razón social")
        first = last = None
    return {"identification_type": kind, "identification_number": number, "dv": dv, "kind_of_person": person,
            "name": full[:200], "first_name": first, "last_name": last, "email": _clean(email),
            "phone": _clean(phone), "address": _clean(address)}


# ---- queries ------------------------------------------------------------------------------------------------

def find_by_identification(db: Session, identification_type: str, number: str) -> Customer | None:
    return db.scalar(select(Customer).where(Customer.identification_type == identification_type,
                                            Customer.identification_number == number))


def search_customers(db: Session, q: str = "", active: bool | None = True, limit: int = 20) -> list[Customer]:
    """Local search by document, name, phone or email. Exact/prefix document
    matches rank first, then name matches. The final-consumer contact never shows."""
    stmt = select(Customer).where(Customer.is_final_consumer.is_(False))
    if active is not None:
        stmt = stmt.where(Customer.active.is_(active))
    term = q.strip()
    if term:
        digits = re.sub(r"[^0-9A-Za-z]", "", term).upper()
        like = f"%{term.lower()}%"
        conditions = [func.lower(Customer.name).like(like), func.lower(Customer.email).like(like)]
        if digits:
            conditions += [Customer.identification_number.like(f"%{digits}%"), Customer.phone.like(f"%{digits}%")]
        stmt = stmt.where(or_(*conditions))
        rank = case(
            (Customer.identification_number == digits, 0),
            (Customer.identification_number.like(f"{digits}%"), 1),
            (func.lower(Customer.name).like(f"{term.lower()}%"), 2),
            else_=3) if digits else case((func.lower(Customer.name).like(f"{term.lower()}%"), 2), else_=3)
        stmt = stmt.order_by(rank, Customer.name)
    else:
        stmt = stmt.order_by(Customer.name)
    return list(db.scalars(stmt.limit(limit)).all())


# ---- create (Alegra first) -------------------------------------------------------------------------------

@dataclass
class CreateResult:
    customer: Customer
    outcome: str  # EXISTING_LOCAL | LINKED_REMOTE | CREATED


def _matching_remote(contacts: list[dict], number: str) -> dict | None:
    for contact in contacts:
        try:
            if fields_from_alegra(contact)["identification_number"] == number:
                return contact
        except CustomerError:
            continue
    return None


def _upsert_from_remote(db: Session, contact: dict) -> Customer:
    fields = fields_from_alegra(contact)
    remote_id = str(contact["id"])
    customer = db.scalar(select(Customer).where(Customer.alegra_contact_id == remote_id)) or \
        find_by_identification(db, fields["identification_type"], fields["identification_number"])
    if customer is None:
        customer = Customer(alegra_contact_id=remote_id)
        db.add(customer)
    customer.alegra_contact_id = remote_id
    for key, value in fields.items():
        setattr(customer, key, value)
    customer.is_final_consumer = fields["identification_number"] == FINAL_CONSUMER_IDENTIFICATION
    customer.last_synced_at = datetime.now(timezone.utc)
    return customer


def create_customer(db: Session, data: dict, provider: AlegraProvider | None = None) -> CreateResult:
    """1 normalize (done by prepare_new_customer) · 2 local lookup · 3 remote
    lookup by identification · 4 link if it exists · 5 else POST to Alegra ·
    6 store with alegra_contact_id. Any Alegra failure leaves NO local row."""
    number = data["identification_number"]
    if number == FINAL_CONSUMER_IDENTIFICATION:
        raise CustomerError("Ese es el documento genérico de Consumidor Final: desmarca «Factura a nombre del cliente»")
    existing = find_by_identification(db, data["identification_type"], number)
    if existing:
        if not existing.active:
            existing.active = True
            db.commit(); db.refresh(existing)
        return CreateResult(existing, "EXISTING_LOCAL")

    provider = provider or AlegraProvider()
    try:
        remote = _matching_remote(provider.find_contacts_by_identification(number), number)
        if remote is not None:
            customer = _upsert_from_remote(db, remote)
            db.commit(); db.refresh(customer)
            logger.info("customer_linked_remote alegra_contact_id=%s", customer.alegra_contact_id)
            return CreateResult(customer, "LINKED_REMOTE")
        response = provider.create_contact(alegra_contact_payload(data))
    except AlegraError:
        db.rollback()
        raise
    if not isinstance(response, dict) or response.get("id") in (None, ""):
        raise CustomerError("Alegra no devolvió el identificador del contacto creado; no se guardó el cliente", 502)
    customer = Customer(alegra_contact_id=str(response["id"]), **data, active=True,
                        last_synced_at=datetime.now(timezone.utc))
    db.add(customer); db.commit(); db.refresh(customer)
    logger.info("customer_created alegra_contact_id=%s", customer.alegra_contact_id)
    return CreateResult(customer, "CREATED")


def update_customer(db: Session, customer: Customer, email: str | None, phone: str | None, address: str | None,
                    provider: AlegraProvider | None = None) -> Customer:
    """Only contact details are editable (never identification or name, which
    define the fiscal identity). Alegra is updated first; local only on success."""
    if customer.is_final_consumer:
        raise CustomerError("Consumidor Final es un contacto del sistema y no se edita desde el POS", 409)
    changes = {"email": _clean(email), "phone": _clean(phone), "address": _clean(address)}
    # Only non-empty values are sent: clearing a field in Alegra through the API
    # was not verified, so an emptied field is cleared locally only (a later
    # sync would bring Alegra's value back).
    payload: dict = {}
    if changes["email"]:
        payload["email"] = changes["email"]
    if changes["phone"]:
        payload["phonePrimary"] = changes["phone"]
    if changes["address"]:
        payload["address"] = {"address": changes["address"]}
    if payload:
        provider = provider or AlegraProvider()
        provider.update_contact(customer.alegra_contact_id, payload)
    for key, value in changes.items():
        setattr(customer, key, value)
    customer.last_synced_at = datetime.now(timezone.utc)
    db.commit(); db.refresh(customer)
    return customer


# ---- sync (Alegra -> local) --------------------------------------------------------------------------------

@dataclass
class SyncResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: int = 0
    error_messages: list[str] = field(default_factory=list)
    total_remote: int = 0
    finished_at: datetime | None = None


def sync_customers(db: Session, provider: AlegraProvider | None = None, max_pages: int = 400) -> SyncResult:
    """Pull every Alegra client contact (paginated, 30 per page) into Customer.
    Idempotent: keyed by alegra_contact_id, then by identification. Never deletes
    local customers that disappeared from Alegra. A per-contact problem is
    counted as an error and the sync continues; a transport/auth failure aborts
    (already-committed pages stay, and re-running is safe)."""
    provider = provider or AlegraProvider()
    result = SyncResult()
    start, page_size = 0, 30
    now = datetime.now(timezone.utc)
    for _ in range(max_pages):
        contacts, total = provider.list_contacts_page(start=start, limit=page_size)
        for contact in contacts:
            result.total_remote += 1
            _sync_one(db, contact, now, result)
        db.commit()
        start += len(contacts)
        if not contacts or len(contacts) < page_size or (total is not None and start >= total):
            break
    result.finished_at = now
    logger.info("customers_sync created=%s updated=%s unchanged=%s errors=%s",
                result.created, result.updated, result.unchanged, result.errors)
    return result


def _sync_one(db: Session, contact: dict, now: datetime, result: SyncResult) -> None:
    remote_id = str(contact.get("id") or "")
    try:
        if not remote_id:
            raise CustomerError("Contacto de Alegra sin id")
        fields = fields_from_alegra(contact)
    except CustomerError as exc:
        result.errors += 1; result.error_messages.append(str(exc))
        return
    by_remote = db.scalar(select(Customer).where(Customer.alegra_contact_id == remote_id))
    by_ident = find_by_identification(db, fields["identification_type"], fields["identification_number"])
    if by_remote and by_ident and by_remote.id != by_ident.id:
        result.errors += 1
        result.error_messages.append(
            f"Alegra {remote_id}: {fields['identification_type']} {fields['identification_number']} ya pertenece a otro cliente local")
        return
    if by_ident and not by_remote and by_ident.alegra_contact_id and by_ident.alegra_contact_id != remote_id:
        result.errors += 1
        result.error_messages.append(
            f"Alegra {remote_id} duplica el documento {fields['identification_type']} {fields['identification_number']} "
            f"del contacto {by_ident.alegra_contact_id}; revisa los duplicados en Alegra")
        return
    customer = by_remote or by_ident
    is_final = fields["identification_number"] == FINAL_CONSUMER_IDENTIFICATION
    if is_final:
        other = db.scalar(select(Customer).where(Customer.is_final_consumer.is_(True)))
        if other is not None and other is not customer:
            other.is_final_consumer = False  # Alegra's current one wins; keeps the partial unique index valid
            db.flush()
    if customer is None:
        db.add(Customer(alegra_contact_id=remote_id, is_final_consumer=is_final, last_synced_at=now, **fields))
        db.flush()
        result.created += 1
        return
    active = customer.active and fields["active"]
    changed = (customer.alegra_contact_id != remote_id or customer.is_final_consumer != is_final
               or customer.active != active or any(getattr(customer, key) != fields[key] for key in SYNCED_FIELDS))
    customer.alegra_contact_id = remote_id
    customer.is_final_consumer = is_final
    customer.active = active
    for key in SYNCED_FIELDS:
        setattr(customer, key, fields[key])
    customer.last_synced_at = now
    db.flush()
    if changed:
        result.updated += 1
    else:
        result.unchanged += 1


def last_sync_at(db: Session) -> datetime | None:
    return db.scalar(select(func.max(Customer.last_synced_at)))


# ---- Consumidor Final --------------------------------------------------------------------------------------

class FinalConsumerUnverified(AlegraError):
    """Alegra could not be asked (network, 5xx, rate limit): the contact is UNKNOWN, never
    assumed. Transient: the invoice waits as a retryable error, nothing is sent."""


def final_consumer_problem(contact, expected_id: str | None = None) -> str | None:
    """None when `contact` (an Alegra contact) is the DIAN consumidor final: identification
    222222222222, active, and — when given — the expected id. Otherwise why not."""
    if not isinstance(contact, dict) or not contact.get("id"):
        return f"El contacto {expected_id} no existe en Alegra"
    if expected_id is not None and str(contact["id"]) != str(expected_id):
        return f"Alegra devolvió el contacto {contact['id']} en vez del {expected_id}"
    try:
        fields = fields_from_alegra(contact)
    except CustomerError:
        return f"El contacto {contact['id']} de Alegra no tiene identificación"
    if fields["identification_number"] != FINAL_CONSUMER_IDENTIFICATION:
        return (f"El contacto {contact['id']} de Alegra no es Consumidor final: su identificación es "
                f"{fields['identification_number']}, no {FINAL_CONSUMER_IDENTIFICATION}")
    if not fields["active"]:
        return f"El contacto Consumidor final {contact['id']} está inactivo en Alegra"
    return None


def _final_consumer_candidate(db: Session) -> str | None:
    """Explicit ALEGRA_FINAL_CONSUMER_CONTACT_ID, else the row synced from Alegra by its
    identification. A candidate only: never billed to before verified_final_consumer_contact_id."""
    from app.core.config import settings
    if settings.alegra_final_consumer_contact_id:
        return settings.alegra_final_consumer_contact_id
    local = db.scalar(select(Customer).where(Customer.is_final_consumer.is_(True)))
    return local.alegra_contact_id if local and local.alegra_contact_id else None


def verified_final_consumer_contact_id(db: Session, provider: AlegraProvider | None = None) -> str:
    """The Alegra contact a sale WITHOUT customer is billed to, proven right now to be the DIAN
    consumidor final (identification 222222222222, active). One GET per invoice.

      candidate (setting or synced row) -> GET /contacts/{id} -> must be 222222222222, else blocked
                                           (never falls back to another id, never guesses)
      no candidate -> GET /contacts?identification=222222222222 -> exactly ONE exact match ->
                      linked locally (local write only); none or several -> blocked
    Never creates a contact in Alegra. Raises CustomerError (blocked: a person must fix the
    contact or the setting) or FinalConsumerUnverified (Alegra unreachable: retry later)."""
    provider = provider or AlegraProvider()
    candidate = _final_consumer_candidate(db)
    try:
        if candidate:
            problem = final_consumer_problem(provider.client.get(f"/contacts/{candidate}"), candidate)
            if problem:
                raise CustomerError(problem)
            return str(candidate)
        found = provider.find_contacts_by_identification(FINAL_CONSUMER_IDENTIFICATION)
    except (AlegraNetworkError, AlegraServerError, AlegraRateLimitError) as exc:
        raise FinalConsumerUnverified(f"No se pudo verificar el contacto Consumidor final en Alegra: {exc}") from exc
    except AlegraError as exc:            # 404, auth...: not a transient doubt
        raise CustomerError(f"No se pudo verificar el contacto Consumidor final en Alegra: {exc}") from exc
    matches = {str(c["id"]): c for c in found if final_consumer_problem(c) is None}
    if len(matches) != 1:
        raise CustomerError(
            "No hay un contacto Consumidor final (222222222222) activo en Alegra" if not matches else
            f"Alegra tiene {len(matches)} contactos Consumidor final ({', '.join(sorted(matches))}); "
            "configura ALEGRA_FINAL_CONSUMER_CONTACT_ID con el correcto")
    customer = _upsert_from_remote(db, next(iter(matches.values())))
    db.commit()
    return customer.alegra_contact_id


def resolve_final_consumer_contact_id(db: Session, provider: AlegraProvider | None = None,
                                      allow_remote: bool = True) -> str | None:
    """allow_remote=False: the local candidate only, for status screens — NEVER for an invoice.
    allow_remote=True: verified_final_consumer_contact_id (raises when it can't be proven)."""
    if not allow_remote:
        return _final_consumer_candidate(db)
    return verified_final_consumer_contact_id(db, provider)


def customer_snapshot(customer: Customer | None, alegra_contact_id: str) -> dict:
    """Immutable fiscal identity frozen on the ElectronicInvoice at issue time."""
    if customer is None:
        return {"final_consumer": True, "alegra_contact_id": alegra_contact_id, "name": "Consumidor final",
                "identification_type": "CC", "identification_number": FINAL_CONSUMER_IDENTIFICATION}
    return {"final_consumer": False, "alegra_contact_id": alegra_contact_id, "customer_id": str(customer.id),
            "name": customer.name, "identification_type": customer.identification_type,
            "identification_number": customer.identification_number, "dv": customer.dv,
            "kind_of_person": customer.kind_of_person, "email": customer.email}
