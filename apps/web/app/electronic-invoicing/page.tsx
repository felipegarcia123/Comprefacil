"use client";

import { useCallback, useEffect, useState } from "react";
import { FileCheck2, LoaderCircle } from "lucide-react";
import { api } from "@/lib/api";
import { dateTimeShort, errorText, money } from "@/lib/format";
import { INVOICE_STATE, STATE_ORDER } from "@/lib/invoiceStates";
import { useBreakpoint } from "@/lib/useBreakpoint";
import type { InvoiceDisplayState, InvoicePage, InvoicingMode, InvoicingScope, InvoicingSummary } from "@/lib/types";
import { DataTable, InlineMessage, Pagination, Sheet, StatusBadge, Td, Th, Toldo, TouchTabs, Tr } from "@/components/ui";
import { InvoiceDetailView } from "./_components/InvoiceDetailView";
import { ReadinessView } from "./_components/ReadinessView";

type Tab = "invoices" | "readiness";
/** The four figures the operation acts on; each one opens its list. */
const SUMMARY: { state: InvoiceDisplayState; label: string }[] = [
  { state: "ACCEPTED", label: "Aceptadas DIAN" },
  { state: "PENDING", label: "Pendientes" },
  { state: "BLOCKED", label: "Bloqueadas" },
  { state: "ERROR", label: "Errores" },
];

/**
 * Facturación electrónica (menú "Más"). Operational control, not a fiscal ERP: see every invoice's real state,
 * why it is blocked, open its PDF/XML, and check whether the system is ready for the cutover. Never issues.
 */
