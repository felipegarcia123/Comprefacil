import type { ElectronicInvoice, InvoiceDisplayState } from "./types";

export type InvoiceTone = "neutral" | "warning" | "info" | "success" | "danger";

/** One vocabulary for every screen. A draft in Alegra is never "Emitida". */
export const INVOICE_STATE: Record<InvoiceDisplayState | "NONE", { label: string; tone: InvoiceTone }> = {
  NONE: { label: "Sin factura", tone: "neutral" },
  PENDING: { label: "Pendiente", tone: "warning" },
  BLOCKED: { label: "Bloqueada", tone: "warning" },
  HELD: { label: "Fuera de emisión", tone: "neutral" },
  PROCESSING: { label: "Procesando", tone: "info" },
  DRAFT: { label: "Borrador en Alegra", tone: "warning" },
  SENT: { label: "Emitida", tone: "info" },
  ACCEPTED: { label: "Aceptada DIAN", tone: "success" },
  DIAN_REJECTED: { label: "Rechazada DIAN", tone: "danger" },
  REJECTED: { label: "Rechazada", tone: "danger" },
  ERROR: { label: "Error", tone: "danger" },
};

export const STATE_ORDER: InvoiceDisplayState[] = ["PENDING", "BLOCKED", "PROCESSING", "DRAFT", "SENT", "ACCEPTED", "DIAN_REJECTED", "ERROR", "HELD", "REJECTED"];

/** What the cashier sees right after COBRAR: three plain states, never Alegra/DIAN details
 * (those live in the invoice panel and in Facturación electrónica). */
export type CashierInvoiceState = "issued" | "pending" | "review";

// ERROR codes a retry alone won't fix: a person has to look. Every other ERROR is transient.
const REVIEW_ERRORS = new Set(["AlegraValidationError", "AlegraAuthError", "CONFIG", "DUPLICATE_IN_ALEGRA"]);

export function cashierInvoiceState(invoice: ElectronicInvoice | null): CashierInvoiceState {
  const state = invoice?.display_status;
  if (invoice?.error_code && REVIEW_ERRORS.has(invoice.error_code)) return "review";
  if (state === "ACCEPTED" || state === "SENT") return "issued";
  if (!invoice || state === "PENDING" || state === "PROCESSING" || state === "ERROR") return "pending";
  return "review";
}

export const CASHIER_INVOICE: Record<CashierInvoiceState, { label: string; tone: InvoiceTone }> = {
  issued: { label: "Factura electrónica emitida", tone: "success" },
  pending: { label: "Factura electrónica pendiente…", tone: "info" },
  review: { label: "Factura electrónica requiere revisión", tone: "warning" },
};
