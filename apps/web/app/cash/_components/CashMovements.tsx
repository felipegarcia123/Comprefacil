"use client";

import { useState } from "react";
import { ChevronRight } from "lucide-react";
import { DataTable, StatusBadge, Td, Th, TouchButton, Tr } from "@/components/ui";
import { money, signed, time } from "@/lib/format";
import type { CashSummary, FinancialSessionMovement } from "@/lib/types";
import { SaleDetailSheet } from "./SaleDetailSheet";

type Filter = "ALL" | "SALE" | "EXCHANGE" | "REFUND" | "SUPPLIER_PAYMENT" | "DEPOSIT" | "WITHDRAWAL" | "EXPENSE";
const FILTERS: [Filter, string][] = [["ALL", "Todos"], ["SALE", "Ventas"], ["EXCHANGE", "Cambios"], ["REFUND", "Devoluciones de dinero"], ["SUPPLIER_PAYMENT", "Proveedores"], ["DEPOSIT", "Ingresos"], ["WITHDRAWAL", "Retiros"], ["EXPENSE", "Gastos"]];

function subtitle(row: FinancialSessionMovement): string {
  const parts: string[] = [];
  if (row.type === "WITHDRAWAL" && row.withdrawal_reason) parts.push(row.withdrawal_reason === "HOME" ? "Casa" : "Otro");
  if (row.payment_channel) parts.push(row.payment_channel);
  parts.push(row.affects_cash ? "Afecta caja" : "No afecta caja física");
  if (row.subtitle && row.subtitle !== row.payment_channel) parts.push(row.subtitle);
  return parts.join(" · ");
}

/** Ventas, movimientos de caja y pagos a proveedores en una sola línea de tiempo — nunca dos ledgers paralelos. */
export function CashMovements({ summary, onChanged }: { summary: CashSummary; onChanged?: () => void }) {
  const [filter, setFilter] = useState<Filter>("ALL");
  const [saleId, setSaleId] = useState<string | null>(null);   // DETALLE DE VENTA (post-venta)
  const rows = summary.financial_movements.filter(row => filter === "ALL" || row.type === filter);

  return (
    <section className="cash-panel" aria-label="Movimientos de la sesión">
      <header className="cash-panel__head">
        <div><h2>Movimientos de la sesión</h2><p>Ventas, caja y pagos a proveedores · más reciente primero · toca una venta para ver su detalle</p></div>
      </header>
      <div className="purchase-filters" role="group" aria-label="Filtrar movimientos">
        {FILTERS.map(([value, label]) => <TouchButton key={value} variant={filter === value ? "primary" : "secondary"} module="caja" size="md" onClick={() => setFilter(value)}>{label}</TouchButton>)}
      </div>
      {rows.length ? (
        <DataTable caption="Movimientos de la sesión" className="cash-cart" module="caja" density="dense">
          <thead><tr><Th>Hora</Th><Th>Concepto</Th><Th numeric>Monto</Th></tr></thead>
          <tbody>
            {rows.map(row => {
              const sale = row.reference_type === "SALE" && row.reference_id ? row.reference_id : null;
              return (
              <Tr key={row.id} interactive={sale !== null} tabIndex={sale ? 0 : undefined} aria-label={sale ? `Ver detalle: ${row.title}` : undefined}
                onClick={sale ? () => setSaleId(sale) : undefined}
                onKeyDown={sale ? event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSaleId(sale); } } : undefined}>
                <Td label="Hora" muted className="ui-num">{time.format(new Date(row.occurred_at))}</Td>
                <Td label="Concepto">
                  <span className="cash-concept">
                    <span className="cash-concept__text"><b>{row.title}</b><small>{subtitle(row)}</small></span>
                  </span>
                </Td>
                <Td numeric label="Monto" className={row.direction === "IN" ? "pos ui-num" : "neg ui-num"}>
                  <span className="cash-amount">{signed(row.direction === "IN" ? Number(row.amount) : -Number(row.amount))}
                    {sale ? <ChevronRight className="cash-concept__go" size={24} aria-hidden="true" /> : <span className="cash-concept__spacer" aria-hidden="true" />}
                  </span>
                </Td>
              </Tr>
              );
            })}
          </tbody>
        </DataTable>
      ) : <p className="ui-empty-hint">No hay movimientos para este filtro.</p>}
      <SaleDetailSheet saleId={saleId} onClose={() => setSaleId(null)} onChanged={() => onChanged?.()} />
    </section>
  );
}

/** Small label:amount list — total vendido por canal / pagos a proveedores por canal. */
export function ChannelList({ rows, empty }: { rows: { channel_id: string; channel_name: string; payment_type: string; amount: string; payment_count: number }[]; empty: string }) {
  if (!rows.length) return <p className="ui-empty-hint">{empty}</p>;
  return (
    <ul className="cash-channel-list">
      {rows.map(row => (
        <li key={row.channel_id}>
          <span>{row.channel_name}<small>{row.payment_count} pago{row.payment_count === 1 ? "" : "s"}</small></span>
          <b className="ui-num">{money.format(Number(row.amount))}</b>
        </li>
      ))}
    </ul>
  );
}

export function DifferenceBadge({ value }: { value: number }) {
  const tone = value < 0 ? "danger" : value > 0 ? "warning" : "success";
  const label = value < 0 ? "Faltan" : value > 0 ? "Sobran" : "Cuadrada";
  return <StatusBadge tone={tone}>{label}{value !== 0 && ` ${money.format(Math.abs(value))}`}</StatusBadge>;
}
