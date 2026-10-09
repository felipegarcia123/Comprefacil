"use client";
import { Bar, BarChart, CartesianGrid, Cell, Line, LineChart, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type React from "react";
import type { DashboardReport } from "@/lib/types";
import { formatMoney as money } from "@/lib/format";

/*
 * Same chart logic/props as before Phase 5 — only the surface changed: tokens instead of hardcoded
 * hex, Atkinson for ticks/tooltips (Bricolage is reserved for titles/TOTAL, never dense chart text),
 * `--mod-strong`/`--mod-deep` so a chart automatically matches whichever module renders it (today
 * only Informes, teal). No chart was added "por decoración" — this file exports exactly the same
 * set of charts reports/page.tsx and the ex-dashboard tab both already relied on.
 */
// Axis labels follow the 14 px text floor of the design system (they were 12 px).
const AXIS_TICK = { fontFamily: "var(--font-sans)", fontSize: 14, fill: "var(--color-ink-muted)" };
const GRID_COLOR = "var(--color-line)";
const TOOLTIP_STYLE = { background: "var(--color-surface)", border: "1px solid var(--color-line)", borderRadius: "var(--radius-input)", boxShadow: "var(--shadow-panel)", fontFamily: "var(--font-sans)", fontSize: 13 };
const TOOLTIP_LABEL_STYLE = { color: "var(--color-ink)", fontWeight: 700, marginBottom: 4 };
const PRIMARY = "var(--mod-strong, #087d6d)";
const DEEP = "var(--mod-deep, #075766)";
const AMBER = "var(--color-warning)";
const MUTED = "var(--color-ink-muted)";
const CHANNEL = "var(--color-informes-deep, #075766)";

const tooltipMoney = (value: unknown) => [money(value), "Ventas"];

export function SalesTrend({ data }: { data: { label: string; sales: string }[] }) {
  return <ChartEmpty data={data}><ResponsiveContainer width="100%" height={250}><LineChart data={data}><CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} /><XAxis dataKey="label" tick={AXIS_TICK} /><YAxis tick={AXIS_TICK} tickFormatter={v => compact(v)} /><Tooltip formatter={tooltipMoney} contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL_STYLE} /><Line type="monotone" dataKey="sales" stroke={PRIMARY} strokeWidth={3} dot={false} /></LineChart></ResponsiveContainer></ChartEmpty>;
}
export function TicketsChart({ data }: { data: { label: string; tickets: number }[] }) {
  return <ChartEmpty data={data}><ResponsiveContainer width="100%" height={220}><BarChart data={data}><CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} /><XAxis dataKey="label" tick={AXIS_TICK} /><YAxis allowDecimals={false} tick={AXIS_TICK} /><Tooltip formatter={v => [v, "Tickets"]} contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL_STYLE} /><Bar dataKey="tickets" fill={DEEP} radius={[4, 4, 0, 0]} /></BarChart></ResponsiveContainer></ChartEmpty>;
}
export function AverageChart({ data }: { data: DashboardReport["series"] }) {
  return <ChartEmpty data={data}><ResponsiveContainer width="100%" height={220}><LineChart data={data}><CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} /><XAxis dataKey="label" tick={AXIS_TICK} /><YAxis tick={AXIS_TICK} tickFormatter={v => compact(v)} /><Tooltip formatter={v => [money(v), "Ticket promedio"]} contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL_STYLE} /><Line type="monotone" dataKey="average_ticket" stroke={AMBER} strokeWidth={2} /></LineChart></ResponsiveContainer></ChartEmpty>;
}
export function CategoryChart({ data }: { data: DashboardReport["categories"] }) {
  return <ChartEmpty data={data}><ResponsiveContainer width="100%" height={Math.max(230, data.length * 42)}><BarChart data={data} layout="vertical" margin={{ left: 25 }}><CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} /><XAxis type="number" tick={AXIS_TICK} tickFormatter={v => compact(v)} /><YAxis type="category" dataKey="name" width={125} tick={AXIS_TICK} /><Tooltip formatter={v => [money(v), "Ventas"]} contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL_STYLE} /><Bar dataKey="total" fill={PRIMARY} radius={[0, 4, 4, 0]} /></BarChart></ResponsiveContainer></ChartEmpty>;
}
export function PaymentDonut({ data }: { data: DashboardReport["payment_groups"] }) {
  const colors = [PRIMARY, AMBER, MUTED];
  return <ChartEmpty data={data}><ResponsiveContainer width="100%" height={240}><PieChart><Pie data={data} dataKey="amount" nameKey="group" innerRadius={55} outerRadius={85}>{data.map((x, i) => <Cell key={x.group} fill={colors[i % colors.length]} />)}</Pie><Tooltip formatter={v => money(v)} contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL_STYLE} /></PieChart></ResponsiveContainer><div className="chart-legend">{data.map((x, i) => <span key={x.group}><i style={{ background: colors[i % colors.length] }} />{x.group}: <b>{money(x.amount)}</b></span>)}</div></ChartEmpty>;
}
export function ChannelChart({ data }: { data: { name: string; amount: string }[] }) {
  return <ChartEmpty data={data}><ResponsiveContainer width="100%" height={240}><BarChart data={data}><CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} /><XAxis dataKey="name" tick={AXIS_TICK} /><YAxis tick={AXIS_TICK} tickFormatter={v => compact(v)} /><Tooltip formatter={v => money(v)} contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL_STYLE} /><Bar dataKey="amount" fill={CHANNEL} radius={[4, 4, 0, 0]} /></BarChart></ResponsiveContainer></ChartEmpty>;
}
export function CategoryToggleChart({ data, metric }: { data: { code: string; name: string; total: string; cash: string; electronic: string }[]; metric: "total" | "cash" | "electronic" }) {
  const rows = data.map(d => ({ name: d.name, value: Number(d[metric]) }));
  return <ChartEmpty data={rows}><ResponsiveContainer width="100%" height={Math.max(200, rows.length * 42)}><BarChart data={rows} layout="vertical" margin={{ left: 25 }}><CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} /><XAxis type="number" tick={AXIS_TICK} tickFormatter={v => compact(v)} /><YAxis type="category" dataKey="name" width={135} tick={AXIS_TICK} /><Tooltip formatter={v => [money(v), "Ventas"]} contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL_STYLE} /><Bar dataKey="value" fill={PRIMARY} radius={[0, 4, 4, 0]} /></BarChart></ResponsiveContainer></ChartEmpty>;
}
export function TopProductsChart({ data, metric }: { data: { product_name: string; units: string; revenue: string; gross_profit: string }[]; metric: "units" | "revenue" | "gross_profit" }) {
  const label = metric === "units" ? "Unidades" : metric === "revenue" ? "Facturación" : "Utilidad";
  const rows = [...data].sort((a, b) => Number(b[metric]) - Number(a[metric])).slice(0, 10).map(d => ({ name: d.product_name, value: Number(d[metric]) }));
  return <ChartEmpty data={rows}><ResponsiveContainer width="100%" height={Math.max(200, rows.length * 36)}><BarChart data={rows} layout="vertical" margin={{ left: 25 }}><CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} /><XAxis type="number" tick={AXIS_TICK} tickFormatter={v => compact(v)} /><YAxis type="category" dataKey="name" width={150} tick={AXIS_TICK} /><Tooltip formatter={v => [metric === "units" ? String(v) : money(v), label]} contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL_STYLE} /><Bar dataKey="value" fill={DEEP} radius={[0, 4, 4, 0]} /></BarChart></ResponsiveContainer></ChartEmpty>;
}
function ChartEmpty({ data, children }: { data: unknown[]; children: React.ReactNode }) { return data.length ? <>{children}</> : <div className="chart-empty">No hay ventas registradas en este período.</div>; }
function compact(value: unknown) { return new Intl.NumberFormat("es-CO", { notation: "compact", maximumFractionDigits: 1 }).format(Number(value)); }

/** 0028 — balance / patrimony over time (one point per frozen close; jornadas without photo are not points). */
export function BalanceTrend({ data, label = "Valor" }: { data: { label: string; value: number }[]; label?: string }) {
  // The caller shows its own empty state (a balance chart is not "no sales").
  return <ResponsiveContainer width="100%" height={240}><LineChart data={data}><CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} /><XAxis dataKey="label" tick={AXIS_TICK} /><YAxis tick={AXIS_TICK} tickFormatter={v => compact(v)} width={64} /><Tooltip formatter={(value: unknown) => [money(value), label]} contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL_STYLE} /><Line type="monotone" dataKey="value" stroke={PRIMARY} strokeWidth={3} dot={{ r: 4, fill: DEEP }} /></LineChart></ResponsiveContainer>;
}
