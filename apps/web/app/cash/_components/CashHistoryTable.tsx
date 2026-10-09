"use client";

import { DataTable, StatusBadge, Td, Th, Tr } from "@/components/ui";
import { dateMedium, money, time } from "@/lib/format";
import { useBreakpoint } from "@/lib/useBreakpoint";
import type { CashHistoryRow } from "@/lib/types";

type Props = { rows: CashHistoryRow[]; onSelect: (id: string) => void };

/** Últimas jornadas (acotado por el backend, ver `cash_history`): nunca la historia completa de vida del negocio. */
export function CashHistoryTable({ rows, onSelect }: Props) {
  const breakpoint = useBreakpoint();
  const compact = breakpoint === "movil";
  const wide = breakpoint === "pos";
  if (!rows.length) return <p className="ui-empty-hint">No hay sesiones de caja todavía.</p>;
  return (
    <DataTable caption="Historial de jornadas" className="cash-cart" module="caja" density="dense" stack={compact}>
      <thead><tr><Th>Fecha</Th>{!compact && <Th>Apertura</Th>}{!compact && <Th>Cierre</Th>}<Th numeric>Monto inicial</Th>{!compact && <Th numeric>Ventas efectivo</Th>}<Th numeric>Esperado</Th>{!compact && <Th numeric>Contado</Th>}<Th numeric>Diferencia</Th>{wide && <Th numeric>Patrimonio declarado</Th>}<Th>Estado</Th></tr></thead>
      <tbody>
        {rows.map(row => {
          const diff = row.session.difference === null ? null : Number(row.session.difference);
          return (
            <Tr key={row.session.id} interactive onClick={() => onSelect(row.session.id)}>
              <Td label="Fecha">{dateMedium.format(new Date(row.session.opened_at))}</Td>
              {!compact && <Td label="Apertura" muted>{time.format(new Date(row.session.opened_at))}</Td>}
              {!compact && <Td label="Cierre" muted>{row.session.closed_at ? time.format(new Date(row.session.closed_at)) : "—"}</Td>}
              <Td numeric label="Monto inicial" className="ui-num">{money.format(Number(row.session.opening_amount))}</Td>
              {!compact && <Td numeric label="Ventas efectivo" className="ui-num">{money.format(Number(row.cash_sales))}</Td>}
              <Td numeric label="Esperado" className="ui-num">{money.format(Number(row.expected_cash))}</Td>
              {!compact && <Td numeric label="Contado" className="ui-num">{row.session.closing_actual === null ? "—" : money.format(Number(row.session.closing_actual))}</Td>}
              <Td numeric label="Diferencia" className={diff === null ? "ui-num" : diff < 0 ? "neg ui-num" : diff > 0 ? "pos ui-num" : "ui-num"}>{diff === null ? "—" : money.format(diff)}</Td>
              {wide && (
                <Td numeric label="Patrimonio declarado" className="ui-num">
                  {row.declared_patrimony != null ? money.format(Number(row.declared_patrimony)) : <span className="cash-history__muted">{row.session.closed_at ? "Sin declarar" : "—"}</span>}
                  {row.accounts_total && row.inventory_value != null ? <small className="cash-history__accounts">Cuentas {row.accounts_registered}/{row.accounts_total}</small> : null}
                </Td>
              )}
              <Td label="Estado"><StatusBadge tone={row.session.status === "OPEN" ? "success" : "neutral"}>{row.session.status === "OPEN" ? "Abierta" : "Cerrada"}</StatusBadge></Td>
            </Tr>
          );
        })}
      </tbody>
    </DataTable>
  );
}
