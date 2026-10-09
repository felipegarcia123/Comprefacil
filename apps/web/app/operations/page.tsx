"use client";

import { useEffect, useState } from "react";
import { Home, Receipt } from "lucide-react";
import { api } from "@/lib/api";
import { DailyOperationsSummary, OperationalCategory, PaymentChannel } from "@/lib/types";
import { errorText, money, time } from "@/lib/format";
import { InlineMessage, Toldo, TouchButton } from "@/components/ui";
import { CategoryGroups, CategoryTotals, groupRows } from "./_components/CategoryGroups";
import { ConsumptionSheet } from "./_components/ConsumptionSheet";
import { ExpenseSheet } from "./_components/ExpenseSheet";
import { ProductClassifierPanel } from "./_components/ProductClassifierPanel";
import { ReconciliationPanel } from "./_components/ReconciliationPanel";

type Modal = "expense" | "consumption" | null;
type Notice = { tone: "success" | "danger"; text: string };

/** Resumen sigue la misma jornada que Caja (resolve_operational_window): mismos números, dos lecturas. */
function windowLabel(summary: DailyOperationsSummary): string {
  const opened = time.format(new Date(summary.date_from));
  if (summary.session_status === "OPEN") return `Jornada actual · abierta desde ${opened}`;
  if (summary.session_status === "CLOSED") return `Última jornada cerrada · abierta desde ${opened}`;
  if (summary.session_status === "NONE") return "Sin jornada registrada todavía";
  return "";
}

