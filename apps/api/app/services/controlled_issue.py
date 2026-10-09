"""Controlled FIRST real emission, step by step, for one explicit sale.

Never wired to the UI or to checkout. Two separate runs:
  1. without confirm -> catalog plan (which products would be linked / created in Alegra,
     GET only) + pre-flight + remote duplicate check + POST /invoices/preview (draft PDF,
     creates nothing; skipped while a line's item doesn't exist yet). Stops.
  2. with confirm AND the POS total typed back by the operator -> the same checks
     again, then the ONLY POST path (attempt_issue: claim + reconcile + pre-flight),
     then GET verification (id, ERG number, observations marker, legal status,
     CUFE, PDF, XML) and a final search proving there is exactly one invoice with
     this sale's marker.
"""
from decimal import Decimal

from sqlalchemy.orm import Session

from app.domain.enums import ElectronicInvoiceStatus
from app.models import Sale
from app.services.alegra_client import AlegraError
from app.services.alegra_provider import AlegraProvider
from app.services.electronic_invoicing import (HOLD_CODES, find_remote_invoices, get_electronic_invoice_for_sale,
                                               invoice_plan, issue_invoice, payload_total, stamp_existing_invoice,
                                               sync_invoice_status, sale_marker)


def _payload_total(payload: dict) -> Decimal:
    return payload_total(payload)          # same check attempt_issue enforces before any POST


def run(db: Session, sale: Sale, provider: AlegraProvider | None = None, confirm: bool = False,
        expected_total: Decimal | None = None, generate_stamp: bool = True) -> dict:
    provider = provider or AlegraProvider()
    report: dict = {"sale_id": str(sale.id), "sale_number": sale.number, "pos_total": str(sale.total),
                    "step": None, "ok": False, "problems": []}

    record = get_electronic_invoice_for_sale(db, sale.id)
    if record is not None and record.provider_invoice_id and record.display_status == "DRAFT":
        # Alegra already has this sale's invoice as a DRAFT: it is completed by stamping it.
        report["existing_record"] = {"status": record.status.value, "error_code": record.error_code,
                                     "provider_invoice_id": record.provider_invoice_id, "number": record.provider_number}
        report["problems"].append("La venta ya tiene un borrador en Alegra; no se crea otra factura "
                                  "(usar --stamp-existing para timbrarlo)")
        report["stamp_plan"] = stamp_existing_invoice(db, record, sale, provider, confirm=False)
        report["step"] = "STAMP_PENDING"
        return report
    if record is not None:
        report["existing_record"] = {"status": record.status.value, "error_code": record.error_code,
                                     "provider_invoice_id": record.provider_invoice_id}
        if record.provider_invoice_id or record.status == ElectronicInvoiceStatus.ISSUED:
            report["problems"].append("La venta ya tiene factura en Alegra; no se emite otra")
        if record.error_code in HOLD_CODES:
            report["problems"].append("La factura de esta venta está apartada para revisión manual")

    plan = invoice_plan(db, sale, provider, generate_stamp)
    report["catalog"] = [{k: r.get(k) for k in ("product_name", "reference", "status", "alegra_item_id", "reason",
                                                 "payload")} for r in plan["catalog"]["products"]]
    report["preflight"] = {"ready": plan["ready"], "reasons": plan["reasons"]}
    if not plan["ready"]:
        report["problems"].append("Pre-flight BLOCKED")
    if report["problems"]:
        report["step"] = "CHECKS"
        return report

    payload = plan["payload"]
    report["payload"] = payload
    report["payload_total"] = str(_payload_total(payload))
    if _payload_total(payload) != sale.total:
        report["problems"].append("El total del payload no coincide con el total de la venta")

    try:
        existing = find_remote_invoices(sale, payload, provider)
    except AlegraError as exc:
        report["problems"].append(f"No se pudo verificar en Alegra si ya existe: {exc}")
        existing = []
    if existing:
        report["problems"].append(f"Alegra ya tiene {len(existing)} factura(s) con {sale_marker(sale.id)}")

    to_create = [line["pending_reference"] for line in payload["items"] if line.get("pending_reference")]
    if to_create:
        # Alegra's preview needs real item ids; these items are created in step 2, right
        # before the invoice (same attempt_issue path as automatic invoicing).
        report["preview_pdf_url"] = None
        report["preview_skipped"] = f"{len(to_create)} ítem(s) se crearán al confirmar: {', '.join(to_create)}"
    else:
        try:
            report["preview_pdf_url"] = (provider.preview_invoice(payload) or {}).get("pdfUrl")
        except AlegraError as exc:
            report["problems"].append(f"POST /invoices/preview falló: {exc}")
    if report["problems"]:
        report["step"] = "PREVIEW"
        return report

    if not confirm:
        report.update(step="PREVIEW", ok=True)
        return report
    if expected_total is None or Decimal(str(expected_total)) != sale.total:
        report["problems"].append(f"Confirmación rechazada: el total confirmado ({expected_total}) "
                                  f"no es el total de la venta ({sale.total})")
        report["step"] = "CONFIRM"
        return report

    record = issue_invoice(db, sale, provider=provider, generate_stamp=generate_stamp)
    report["step"] = "ISSUED"
    report["record"] = {"status": record.status.value, "error_code": record.error_code,
                        "error_message": record.error_message, "provider_invoice_id": record.provider_invoice_id}
    if record.status != ElectronicInvoiceStatus.ISSUED:
        report["problems"].append("La emisión no terminó en ISSUED (ver record)")
        return report

    # Persist legal status / CUFE / PDF / XML on the record (GET only), then verify from that same answer.
    record = sync_invoice_status(db, record, provider)
    remote = record.provider_response or {}
    stamp = remote.get("stamp") or {}
    template = remote.get("numberTemplate") or {}
    report["verification"] = {
        "provider_invoice_id": remote.get("id"),
        "number": template.get("fullNumber") or f"{template.get('prefix') or ''}{template.get('number') or ''}",
        "observations_has_marker": sale_marker(sale.id) in str(remote.get("observations") or ""),
        "legal_status": stamp.get("legalStatus"),
        "cufe": stamp.get("cufe"),
        "pdf": remote.get("pdf"),
        "xml": remote.get("xml"),
        "total": remote.get("total"),
    }
    matches = find_remote_invoices(sale, payload, provider)
    report["verification"]["invoices_with_marker"] = len(matches)
    if len(matches) != 1:
        report["problems"].append(f"Se esperaba exactamente 1 factura con el marker y hay {len(matches)}")
    if not report["verification"]["observations_has_marker"]:
        report["problems"].append("La factura remota no contiene el marker POS sale_id en observations")
    report["ok"] = not report["problems"]
    return report