export default function ElectronicInvoicingPage() {
  const breakpoint = useBreakpoint();
  const wide = breakpoint === "pos";
  const compact = breakpoint === "movil";
  const [tab, setTab] = useState<Tab>("invoices");
  const [mode, setMode] = useState<InvoicingMode | null>(null);
  const [scope, setScope] = useState<InvoicingScope>("session");
  const [day, setDay] = useState("");
  const [state, setState] = useState<InvoiceDisplayState | "">("");
  const [saleNumber, setSaleNumber] = useState("");
  const [customer, setCustomer] = useState("");
  const [page, setPage] = useState(1);
  const [summary, setSummary] = useState<InvoicingSummary | null>(null);
  const [list, setList] = useState<InvoicePage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => { api.invoicingMode().then(setMode).catch(() => setMode(null)); }, []);

  const dayReady = scope !== "day" || Boolean(day);
  const load = useCallback(async () => {
    if (!dayReady) return;
    setLoading(true); setError("");
    try {
      const [nextSummary, nextList] = await Promise.all([
        api.invoicingSummary(scope, day || undefined),
        api.invoicingList({ state, scope, day: day || undefined, sale_number: saleNumber.trim(), customer: customer.trim(), page }),
      ]);
      setSummary(nextSummary); setList(nextList);
    } catch (cause) { setError(errorText(cause)); }
    finally { setLoading(false); }
  }, [scope, day, state, saleNumber, customer, page, dayReady]);
  useEffect(() => { const timer = window.setTimeout(() => void load(), 250); return () => window.clearTimeout(timer); }, [load]);

  const pickState = (next: InvoiceDisplayState | "") => { setState(current => (current === next ? "" : next)); setPage(1); };
  const detail = selectedId && <InvoiceDetailView id={selectedId} automatic={Boolean(mode?.automatic)} onChanged={() => void load()} />;
  const scopeLabel = scope === "session" ? "de la jornada" : scope === "day" ? "del día" : "en total";

  return (
    <main className="sale einv" data-module="informes">
      <Toldo module="informes" />
      <div className="purchase__head">
        <div className="purchase__head-title">
          <h1>Facturación electrónica</h1>
          {mode && <p className="cash-open-since">{mode.label}</p>}
        </div>
        {mode && <StatusBadge tone={mode.automatic ? "success" : "neutral"}>{mode.automatic ? "Automática" : "Manual"}</StatusBadge>}
      </div>
      <div className="einv__tabs">
        <TouchTabs ariaLabel="Secciones" value={tab} onChange={value => setTab(value as Tab)}
          items={[{ id: "invoices", label: "Facturas", module: "informes" }, { id: "readiness", label: "Preparación", module: "informes" }]} />
      </div>

      {tab === "readiness" ? <ReadinessView /> : (
        <div className="sale__body">
          <section className="sale__main einv__main" aria-label="Facturas electrónicas">
            <div className="einv__summary" role="group" aria-label={`Resumen ${scopeLabel}`}>
              {SUMMARY.map(item => (
                <button key={item.state} type="button" className="einv__figure" aria-pressed={state === item.state}
                  data-tone={INVOICE_STATE[item.state].tone} onClick={() => pickState(item.state)}>
                  <span className="einv__figure-value ui-num">{summary?.counts[item.state] ?? "—"}</span>
                  <span className="einv__figure-label">{item.label}</span>
                </button>
              ))}
            </div>
            {summary && (
              <p className="einv__note">
                {summary.completed_sales} ventas {scopeLabel}
                {summary.sales_without_invoice > 0 && <> · {summary.sales_without_invoice} sin factura electrónica</>}
                {summary.counts.DRAFT > 0 && <> · <button type="button" className="einv__link" onClick={() => pickState("DRAFT")}>{summary.counts.DRAFT} borrador(es) en Alegra</button></>}
                {summary.counts.HELD > 0 && <> · <button type="button" className="einv__link" onClick={() => pickState("HELD")}>{summary.counts.HELD} fuera de emisión</button></>}
              </p>
            )}

            <div className="einv__filters">
              <label className="ui-field"><span>Estado</span>
                <select className="inventory-select" value={state} onChange={event => { setState(event.target.value as InvoiceDisplayState | ""); setPage(1); }}>
                  <option value="">Todos</option>
                  {STATE_ORDER.map(value => <option key={value} value={value}>{INVOICE_STATE[value].label}</option>)}
                </select>
              </label>
              <label className="ui-field"><span>Periodo</span>
                <select className="inventory-select" value={scope} onChange={event => { setScope(event.target.value as InvoicingScope); setPage(1); }}>
                  <option value="session">Jornada actual</option>
                  <option value="day">Un día</option>
                  <option value="all">Todas</option>
                </select>
              </label>
              {scope === "day" && <label className="ui-field"><span>Fecha</span><input type="date" value={day} onChange={event => { setDay(event.target.value); setPage(1); }} /></label>}
              <label className="ui-field"><span>Venta</span><input value={saleNumber} placeholder="V-…" autoComplete="off" onChange={event => { setSaleNumber(event.target.value); setPage(1); }} /></label>
              <label className="ui-field"><span>Cliente</span><input value={customer} placeholder="Nombre o documento" autoComplete="off" onChange={event => { setCustomer(event.target.value); setPage(1); }} /></label>
            </div>

            {error && <InlineMessage tone="danger" onDismiss={() => setError("")}>{error}</InlineMessage>}
            <div className="sale__cart">
              {loading && !list ? (
                <div className="sale-status" role="status"><LoaderCircle className="ui-btn__spin" size={28} aria-hidden="true" />Cargando…</div>
              ) : list && list.items.length ? (
                <DataTable caption="Facturas electrónicas" className="einv-cart" module="informes" stack={compact} density="dense">
                  <thead><tr><Th>Venta</Th><Th>Fecha</Th>{!compact && <Th>Cliente</Th>}<Th numeric>Total</Th><Th>Estado</Th><Th>Número</Th>{wide && <Th>Motivo</Th>}</tr></thead>
                  <tbody>
                    {list.items.map(row => (
                      <Tr key={row.id} interactive selected={row.id === selectedId} onClick={() => setSelectedId(row.id)}>
                        <Td label="Venta"><strong className="einv__sale">{row.sale_number}</strong></Td>
                        <Td label="Fecha" muted>{dateTimeShort.format(new Date(row.completed_at))}</Td>
                        {!compact && <Td label="Cliente">{row.customer}</Td>}
                        <Td label="Total" numeric className="ui-num">{money.format(Number(row.total))}</Td>
                        <Td label="Estado"><StatusBadge tone={INVOICE_STATE[row.display_status].tone}>{INVOICE_STATE[row.display_status].label}</StatusBadge></Td>
                        <Td label="Número" muted>{row.provider_number ?? "—"}</Td>
                        {wide && <Td label="Motivo" muted><span className="einv__reason" title={row.reason ?? undefined}>{row.reason ?? ""}</span></Td>}
                      </Tr>
                    ))}
                  </tbody>
                </DataTable>
              ) : (
                <div className="sale-empty" data-module="informes">
                  <span className="sale-empty__icon"><FileCheck2 size={44} aria-hidden="true" /></span>
                  <h2>Sin facturas {scopeLabel}</h2>
                  <p>{mode && !mode.automatic ? "La facturación automática está desactivada: las ventas se facturan por el procedimiento actual." : "Cambia el periodo o los filtros."}</p>
                </div>
              )}
            </div>
            {list && list.total_pages > 1 && <Pagination page={list.page} totalPages={list.total_pages} total={list.total} itemLabel="facturas" module="informes" onChange={setPage} />}
          </section>
          {wide && (
            <section className="sale__panel" aria-label="Detalle de la factura">
              {detail || <div className="sale-status customers__placeholder">Selecciona una factura para ver su detalle.</div>}
            </section>
          )}
        </div>
      )}
      <Sheet open={!wide && Boolean(selectedId)} title="Factura" module="informes" onClose={() => setSelectedId(null)}>{!wide && detail}</Sheet>
    </main>
  );
}
