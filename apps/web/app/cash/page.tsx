"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowLeft, ArrowLeftRight, ArrowUpFromLine, Landmark, Lock, Minus, Plus } from "lucide-react";
import { api } from "@/lib/api";
import { errorText, money, time } from "@/lib/format";
import type { CashHistoryRow, CashSummary, WithdrawalReason } from "@/lib/types";
import { Display, InlineMessage, MetricCard, Toldo, TouchButton, TouchTabs } from "@/components/ui";
import { CashActionSheet, type CashAction } from "./_components/CashActionSheet";
import { CashHistoryTable } from "./_components/CashHistoryTable";
import { ChannelList, CashMovements } from "./_components/CashMovements";
import { CloseCashSheet, type AccountBalance, type DeclaredPatrimony } from "./_components/CloseCashSheet";
import { CloseReportSheet, CloseReportView } from "./_components/CloseReportSheet";
import { AccountMovementSheet, AccountMovementsPanel } from "@/components/accounts/AccountMovements";

type Tab = "current" | "history";
type Notice = { tone: "success" | "danger"; text: string };

export default function CashPage() {
  // Discreet electronic-invoicing mode (manual / automatic); never env names, never in Venta.
  const [invoicingMode, setInvoicingMode] = useState("");
  useEffect(() => { api.invoicingMode().then(mode => setInvoicingMode(mode.label)).catch(() => setInvoicingMode("")); }, []);
  const [summary, setSummary] = useState<CashSummary | null>(null);
  const [history, setHistory] = useState<CashHistoryRow[]>([]);
  const [tab, setTab] = useState<Tab>("current");
  const [detail, setDetail] = useState<CashSummary | null>(null);
  const [action, setAction] = useState<CashAction | null>(null);
  const [closing, setClosing] = useState(false);
  const [closedReport, setClosedReport] = useState<string | null>(null);   // session id of the "Cierre de jornada" report
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);
  const [accountMove, setAccountMove] = useState(false);
  const [movesKey, setMovesKey] = useState(0);

  useEffect(() => { void load(); }, []);

  async function load() {
    try {
      const [current, rows] = await Promise.all([api.currentCash(), api.cashHistory()]);
      setSummary(current); setHistory(rows);
      window.dispatchEvent(new Event("operational-session-changed"));
    } catch (error) { setNotice({ tone: "danger", text: errorText(error) }); }
  }

  async function selectHistory(id: string) {
    try { setDetail(await api.cashSession(id)); }
    catch (error) { setNotice({ tone: "danger", text: errorText(error) }); }
  }

  async function submitAction(amount: number, note: string, withdrawalReason?: WithdrawalReason) {
    setBusy(true);
    try {
      if (action === "open") await api.openCash(amount, note);
      if (action === "deposit") await api.cashDeposit(amount, note);
      if (action === "withdrawal") await api.cashWithdrawal(amount, note, withdrawalReason!);
      setAction(null); await load();
      setNotice({ tone: "success", text: "Movimiento registrado correctamente" });
    } finally { setBusy(false); }
  }

  async function submitClose(counted: number, note: string, balances: AccountBalance[], declared: DeclaredPatrimony) {
    if (!summary) return;
    setBusy(true);
    try {
      await api.closeCash(counted, Number(summary.expected_cash), note, balances, declared);
      const closedId = summary.session.id;
      setClosing(false); await load();
      setNotice({ tone: "success", text: "Caja cerrada correctamente" });
      setClosedReport(closedId);
    } finally { setBusy(false); }
  }

  return (
    <main className="cash purchase" data-module="caja">
      <Toldo module="caja" />
      <div className="purchase__head">
        <div className="purchase__head-title">
          <h1>Caja</h1>
          {(summary || invoicingMode) && (
            <p className="cash-open-since">
              {summary && <>Abierta desde {time.format(new Date(summary.session.opened_at))}</>}
              {summary && invoicingMode && " · "}
              {invoicingMode && <Link href="/electronic-invoicing" className="cash-invoicing-mode">{invoicingMode}</Link>}
            </p>
          )}
        </div>
        {summary && tab === "current" && (
          <div className="ui-row">
            <TouchButton variant="secondary" module="caja" icon={Plus} onClick={() => setAction("deposit")}>Ingreso</TouchButton>
            <TouchButton variant="secondary" module="caja" icon={Minus} onClick={() => setAction("withdrawal")}>Retiro</TouchButton>
            <TouchButton variant="secondary" module="caja" icon={ArrowLeftRight} onClick={() => setAccountMove(true)}>Cuentas</TouchButton>
            <TouchButton variant="danger" icon={Lock} onClick={() => setClosing(true)}>Cerrar caja</TouchButton>
          </div>
        )}
      </div>

      <div className="purchase__notice">{notice && <InlineMessage tone={notice.tone} onDismiss={() => setNotice(null)}>{notice.text}</InlineMessage>}</div>

      <TouchTabs ariaLabel="Vista de caja" value={tab} onChange={id => { setTab(id as Tab); setDetail(null); }}
        items={[{ id: "current", label: "Caja actual", module: "caja" }, { id: "history", label: "Historial", module: "caja" }]} />

      <div className="cash-body">
        {tab === "current" ? (
          summary ? <CashDashboardBody summary={summary} onChanged={() => void load()} movesKey={movesKey} /> : <ClosedCash onOpen={() => setAction("open")} />
        ) : detail ? (
          <div className="ui-stack">
            <button className="count-back-link" onClick={() => setDetail(null)}><ArrowLeft size={20} aria-hidden="true" />Historial</button>
            <CashDashboardBody summary={detail} onChanged={() => { void selectHistory(detail.session.id); void load(); }} />
            {detail.session.closed_at && (
              <section className="operations-panel recon" aria-label="Cierre de jornada">
                <header className="operations-panel__head"><h2>Cierre de jornada</h2></header>
                <CloseReportView sessionId={detail.session.id} />
              </section>
            )}
          </div>
        ) : (
          <CashHistoryTable rows={history} onSelect={id => void selectHistory(id)} />
        )}
      </div>

      <CashActionSheet action={action} busy={busy} onClose={() => setAction(null)} onSubmit={submitAction} />
      <CloseCashSheet open={closing} summary={summary} busy={busy} onClose={() => setClosing(false)} onSubmit={submitClose} />
      <CloseReportSheet sessionId={closedReport} onClose={() => setClosedReport(null)} />
      <AccountMovementSheet open={accountMove} onClose={() => setAccountMove(false)}
        onDone={() => { setAccountMove(false); setMovesKey(key => key + 1); setNotice({ tone: "success", text: "Movimiento de cuenta registrado" }); }} />
    </main>
  );
}

