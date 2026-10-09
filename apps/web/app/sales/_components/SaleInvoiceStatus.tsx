"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { StatusBadge } from "@/components/ui";
import { CASHIER_INVOICE, cashierInvoiceState } from "@/lib/invoiceStates";
import type { ElectronicInvoice } from "@/lib/types";

const POLL_MS = 2000;
const WINDOW_MS = 30000;

/**
 * Compact invoice state of a sale that was just charged. The invoice is processed after the sale
 * committed, so it asks a few times (every 2 s, at most ~30 s) until it leaves "pendiente"; after
 * that it stays as last seen — the full detail lives in the invoice panel. Never retries anything.
 */
export function SaleInvoiceStatus({ saleId }: { saleId: string }) {
  const [invoice, setInvoice] = useState<ElectronicInvoice | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const started = Date.now();
    setInvoice(null);
    async function poll() {
      try {
        const found = await api.electronicInvoiceForSale(saleId);
        if (cancelled) return;
        setInvoice(found);
        if (cashierInvoiceState(found) !== "pending") return;
      } catch { /* network hiccup: keep the last state, ask again within the window */ }
      if (!cancelled && Date.now() - started < WINDOW_MS) timer = setTimeout(() => void poll(), POLL_MS);
    }
    void poll();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [saleId]);

  const state = cashierInvoiceState(invoice);
  const { label, tone } = CASHIER_INVOICE[state];
  return (
    <span role="status" aria-live="polite">
      <StatusBadge tone={tone}>{label}{state === "issued" && invoice?.provider_number ? ` — ${invoice.provider_number}` : ""}</StatusBadge>
    </span>
  );
}
