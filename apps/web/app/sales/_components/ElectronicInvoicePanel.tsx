"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { errorText } from "@/lib/format";
import { InlineMessage, StatusBadge, TouchButton } from "@/components/ui";
import { formatIdentification } from "@/components/customers/identification";
import { INVOICE_STATE } from "@/lib/invoiceStates";
import type { ElectronicInvoice, ElectronicInvoiceConfigStatus, InvoiceCustomerSnapshot } from "@/lib/types";

function recipient(snapshot: InvoiceCustomerSnapshot | null | undefined): string {
  if (!snapshot) return "—";
  if (snapshot.final_consumer) return "Consumidor final";
  return `${snapshot.name} · ${formatIdentification(snapshot.identification_type, snapshot.identification_number, snapshot.dv)}`;
}

/**
 * Read-only invoice status of the last completed sale (none = it was charged without electronic invoice).
 * invoices are issued only through the controlled process; after it, automatically. The pre-flight explains,
 * in plain words, what would block this sale. "Reintentar" appears only once automatic issue is on.
 */
export function ElectronicInvoicePanel({ saleId }: { saleId: string }) {
  const [config, setConfig] = useState<ElectronicInvoiceConfigStatus | null>(null);
  const [invoice, setInvoice] = useState<ElectronicInvoice | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setInvoice(null); setError("");
    api.electronicInvoiceConfigStatus().then(setConfig).catch(() => setConfig(null));
    api.electronicInvoiceForSale(saleId).then(setInvoice).catch(() => setInvoice(null));
  }, [saleId]);

  async function run(action: () => Promise<ElectronicInvoice>) {
    setBusy(true); setError("");
    try { setInvoice(await action()); }
    catch (e) { setError(errorText(e)); }
    finally { setBusy(false); }
  }

  // Alegra isn't configured for this store yet — stay out of the cashier's way entirely.
  if (!config?.configured) return null;

  // While the invoice is being prepared (items linked/created in Alegra, then sent) the cashier only sees this.
  const state = invoice?.display_status === "PROCESSING" ? { label: "Preparando factura…", tone: "info" as const }
    : INVOICE_STATE[invoice?.display_status ?? "NONE"] ?? INVOICE_STATE.ERROR;
  const held = invoice?.display_status === "HELD";
  // Same rule as the backend's retryable(): not yet in Alegra (PENDING/ERROR/BLOCKED) -> protected retry;
  // a draft of this sale in Alegra -> the retry stamps that same draft. The backend re-checks everything.
  const canRetry = Boolean(config.auto_issue) && !held && invoice?.error_code !== "DUPLICATE_IN_ALEGRA" && (
    invoice?.status === "ERROR" || invoice?.status === "BLOCKED" || invoice?.status === "PENDING" || invoice?.display_status === "DRAFT");
  return (
    <section className="einvoice-panel">
      <header className="einvoice-panel__head"><h3>Facturación electrónica</h3><StatusBadge tone={state.tone}>{state.label}</StatusBadge></header>
      {invoice && <p className="operations-row"><span>Cliente</span><b>{invoice.customer_snapshot ? recipient(invoice.customer_snapshot) : "Pendiente de emisión"}</b></p>}
      {!invoice && <InlineMessage tone="info">Esta venta se cobró sin factura electrónica.</InlineMessage>}
      {invoice?.provider_number && <p className="operations-row"><span>Número</span><b>{invoice.provider_number}</b></p>}
      {invoice?.pdf_url && <a href={invoice.pdf_url} target="_blank" rel="noreferrer" className="einvoice-link">Ver PDF</a>}
      {invoice?.error_message && (
        <InlineMessage tone={held || invoice.status === "BLOCKED" ? "warning" : "danger"}
          title={held ? "Apartada: no se envía desde el POS" : invoice.status === "BLOCKED" ? "No se envió a Alegra" : invoice.needs_reconciliation ? "Se verificará en Alegra antes de reintentar" : undefined}>
          {invoice.error_message}
        </InlineMessage>
      )}
      <div className="ui-row">
        {canRetry && <TouchButton variant="secondary" disabled={busy} loading={busy} onClick={() => void run(() => api.retryElectronicInvoice(invoice!.id))}>Reintentar</TouchButton>}
        {invoice?.provider_invoice_id && <TouchButton variant="secondary" disabled={busy} loading={busy} onClick={() => void run(() => api.syncElectronicInvoice(invoice.id))}>Actualizar estado</TouchButton>}
      </div>
      {error && <InlineMessage tone="danger" onDismiss={() => setError("")}>{error}</InlineMessage>}
    </section>
  );
}