function ClosedCash({ onOpen }: { onOpen: () => void }) {
  return (
    <section className="sale-empty" data-module="caja">
      <span className="sale-empty__icon"><Landmark size={44} aria-hidden="true" /></span>
      <h2>Caja cerrada</h2>
      <p>La jornada está cerrada. Abre la caja para comenzar a registrar ventas, compras y movimientos.</p>
      <TouchButton module="caja" size="lg" icon={ArrowUpFromLine} onClick={onOpen}>Abrir caja</TouchButton>
    </section>
  );
}

/** Cash outflow tile: danger tone only when money actually left the drawer. */
const outClass = (value: string | number) => (Number(value) > 0 ? "cash-metric--out" : "cash-metric--neutral cash-metric--zero");

function CashDashboardBody({ summary, onChanged, movesKey }: { summary: CashSummary; onChanged?: () => void; movesKey?: number }) {
  return (
    <div className="ui-stack">
      <Display size="figure" label="Efectivo esperado en cajón" value={money.format(Number(summary.expected_cash))} />
      <div className="ui-metric-grid cash-metrics">
        {/* Caja's module "deep" is a dark rose-red — perfect for the toldo/badges, but it would make
           every tile read as a warning. `--mod-deep` is inherited from <main data-module="caja">,
           so omitting `module` here isn't enough — `.cash-metric--neutral` re-declares it back to
           ink for these three; only the cash OUTFLOWS below opt into red via `.cash-metric--out`. */}
        <MetricCard className="cash-metric--neutral" label="Apertura" value={money.format(Number(summary.session.opening_amount))} />
        <MetricCard className="cash-metric--neutral" label="Ventas efectivo" value={money.format(Number(summary.cash_sales))} />
        <MetricCard className="cash-metric--neutral" label="Ingresos" value={money.format(Number(summary.deposits))} />
        {/* Red only when money actually left the drawer: a $0 outflow is neutral, not an alert. */}
        <MetricCard className={outClass(summary.withdrawals)} label="Retiros" value={money.format(Number(summary.withdrawals))} />
        <MetricCard className={outClass(summary.expenses)} label="Gastos" value={money.format(Number(summary.expenses))} />
        <MetricCard className={outClass(summary.supplier_payments_cash)} label="Pagos proveedores" value={money.format(Number(summary.supplier_payments_cash))} />
        {Number(summary.refunds) > 0 && <MetricCard className="cash-metric--out" label="Reembolsos" value={money.format(Number(summary.refunds))} />}
        {Number(summary.exchanges_cash ?? 0) > 0 && <MetricCard className="cash-metric--neutral" label="Diferencias cobradas en cambios" value={money.format(Number(summary.exchanges_cash))} />}
        {Number(summary.exchange_refunds_cash ?? 0) > 0 && <MetricCard className="cash-metric--out" label="Diferencias devueltas en cambios" value={money.format(Number(summary.exchange_refunds_cash))} />}
        {Number(summary.payment_corrections_cash ?? 0) !== 0 && <MetricCard className="cash-metric--neutral" label="Correcciones de pago" value={`${Number(summary.payment_corrections_cash) > 0 ? "+" : "−"}${money.format(Math.abs(Number(summary.payment_corrections_cash)))}`} />}
      </div>

      <div className="cash-columns">
        <section className="cash-panel" aria-label="Total vendido, todos los canales">
          <header className="cash-panel__head"><div><h2>Total vendido · todos los canales</h2><p>{summary.transaction_count} venta{summary.transaction_count === 1 ? "" : "s"}</p></div><b className="ui-num cash-panel__total cash-panel__total--neutral">{money.format(Number(summary.total_sales))}</b></header>
          <ChannelList rows={summary.payment_channels} empty="Todavía no hay ventas en esta sesión." />
          <p className="cash-panel__footnote">Los canales digitales no forman parte del efectivo esperado.</p>
        </section>
        <section className="cash-panel" aria-label="Pagos a proveedores">
          <header className="cash-panel__head"><div><h2>Pagos a proveedores</h2></div><b className="ui-num cash-panel__total">{money.format(Number(summary.supplier_payments_total))}</b></header>
          <ChannelList rows={summary.supplier_payment_channels} empty="Todavía no hay pagos a proveedores." />
        </section>
      </div>

      <CashMovements summary={summary} onChanged={onChanged} />
      <section className="cash-panel" aria-label="Movimientos de cuentas">
        <header className="cash-panel__head"><div><h2>Movimientos de cuentas</h2><p>Nequi, Davivienda, Nu y DaviPlata · no afectan el efectivo del cajón</p></div></header>
        <AccountMovementsPanel sessionId={summary.session.id} refreshKey={movesKey} />
      </section>
    </div>
  );
}
