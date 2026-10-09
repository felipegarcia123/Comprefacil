"use client";

import { useEffect, useState } from "react";
import { FileCode2, FileText, LoaderCircle } from "lucide-react";
import { api } from "@/lib/api";
import { dateTime, errorText, formatQuantity, money } from "@/lib/format";
import { INVOICE_STATE } from "@/lib/invoiceStates";
import type { InvoiceDetail } from "@/lib/types";
import { InlineMessage, StatusBadge, TouchButton } from "@/components/ui";

const CARD = { DEBIT: "Débito", CREDIT: "Crédito" } as const;
const ALEGRA_STATUS: Record<string, string> = { draft: "Borrador", open: "Emitida", closed: "Pagada", void: "Anulada", deleted: "Eliminada en Alegra" };

/** DIAN's answer in plain words; the raw value stays visible (small) for support. */
function legalLabel(value: string | null): string {
  if (!value) return "—";
  const upper = value.toUpperCase();
  if (upper.includes("REJECT")) return "Rechazada por la DIAN";
  if (upper.includes("ACCEPTED_WITH_OBSERVATIONS")) return "Aceptada por la DIAN con observaciones";
  if (upper.includes("ACCEPT")) return "Aceptada por la DIAN";
  return "En proceso con la DIAN";
}

/**
 * One invoice: sale, customer, payments, local + Alegra + DIAN state, CUFE, attempts and human reasons.
 * PDF/XML go through the backend, which refreshes expired signed links (GET only) — the permanent source is
 * the Alegra invoice id, never the stored URL. "Reintentar" exists only once automatic issue is on.
 */
export function InvoiceDetailView({ id, automatic, onChanged }: { id: string; automatic: boolean; onChanged: () => void }) {
  const [detail, setDetail] = useState<InvoiceDetail | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<"" | "pdf" | "xml" | "retry">("");

  useEffect(() => {
    setDetail(null); setError("");
    api.invoiceDetail(id).then(setDetail).catch(cause => setError(errorText(cause)));
  }, [id]);

  async function open(kind: "pdf" | "xml") {
    // Open the tab synchronously (popup blockers), then point it to the fresh link.
    const tab = window.open("", "_blank");
    setBusy(kind); setError("");
    try {
      const { url } = await api.invoiceDocument(id, kind);
      if (tab) { tab.opener = null; tab.location.href = url; } else window.location.assign(url);
    } catch (cause) { tab?.close(); setError(errorText(cause)); }
    finally { setBusy(""); }
  }

  async function retry() {
    setBusy("retry"); setError("");
    try { await api.retryElectronicInvoice(id); setDetail(await api.invoiceDetail(id)); onChanged(); }
    catch (cause) { setError(errorText(cause)); }
    finally { setBusy(""); }
  }

  if (error && !detail) return <div className="einv-detail"><InlineMessage tone="danger">{error}</InlineMessage></div>;
  if (!detail) return <div className="sale-status" role="status"><LoaderCircle className="ui-btn__spin" size={28} aria-hidden="true" />Cargando…</div>;

  const state = INVOICE_STATE[detail.display_status];
  return (
    <div className="einv-detail">
      <div className="einv-detail__head">
        <div>
          <h2 className="einv-detail__title">{detail.sale_number}</h2>
          <p className="einv-detail__sub">{dateTime.format(new Date(detail.completed_at))} · {detail.customer}</p>
        </div>
        <StatusBadge tone={state.tone}>{state.label}</StatusBadge>
      </div>

      {detail.reasons.length > 0 && (
        <InlineMessage tone={detail.display_status === "ERROR" ? "danger" : "warning"} title={detail.display_status === "HELD" ? "No se envía desde el POS" : "Por qué no se facturó"}>
          <ul className="einvoice-reasons">
            {detail.reasons.map(reason => <li key={reason.code}>{reason.message}{reason.detail && reason.detail !== reason.message ? <small className="einv-detail__why">{reason.detail}</small> : null}</li>)}
          </ul>
        </InlineMessage>
      )}

      <dl className="purchase__receipt-meta">
        <div><dt>Total</dt><dd className="ui-num">{money.format(Number(detail.total))}</dd></div>
        <div><dt>Número</dt><dd>{detail.provider_number ?? "—"}</dd></div>
        <div><dt>Estado en Alegra</dt><dd>{detail.provider_status ? ALEGRA_STATUS[detail.provider_status] ?? detail.provider_status : "—"}</dd></div>
        <div><dt>Estado DIAN</dt><dd>{legalLabel(detail.legal_status)}{detail.legal_status && <small className="einv-detail__why">{detail.legal_status}</small>}</dd></div>
        <div><dt>Intentos</dt><dd className="ui-num">{detail.attempt_count}</dd></div>
        <div><dt>Último intento</dt><dd>{detail.last_attempt_at ? dateTime.format(new Date(detail.last_attempt_at)) : "—"}</dd></div>
      </dl>
      {detail.cufe && <p className="einv-detail__cufe"><span>CUFE</span><code>{detail.cufe}</code></p>}

      <div>
        <h3 className="einv-detail__h3">Productos</h3>
        <ul className="einv-detail__lines">
          {detail.items.map((item, index) => (
            <li key={index}><span>{formatQuantity(item.quantity)} × {item.product_name}</span><b className="ui-num">{money.format(Number(item.line_total))}</b></li>
          ))}
        </ul>
        <h3 className="einv-detail__h3">Pagos</h3>
        <ul className="einv-detail__lines">
          {detail.payments.map((payment, index) => (
            <li key={index}><span>{payment.channel_name}{payment.card_type ? ` · ${CARD[payment.card_type]}` : ""}</span><b className="ui-num">{money.format(Number(payment.amount))}</b></li>
          ))}
        </ul>
      </div>

      {error && <InlineMessage tone="danger" onDismiss={() => setError("")}>{error}</InlineMessage>}
      <div className="ui-form__actions einv-detail__actions">
        {detail.has_pdf && <TouchButton variant="secondary" icon={FileText} loading={busy === "pdf"} onClick={() => void open("pdf")}>PDF</TouchButton>}
        {detail.has_xml && <TouchButton variant="secondary" icon={FileCode2} loading={busy === "xml"} onClick={() => void open("xml")}>XML</TouchButton>}
        {automatic && detail.retryable && <TouchButton module="informes" loading={busy === "retry"} onClick={() => void retry()}>Reintentar</TouchButton>}
      </div>
    </div>
  );
}
