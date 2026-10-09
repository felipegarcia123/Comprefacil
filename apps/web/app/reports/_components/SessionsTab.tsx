"use client";

import { useEffect, useState } from "react";
import { ArrowLeft } from "lucide-react";
import { api } from "@/lib/api";
import { DataTable, InlineMessage, StatusBadge, Td, Th, Tr } from "@/components/ui";
import { dateMedium, errorText, money } from "@/lib/format";
import { useBreakpoint } from "@/lib/useBreakpoint";
import type { DailyReportListItem, DailyReportOut } from "@/lib/types";
import { JornadaReport } from "./JornadaReport";

/** Últimas jornadas cerradas (el backend acota a 31 por defecto — nunca la historia completa). */
export function SessionsTab() {
  const [rows, setRows] = useState<DailyReportListItem[] | null>(null);
  const [detail, setDetail] = useState<DailyReportOut | null>(null);
  const [message, setMessage] = useState("");
  const compact = useBreakpoint() === "movil";

  useEffect(() => { api.dailyReports().then(setRows).catch(e => setMessage(errorText(e))); }, []);
  async function open(id: string) { try { setDetail(await api.dailyReport(id)); } catch (error) { setMessage(errorText(error)); } }

  if (detail) return (
    <div className="ui-stack">
      <button className="count-back-link" onClick={() => setDetail(null)}><ArrowLeft size={20} aria-hidden="true" />Jornadas</button>
      <JornadaReport report={detail} />
    </div>
  );

  return (
    <>
      {message && <InlineMessage tone="danger" onDismiss={() => setMessage("")}>{message}</InlineMessage>}
      {!rows ? <p className="chart-empty">Cargando…</p> : !rows.length ? <p className="chart-empty">No hay jornadas cerradas todavía.</p> : (
        <DataTable caption="Jornadas cerradas" className="reports-cart" module="informes" density="dense" stack={compact}>
          <thead><tr><Th>Fecha</Th><Th numeric>Ventas</Th>{!compact && <Th numeric>Tickets</Th>}{!compact && <Th numeric>Ticket prom.</Th>}{!compact && <Th numeric>Utilidad</Th>}{!compact && <Th numeric>Efectivo</Th>}{!compact && <Th numeric>Electrónico</Th>}<Th numeric>Dif. caja</Th><Th /></tr></thead>
          <tbody>
            {rows.map(row => {
              const diff = Number(row.closing_difference);
              return (
                <Tr key={row.id} interactive onClick={() => void open(row.id)}>
                  <Td label="Fecha">{dateMedium.format(new Date(row.opened_at))}</Td>
                  <Td numeric label="Ventas" className="ui-num">{money.format(Number(row.sales_total))}</Td>
                  {!compact && <Td numeric label="Tickets" className="ui-num">{row.tickets}</Td>}
                  {!compact && <Td numeric label="Ticket prom." className="ui-num">{money.format(Number(row.average_ticket))}</Td>}
                  {!compact && <Td numeric label="Utilidad" className="ui-num">{money.format(Number(row.gross_profit))}</Td>}
                  {!compact && <Td numeric label="Efectivo" className="ui-num">{money.format(Number(row.cash_total))}</Td>}
                  {!compact && <Td numeric label="Electrónico" className="ui-num">{money.format(Number(row.electronic_total))}</Td>}
                  <Td numeric label="Dif. caja" className={diff < 0 ? "neg ui-num" : diff > 0 ? "pos ui-num" : "ui-num"}>{money.format(diff)}</Td>
                  <Td label="Estado"><StatusBadge tone="neutral">Ver informe</StatusBadge></Td>
                </Tr>
              );
            })}
          </tbody>
        </DataTable>
      )}
    </>
  );
}
