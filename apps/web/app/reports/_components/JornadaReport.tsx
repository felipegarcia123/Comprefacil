"use client";

import { useState } from "react";
import { StatusBadge } from "@/components/ui";
import { dateFull, money, percent, time } from "@/lib/format";
import type { DailyReportData } from "@/lib/types";
import { CategoryToggleChart, ChannelChart, TicketsChart, TopProductsChart } from "@/components/report-charts";
import { Kpi, MetricToggle, ReportCard, Row, hourLabel } from "./pieces";

export type ReportLike = { opened_at: string; closed_at: string; sales_total: string; cash_total: string; electronic_total: string; tickets: number; average_ticket: string; gross_profit: string; gross_margin: string; closing_difference: string | null; report_data: DailyReportData };
type CategoryMetric = "total" | "cash" | "electronic";
type ProductMetric = "units" | "revenue" | "gross_profit";
const CATEGORY_METRICS: [CategoryMetric, string][] = [["total", "Total"], ["cash", "Efectivo"], ["electronic", "Electrónico"]];
const PRODUCT_METRICS: [ProductMetric, string][] = [["units", "Unidades"], ["revenue", "Facturación"], ["gross_profit", "Utilidad"]];

/** Full jornada breakdown — same block for the live OPEN preview and any closed session's detail. */
export function JornadaReport({ report }: { report: ReportLike }) {
  const h = report.report_data.highlights;
  const [categoryMetric, setCategoryMetric] = useState<CategoryMetric>("total");
  const [productMetric, setProductMetric] = useState<ProductMetric>("revenue");
  const hourly = report.report_data.hourly.map(row => ({ label: `${hourLabel(row.hour)}:00`, tickets: row.tickets }));
  const channels = report.report_data.payment_channels.map(c => ({ name: c.channel_name, amount: c.amount }));
  const diff = report.closing_difference === null ? null : Number(report.closing_difference);
  const op = report.report_data.operations;
  const netSnapshot = report.report_data.sales_basis === "NET_COMPLETED_REFUNDS_V1";

  return (
    <div className="reports-content">
      <div className="reports-period"><strong>{dateFull.format(new Date(report.opened_at))}</strong><small>{time.format(new Date(report.opened_at))} – {time.format(new Date(report.closed_at))}</small></div>
      <div className="ui-metric-grid">
        <Kpi label={netSnapshot ? "Ventas netas" : report.report_data.returns ? "Ventas brutas" : "Ventas"} value={money.format(Number(report.sales_total))} />
        <Kpi label="Tickets" value={String(report.tickets)} />
        <Kpi label="Ticket promedio" value={money.format(Number(report.average_ticket))} />
        <Kpi label="Utilidad bruta" value={money.format(Number(report.gross_profit))} />
      </div>
      <div className="reports-secondary">
        <div><small>Efectivo</small><b className="ui-num">{money.format(Number(report.cash_total))}</b></div>
        <div><small>Electrónico</small><b className="ui-num">{money.format(Number(report.electronic_total))}</b></div>
        <div><small>Margen bruto</small><b className="ui-num">{percent(report.gross_margin, 2)}</b></div>
        {report.report_data.exchanges && report.report_data.exchanges.exchange_count > 0 && <>
          <div><small>Cambios de producto</small><b className="ui-num">{report.report_data.exchanges.exchange_count}</b></div>
          <div><small>Diferencias cobradas en cambios</small><b className="ui-num">{money.format(Number(report.report_data.exchanges.difference_collected))}</b></div>
          {Number(report.report_data.exchanges.difference_refunded ?? 0) > 0 && <div><small>Diferencias devueltas en cambios</small><b className="ui-num">−{money.format(Number(report.report_data.exchanges.difference_refunded))}</b></div>}
        </>}
        {report.report_data.returns && Number(report.report_data.returns.returns_total) > 0 && <>
          <div><small>Devoluciones de dinero</small><b className="ui-num">−{money.format(Number(report.report_data.returns.returns_total))}</b></div>
          <div><small>Ventas netas</small><b className="ui-num">{money.format(Number(report.report_data.returns.net_sales))}</b></div>
        </>}
        {diff === null ? <div><small>Diferencia caja</small><b>Jornada en curso</b></div> :
          <div><small>Diferencia caja</small><StatusBadge tone={diff < 0 ? "danger" : diff > 0 ? "warning" : "success"}>{diff < 0 ? "Faltan" : diff > 0 ? "Sobran" : "Cuadrada"} {money.format(Math.abs(diff))}</StatusBadge></div>}
      </div>
      <section className="reports-insights">
        <span>Medio de pago principal<b>{h.payment_channel_by_revenue?.channel_name ?? "—"}</b></span>
        <span>Hora pico<b>{h.peak_hour_by_tickets ? `${hourLabel(h.peak_hour_by_tickets.hour)} · ${h.peak_hour_by_tickets.tickets} tickets` : "—"}</b></span>
        <span>Producto más vendido<b>{h.top_product_by_units?.product_name ?? "—"}</b></span>
        <span>Categoría principal<b>{h.top_category_by_revenue?.name ?? "—"}</b></span>
      </section>
      <div className="reports-two">
        <ReportCard title="Tickets por hora"><TicketsChart data={hourly} /></ReportCard>
        <ReportCard title="Medios de pago"><ChannelChart data={channels} /></ReportCard>
      </div>
      <div className="reports-two">
        <ReportCard title="Categorías" action={<MetricToggle value={categoryMetric} onChange={setCategoryMetric} options={CATEGORY_METRICS} />}><CategoryToggleChart data={report.report_data.categories} metric={categoryMetric} /></ReportCard>
        <ReportCard title="Top productos" action={<MetricToggle value={productMetric} onChange={setProductMetric} options={PRODUCT_METRICS} />}><TopProductsChart data={report.report_data.products} metric={productMetric} /></ReportCard>
      </div>
      <section className="operations-panel" aria-label="Operación">
        <header className="operations-panel__head"><h2>Operación</h2></header>
        <Row label="Gastos" value={op.expenses} />
        <Row label="Pagos a proveedores" value={op.supplier_payments} />
        <Row label="Retiros Casa" value={op.withdrawals_home} />
        <Row label="Otros retiros" value={op.withdrawals_other} />
        <Row label="Consumo interno" value={op.internal_consumption} />
        <Row label="Ingresos" value={op.deposits} />
      </section>
    </div>
  );
}
