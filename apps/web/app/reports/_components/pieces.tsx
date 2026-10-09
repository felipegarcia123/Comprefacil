import type React from "react";
import { MetricCard, TouchButton } from "@/components/ui";
import { money } from "@/lib/format";

export function Kpi({ label, value }: { label: string; value: string }) {
  return <MetricCard module="informes" className="reports-kpi" label={label} value={value} />;
}

export function Row({ label, value }: { label: string; value: string }) {
  return <div className="operations-row"><span>{label}</span><b className="ui-num">{money.format(Number(value))}</b></div>;
}

export function ReportCard({ title, action, children }: { title: string; action?: React.ReactNode; children: React.ReactNode }) {
  return <section className="report-card"><header className="reports-card-header"><h2>{title}</h2>{action}</header>{children}</section>;
}

export function MetricToggle<T extends string>({ value, onChange, options }: { value: T; onChange: (v: T) => void; options: [T, string][] }) {
  return <div className="purchase-filters" role="group">{options.map(([key, label]) => <TouchButton key={key} type="button" size="md" variant={value === key ? "primary" : "secondary"} module="informes" onClick={() => onChange(key)}>{label}</TouchButton>)}</div>;
}

export function hourLabel(hour: string) { return hour.split(" ").pop() ?? hour; }