export default function OperationsPage() {
  const [summary, setSummary] = useState<DailyOperationsSummary | null>(null);
  const [channels, setChannels] = useState<PaymentChannel[]>([]);
  const [categories, setCategories] = useState<OperationalCategory[]>([]);
  const [modal, setModal] = useState<Modal>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { void load(); }, []);
  async function load() {
    try {
      const [data, paymentChannels, operational] = await Promise.all([api.dailyOperations(), api.paymentChannels(true), api.operationalCategories()]);
      setSummary(data); setChannels(paymentChannels); setCategories(operational);
    } catch (error) { setNotice({ tone: "danger", text: errorText(error) }); }
  }
  async function done(action: () => Promise<unknown>) {
    setBusy(true);
    try { await action(); setModal(null); await load(); setNotice({ tone: "success", text: "Registro guardado correctamente" }); }
    catch (error) { setNotice({ tone: "danger", text: errorText(error) }); throw error; }
    finally { setBusy(false); }
  }

  const { primary, extra } = groupRows(summary, categories);
  const allRows = [...primary, ...extra];
  const cashTotal = allRows.reduce((sum, row) => sum + Number(row.cash), 0);
  const electronicTotal = allRows.reduce((sum, row) => sum + Number(row.electronic), 0);
  const productsTaken = Number(summary?.outgoing.internal_consumption_cost ?? 0) + Number(summary?.outgoing.internal_consumption_estimated ?? 0);

  return (
    <main className="operations purchase" data-module="caja">
      <Toldo module="caja" />
      <div className="purchase__head">
        <div className="purchase__head-title"><h1>Resumen operativo</h1><p className="cash-open-since">{summary ? windowLabel(summary) : "Cuadre por familia de producto y forma de pago"}</p></div>
        <div className="ui-row">
          <TouchButton variant="secondary" module="caja" icon={Receipt} onClick={() => setModal("expense")}>Registrar gasto</TouchButton>
          <TouchButton module="caja" icon={Home} onClick={() => setModal("consumption")}>Casa / consumo interno</TouchButton>
        </div>
      </div>

      <div className="purchase__notice">{notice && <InlineMessage tone={notice.tone} onDismiss={() => setNotice(null)}>{notice.text}</InlineMessage>}</div>

      <div className="operations-body">
        <section className="operations-main" aria-label="Ventas por familia y forma de pago">
          <CategoryGroups primary={primary} extra={extra} />
          <CategoryTotals cash={cashTotal} electronic={electronicTotal} total={Number(summary?.sales_total ?? 0)} />
        </section>
        <aside className="operations-side">
          <ReconciliationPanel refreshKey={summary ? Date.parse(summary.date_to) : 0} />
          {summary?.exchanges && summary.exchanges.exchange_count > 0 && (
            <section className="operations-panel" aria-label="Cambios de producto">
              <header className="operations-panel__head"><h2>Cambios de producto</h2></header>
              <p className="operations-row"><span>Cambios realizados</span><b className="ui-num">{summary.exchanges.exchange_count}{summary.exchanges.same_value_count > 0 && ` (${summary.exchanges.same_value_count} sin diferencia)`}</b></p>
              <Row label="Valor productos devueltos" value={summary.exchanges.returned_credit} />
              <Row label="Valor productos entregados" value={summary.exchanges.replacement_total} />
              <Row label="Diferencias cobradas" value={summary.exchanges.difference_collected} />
              <Row label="· en efectivo" value={summary.exchanges.difference_cash} />
              <Row label="· electrónicas" value={summary.exchanges.difference_electronic} />
              <Row label="Diferencias devueltas" value={summary.exchanges.difference_refunded ?? "0"} />
              <Row label="· en efectivo" value={summary.exchanges.refunded_cash ?? "0"} />
              <Row label="· electrónicas" value={summary.exchanges.refunded_electronic ?? "0"} />
              {Number(summary.exchanges.refunded_electronic_pending ?? 0) > 0 && <Row label="· pendientes por devolver" value={summary.exchanges.refunded_electronic_pending} />}
              <p className="cash-panel__footnote">Un cambio no es venta ni devolución de dinero: solo las diferencias cobradas o devueltas son dinero real.</p>
            </section>
          )}
          {summary?.returns && Number(summary.returns.returns_total) > 0 && (
            <section className="operations-panel" aria-label="Devoluciones">
              <header className="operations-panel__head"><h2>Devoluciones de dinero</h2></header>
              <Row label="Ventas brutas" value={summary.returns.gross_sales} />
              <Row label="Devoluciones" value={summary.returns.returns_total} />
              <Row label="Ventas netas" value={summary.returns.net_sales} />
              <Row label="Reembolsado en efectivo" value={summary.returns.refunds_cash} />
              <Row label="Reembolsado electrónico" value={summary.returns.refunds_electronic} />
              {Number(summary.returns.refunds_electronic_pending) > 0 && <Row label="Pendiente por devolver" value={summary.returns.refunds_electronic_pending} />}
            </section>
          )}
          <section className="operations-panel" aria-label="Gastos">
            <header className="operations-panel__head"><h2>Gastos</h2></header>
            <Row label="Total" value={summary?.outgoing.expenses_total} />
            <Row label="Efectivo" value={summary?.outgoing.expenses_cash} />
            <Row label="Electrónico" value={summary?.outgoing.expenses_electronic} />
          </section>
          <section className="operations-panel" aria-label="Casa y consumo interno">
            <header className="operations-panel__head"><h2>Casa / consumo interno</h2></header>
            <Row label="Productos llevados" value={String(productsTaken)} />
            <Row label="Efectivo retirado" value={summary?.outgoing.home_withdrawals} />
            <p className="operations-note">Casa no se cuenta como venta. Solo incluye retiros de caja registrados con motivo &quot;Casa&quot;.</p>
          </section>
          <ProductClassifierPanel categories={categories} done={load} />
        </aside>
      </div>

      <ExpenseSheet open={modal === "expense"} channels={channels} busy={busy}
        onClose={() => setModal(null)}
        onSubmit={body => done(() => api.createExpense({ ...body, idempotency_key: crypto.randomUUID() }))} />
      <ConsumptionSheet open={modal === "consumption"} busy={busy}
        onClose={() => setModal(null)}
        onSubmit={body => done(() => api.createInternalConsumption({ ...body, idempotency_key: crypto.randomUUID() }))} />
    </main>
  );
}

function Row({ label, value }: { label: string; value?: string }) {
  return <div className="operations-row"><span>{label}</span><b className="ui-num">{money.format(Number(value ?? 0))}</b></div>;
}
