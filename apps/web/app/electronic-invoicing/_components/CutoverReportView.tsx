"use client";

import { useEffect, useState } from "react";
import { CircleAlert, CircleCheck, LoaderCircle } from "lucide-react";
import { api } from "@/lib/api";
import { dateMedium, errorText, money, percent } from "@/lib/format";
import type { CutoverReport, SalesCoverage } from "@/lib/types";
import { DataTable, InlineMessage, MetricCard, StatusBadge, Td, Th, Tr } from "@/components/ui";

/** Below this, percentages describe a handful of tickets, not the business: said explicitly, never hidden. */
const MIN_LINES_FOR_DECISION = 300;
const MIN_SESSIONS_FOR_DECISION = 5;

const pct = (value: number | null) => (value === null ? "—" : percent(value, 2));

function Fact({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  const Icon = ok ? CircleCheck : CircleAlert;
  return <li data-ok={ok} data-blocking="true"><Icon size={22} aria-hidden="true" /><span>{children}</span></li>;
}

/**
 * Factual cutover report: five areas, no score. Coverage is measured on REAL sales (after the catalog load,
 * without demo products), by lines and by money — never by catalog size alone.
 */
export function CutoverReportView() {
  const [report, setReport] = useState<CutoverReport | null>(null);
  const [coverage, setCoverage] = useState<SalesCoverage | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api.cutoverReport(), api.salesCoverage()])
      .then(([nextReport, nextCoverage]) => { setReport(nextReport); setCoverage(nextCoverage); })
      .catch(cause => setError(errorText(cause)));
  }, []);

  if (error) return <InlineMessage tone="danger">{error}</InlineMessage>;
  if (!report || !coverage) return <p className="customer-picker__hint" role="status"><LoaderCircle className="ui-btn__spin" size={20} aria-hidden="true" />Calculando cobertura…</p>;

  const thin = Number(coverage.lines.total) < MIN_LINES_FOR_DECISION || coverage.by_session.length < MIN_SESSIONS_FOR_DECISION;
  return (
    <section className="einv-card" aria-labelledby="einv-report-title">
      <div className="einv-card__head">
        <h2 id="einv-report-title">Informe para decidir el cambio</h2>
        <StatusBadge tone="neutral">Solo hechos, sin puntaje</StatusBadge>
      </div>

      <h3 className="einv-detail__h3">Técnico</h3>
      <ul className="einv-checks">
        <Fact ok={report.technical.pass}>{report.technical.pass ? "Integración, numeración, clientes, medios de pago e ítems genéricos listos" : report.technical.reasons.join(" · ")}</Fact>
      </ul>

      <h3 className="einv-detail__h3">Cobertura de ventas reales</h3>
      <p className="einv-card__hint">
        Ventas desde {coverage.since ? dateMedium.format(new Date(`${coverage.since}T12:00:00`)) : "—"} (carga del catálogo actual).
        Excluidas: {coverage.excluded.before_catalog} anteriores al catálogo y {coverage.excluded.inactive_product} con productos demo inactivos.
      </p>
      {thin && (
        <InlineMessage tone="warning" title="Historial insuficiente para decidir">
          Hay {coverage.sales} ventas reales ({String(coverage.lines.total)} líneas) en {coverage.by_session.length} jornada(s).
          Los porcentajes todavía no representan la operación: conviene acumular más jornadas con el catálogo actual.
        </InlineMessage>
      )}
      <div className="einv-metrics">
        <MetricCard module="informes" label="Líneas facturables" value={pct(coverage.lines.pct)} caption={`${Number(coverage.lines.mapped) + Number(coverage.lines.manual_covered)} de ${coverage.lines.total}`} />
        <MetricCard module="informes" label="Valor facturable" value={pct(coverage.value.pct)} caption={`de ${money.format(Number(coverage.value.total))}`} />
        <MetricCard label="Ventas completas facturables" value={pct(coverage.sales_invoiceable_pct)} caption={`${coverage.sales_fully_invoiceable} de ${coverage.sales}`} />
        <MetricCard label="Productos vendidos vinculados" value={pct(coverage.products.pct)} caption={`${coverage.products.distinct_sold_mapped} de ${coverage.products.distinct_sold}`} />
      </div>
      {coverage.by_session.length > 0 && (
        <DataTable caption="Cobertura por jornada" className="einv-cart" module="informes" density="dense">
          <thead><tr><Th>Jornada</Th><Th numeric>Ventas</Th><Th numeric>Líneas</Th><Th numeric>% líneas</Th><Th numeric>% valor</Th></tr></thead>
          <tbody>
            {coverage.by_session.map(row => (
              <Tr key={row.cash_session_id}>
                <Td label="Jornada">{dateMedium.format(new Date(row.opened_at))}</Td>
                <Td label="Ventas" numeric className="ui-num">{row.sales}</Td>
                <Td label="Líneas" numeric className="ui-num">{row.lines}</Td>
                <Td label="% líneas" numeric className="ui-num">{pct(row.lines_pct)}</Td>
                <Td label="% valor" numeric className="ui-num">{pct(row.value_pct)}</Td>
              </Tr>
            ))}
          </tbody>
        </DataTable>
      )}
      {coverage.top_unmapped.length > 0 ? (
        <>
          <h3 className="einv-detail__h3">Productos vendidos que bloquean (por frecuencia)</h3>
          <ul className="einv-groups">
            {coverage.top_unmapped.slice(0, 10).map(row => <li key={row.product_id}><span>{row.name}</span><b className="ui-num">{row.times_sold} ventas · {money.format(Number(row.revenue))}</b></li>)}
          </ul>
          <p className="einv-card__hint">
            Para llegar a {coverage.pareto.map(p => `${p.target_pct} %: ${p.reachable ? `${p.products_needed} producto(s)` : "no alcanzable solo vinculando"}`).join(" · ")}
          </p>
        </>
      ) : <p className="einv-card__hint">Ningún producto sin vincular aparece en las ventas reales registradas.</p>}

      <h3 className="einv-detail__h3">Medios de pago</h3>
      <ul className="einv-checks">
        {report.payments.cases.map(item => <Fact key={item.case} ok={item.ready}>{item.case}{item.note ? <small>{item.note}</small> : null}</Fact>)}
      </ul>

      <h3 className="einv-detail__h3">Ventas manuales</h3>
      <ul className="einv-checks">
        <Fact ok={report.manual.granel.ready}>Granel {report.manual.granel.ready ? "listo" : "sin ítem configurado"}</Fact>
        <Fact ok={report.manual.bulto.ready}>Bulto {report.manual.bulto.ready ? "listo" : "sin ítem configurado"}</Fact>
      </ul>

      <h3 className="einv-detail__h3">Operación</h3>
      <ul className="einv-checks">
        <Fact ok={report.operational.cash === "CLOSED"}>Caja {report.operational.cash === "OPEN" ? "abierta" : "cerrada"}{report.operational.note ? <small>{report.operational.note}</small> : null}</Fact>
      </ul>
    </section>
  );
}
