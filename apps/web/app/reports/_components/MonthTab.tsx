"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { InlineMessage } from "@/components/ui";
import { dateMedium as dateShort, errorText, money, percent } from "@/lib/format";
import type { MonthlyReport } from "@/lib/types";
import { CategoryToggleChart, ChannelChart, SalesTrend, TopProductsChart } from "@/components/report-charts";
import { Kpi, MetricToggle, ReportCard } from "./pieces";

type CategoryMetric = "total" | "cash" | "electronic";
type ProductMetric = "units" | "revenue" | "gross_profit";
const CATEGORY_METRICS: [CategoryMetric, string][] = [["total", "Total"], ["cash", "Efectivo"], ["electronic", "Electrónico"]];
const PRODUCT_METRICS: [ProductMetric, string][] = [["units", "Unidades"], ["revenue", "Facturación"], ["gross_profit", "Utilidad"]];

export function MonthTab() {
  const now = new Date();
  const [value, setValue] = useState(`${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`);
  const [report, setReport] = useState<MonthlyReport | null>(null);
  const [message, setMessage] = useState("");
  const [categoryMetric, setCategoryMetric] = useState<CategoryMetric>("total");
  const [productMetric, setProductMetric] = useState<ProductMetric>("revenue");

  useEffect(() => {
    const [year, month] = value.split("-").map(Number);
    setReport(null);
    api.monthlyReport(year, month).then(setReport).catch(e => setMessage(errorText(e)));
  }, [value]);

  const days = report?.days_breakdown.map(d => ({ label: dateShort.format(new Date(d.date)), sales: d.sales_total })) ?? [];
  const channels = report?.payment_channels.map(c => ({ name: c.channel_name, amount: c.amount })) ?? [];

  return (
    <>
      <div className="reports-month-picker"><label>Mes <input type="month" value={value} onChange={e => setValue(e.target.value)} /></label></div>
      {message && <InlineMessage tone="danger" onDismiss={() => setMessage("")}>{message}</InlineMessage>}
      {!report ? <p className="chart-empty">Cargando…</p> : !report.days ? <p className="chart-empty">No hay jornadas cerradas en este mes.</p> : (
        <>
          <div className="ui-metric-grid">
            <Kpi label="Ventas del mes" value={money.format(Number(report.sales_total))} />
            <Kpi label="Tickets" value={String(report.tickets)} />
            <Kpi label="Ticket promedio" value={money.format(Number(report.average_ticket))} />
            <Kpi label="Utilidad bruta" value={money.format(Number(report.gross_profit))} />
          </div>
          <div className="reports-secondary">
            <div><small>Efectivo</small><b className="ui-num">{money.format(Number(report.cash_total))}</b></div>
            <div><small>Electrónico</small><b className="ui-num">{money.format(Number(report.electronic_total))}</b></div>
            <div><small>Margen bruto</small><b className="ui-num">{percent(report.gross_margin, 2)}</b></div>
            <div><small>Jornadas</small><b className="ui-num">{report.days}</b></div>
          </div>
          <ReportCard title="Ventas por día"><SalesTrend data={days} /></ReportCard>
          <div className="reports-two">
            <ReportCard title="Categorías" action={<MetricToggle value={categoryMetric} onChange={setCategoryMetric} options={CATEGORY_METRICS} />}><CategoryToggleChart data={report.categories} metric={categoryMetric} /></ReportCard>
            <ReportCard title="Top productos" action={<MetricToggle value={productMetric} onChange={setProductMetric} options={PRODUCT_METRICS} />}><TopProductsChart data={report.products} metric={productMetric} /></ReportCard>
          </div>
          <ReportCard title="Medios de pago"><ChannelChart data={channels} /></ReportCard>
        </>
      )}
    </>
  );
}
