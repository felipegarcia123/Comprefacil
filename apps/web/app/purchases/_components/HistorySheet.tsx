"use client";

import { useEffect, useState } from "react";
import { Sheet, TouchButton } from "@/components/ui";
import { PurchaseStatusBadge } from "@/components/PurchaseStatusBadge";
import { api } from "@/lib/api";
import { dateTime, errorText, money } from "@/lib/format";
import type { Purchase, PurchaseStatus } from "@/lib/types";

const FILTERS: ["" | PurchaseStatus, string][] = [["", "Todas"], ["DRAFT", "Borrador"], ["RECEIVED", "Recibidas"], ["CANCELLED", "Canceladas"]];

type Props = {
  open: boolean;
  onClose: () => void;
  onOpenPurchase: (purchase: Purchase) => void;
};

/** Purchase history: search + status filter, dense rows. Opening a DRAFT resumes it (becomes the active context); opening a RECEIVED/CANCELLED one just shows it for reference. */
export function HistorySheet({ open, onClose, onOpenPurchase }: Props) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<"" | PurchaseStatus>("");
  const [rows, setRows] = useState<Purchase[]>([]);
  const [error, setError] = useState("");

  useEffect(() => { if (!open) { setQuery(""); setStatus(""); } }, [open]);
  useEffect(() => {
    if (!open) return;
    const timer = setTimeout(() => { void load(); }, 200);
    return () => clearTimeout(timer);
    async function load() {
      try { setRows(await api.purchases(status)); } catch (e) { setError(errorText(e)); }
    }
  }, [open, status]);

  const filtered = query.trim()
    ? rows.filter(purchase => `${purchase.supplier_name} ${purchase.invoice_number ?? ""}`.toLowerCase().includes(query.trim().toLowerCase()))
    : rows;

  return (
    <Sheet open={open} title="Historial de compras" onClose={onClose} module="compras">
      <div className="ui-stack">
        <label className="ui-field"><span>Buscar</span><input autoFocus value={query} onChange={event => setQuery(event.target.value)} placeholder="Proveedor o factura" /></label>
        <div className="purchase-filters" role="group" aria-label="Filtrar por estado">
          {FILTERS.map(([value, label]) => (
            <TouchButton key={value} variant={status === value ? "primary" : "secondary"} module="compras" size="md" onClick={() => setStatus(value)}>{label}</TouchButton>
          ))}
        </div>
        {error && <p className="ui-field-error" role="alert">{error}</p>}
        <ul className="ui-menu-list">
          {filtered.map(purchase => (
            <li key={purchase.id}>
              <button type="button" className="ui-menu-link purchase-history-row" onClick={() => onOpenPurchase(purchase)}>
                <span>{purchase.supplier_name}<small>{purchase.invoice_number ?? purchase.id.slice(0, 8)} · {dateTime.format(new Date(purchase.purchased_at))}</small></span>
                <span className="purchase-history-row__end"><PurchaseStatusBadge status={purchase.status} /><b className="ui-num">{money.format(Number(purchase.total))}</b></span>
              </button>
            </li>
          ))}
          {!filtered.length && <p className="ui-empty-hint">Sin compras para este filtro.</p>}
        </ul>
      </div>
    </Sheet>
  );
}