def run_stamp(db: Session, sale: Sale, provider: AlegraProvider | None = None, confirm: bool = False,
              expected_total: Decimal | None = None) -> dict:
    """Recovery of a remote DRAFT of this sale: stamp THAT invoice. Without confirm only
    checks (GET). With confirm requires the POS total typed back, like the first issue."""
    provider = provider or AlegraProvider()
    report: dict = {"sale_id": str(sale.id), "sale_number": sale.number, "pos_total": str(sale.total),
                    "step": "STAMP_PLAN", "ok": False, "problems": []}
    record = get_electronic_invoice_for_sale(db, sale.id)
    if record is None or not record.provider_invoice_id:
        report["problems"].append("La venta no tiene factura en Alegra")
        return report
    if confirm and (expected_total is None or Decimal(str(expected_total)) != sale.total):
        report["problems"].append(f"Confirmación rechazada: el total confirmado ({expected_total}) "
                                  f"no es el total de la venta ({sale.total})")
        return report
    plan = stamp_existing_invoice(db, record, sale, provider, confirm=confirm)
    report["stamp"] = plan
    report["problems"] += plan["problems"]
    report["step"] = ("ALREADY_STAMPED" if plan.get("already_stamped") else
                      "STAMPED" if plan.get("stamped") else ("STAMP_ATTEMPTED" if confirm else "STAMP_PLAN"))
    report["ok"] = not report["problems"] and bool(plan.get("stamped") or not confirm)
    if confirm:
        matches = find_remote_invoices(sale, record.provider_payload, provider)
        report["invoices_with_marker"] = len(matches)
        if len(matches) != 1:
            report["problems"].append(f"Se esperaba exactamente 1 factura con la marca y hay {len(matches)}")
            report["ok"] = False
    return report
