"use client";

import { useCallback, useEffect, useState } from "react";
import { MetricCard } from "@/components/ui";
import { errorText, formatMoney as money, formatNumber as number } from "@/lib/format";
import { api } from "@/lib/api";
import type { DashboardReport } from "@/lib/types";
import { AverageChart, CategoryChart, ChannelChart, PaymentDonut, SalesTrend, TicketsChart } from "@/components/report-charts";
import { ReportCard } from "./pieces";

const PRESETS: [string, string][] = [["today", "Hoy"], ["yesterday", "Ayer"], ["last7", "Últimos 7 días"], ["month", "Este mes"], ["custom", "Rango personalizado"]];

/**
 * Ex-`/dashboard`: comparación contra el período anterior por rango de fechas libre — la única
 * lectura que Última jornada/Jornadas/Mes no cubren (esas van por jornada de caja, no por
 * calendario). Se conserva tal cual (mismo endpoint `GET /reports/dashboard`, mismos presets);
 * solo se restyló como una pestaña más de Informes en vez de una pantalla aparte (Fase 5, sección 15).
 */
export function RangeTab() {
  const [preset, setPreset] = useState("today");
  const [from, setFrom] = useState(""); const [to, setTo] = useState("");
  const [data, setData] = useState<DashboardReport | null>(null);
  const [loading, setLoading] = useState(true); const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try { setData(await api.dashboardReport(preset, preset === "custom" ? from : undefined, preset === "custom" ? to : undefined)); }
    catch (e) { setError(errorText(e, "No fue posible cargar el rango")); }
    finally { setLoading(false); }
  }, [preset, from, to]);

  useEffect(() => { if (preset !== "custom" || (from && to)) void load(); }, [load, preset, from, to]);

  return (
    <div className="ui-stack">
      <div className="reports-month-picker">
        <select className="inventory-select" value={preset} onChange={e => setPreset(e.target.value)}>
          {PRESETS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        {preset === "custom" && <>
          <input className="operations-search-input" type="date" value={from} onChange={e => setFrom(e.target.value)} />
          <input className="operations-search-input" type="date" value={to} min={from} onChange={e => setTo(e.target.value)} />
        </>}
      </div>
      {error && <p className="chart-empty">{error}</p>}
      {loading && !data ? <p className="chart-empty">Cargando…</p> : data && (
        <>
          <div className="ui-metric-grid">
            <Kpi label={Number(data.summary.returns_total ?? 0) > 0 ? "Ventas brutas" : "Ventas"} value={money(data.summary.sales_total)} change={data.summary.sales_change} />
            <Kpi label="Tickets cerrados" value={number(data.summary.tickets)} change={data.summary.tickets_change} />
            <Kpi label="Ticket promedio" value={money(data.summary.average_ticket)} change={data.summary.average_ticket_change} />
            <Kpi label="Unidades vendidas" value={number(data.summary.units)} />
          </div>
          <section className="reports-insights">
            <span>Hora con más tickets<b>{data.summary.peak_tickets_label ?? "—"} · {data.summary.peak_tickets}</b></span>
            <span>Mayor facturación<b>{data.summary.peak_sales_label ?? "—"} · {money(data.summary.peak_sales)}</b></span>
            <span>Categoría líder<b>{data.summary.leading_category ?? "—"}</b></span>
            <span>Canal principal<b>{data.summary.leading_channel ?? "—"}</b></span>
            {(data.summary.exchange_count ?? 0) > 0 && <span>Cambios · cobrado / devuelto<b>{data.summary.exchange_count} · +{money(data.summary.exchange_difference ?? 0)} / −{money(data.summary.exchange_difference_refunded ?? 0)}</b></span>}
            {Number(data.summary.returns_total ?? 0) > 0 && <span>Devoluciones de dinero · ventas netas<b>−{money(data.summary.returns_total ?? 0)} · {money(data.summary.net_sales ?? 0)}</b></span>}
          </section>
          <ReportCard title="Ventas en el tiempo"><SalesTrend data={data.series} /></ReportCard>
          <div className="reports-two">
            <ReportCard title="Tickets cerrados por hora"><TicketsChart data={data.series} /></ReportCard>
            <ReportCard title="Ticket promedio por hora"><AverageChart data={data.series} /></ReportCard>
          </div>
          <div className="reports-two">
            <ReportCard title="Ventas por categoría"><CategoryChart data={data.categories} /></ReportCard>
            <ReportCard title="Ventas por tipo de pago"><PaymentDonut data={data.payment_groups} /></ReportCard>
          </div>
          <div className="reports-two">
            <ReportCard title="Ventas por canal">
              <ChannelChart data={data.payment_channels} />
              <div className="reports-channel-list">{data.payment_channels.map(x => <span key={x.channel_id}>{x.name}<b>{money(x.amount)} · {x.payment_count} pagos</b></span>)}</div>
            </ReportCard>
            <ReportCard title="Productos más vendidos">
              <ol className="reports-top-products">{data.top_products.map(x => <li key={x.product_id}><span>{x.name}<small>{money(x.sales)}</small></span><b>{number(x.units)} uds</b></li>)}</ol>
              {!data.top_products.length && <div className="chart-empty">No hay productos vendidos en este período.</div>}
            </ReportCard>
          </div>
          {(Number(data.summary.expenses) > 0 || Number(data.summary.internal_consumption) > 0) && (
            <section className="reports-outgoing-info">
              <span>Gastos del período <b>{money(data.summary.expenses)}</b></span>
              <span>Consumo interno / Casa <b>{money(data.summary.internal_consumption)}</b></span>
              <small>Información separada de ventas; no representa utilidad neta.</small>
            </section>
          )}
        </>
      )}
    </div>
  );
}

function Kpi({ label, value, change }: { label: string; value: string; change?: string | null }) {
  const n = change == null ? null : Number(change);
  return (
    <MetricCard module="informes" className="reports-kpi" label={label} value={value}
      caption={n == null ? "Sin comparación" : `${n >= 0 ? "+" : ""}${number(n)}% vs período anterior`}
      captionTone={n != null && n < 0 ? "down" : undefined} />
  );
}
