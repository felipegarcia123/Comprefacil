"use client";

import { useEffect, useState } from "react";
import { ConfirmDialog, DataTable, InlineMessage, StatusBadge, Td, Th, TouchButton, Tr } from "@/components/ui";
import { BalanceTrend } from "@/components/report-charts";
import { PatrimonyLegend, RegisteredValuationBlock, variationText } from "@/components/accounts/PatrimonyBlock";
import { api } from "@/lib/api";
import { dateMedium, dateTime, errorText, money } from "@/lib/format";
import { useBreakpoint } from "@/lib/useBreakpoint";
import type { PatrimonyHistory } from "@/lib/types";

const newKey = () => (typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `k-${Date.now()}-${Math.random()}`);

/**
 * PATRIMONIO. Two series, never mixed: the VALUE DECLARED BY THE OWNER (official source by default) with its own
 * variation between consecutive declarations, and the VALUE CALCULATED BY THE POS (inventory registered, "No
 * verificada" until a validated physical inventory and an explicit, audited change of the official source).
 */
export function PatrimonyTab() {
  const [data, setData] = useState<PatrimonyHistory | null>(null);
  const [error, setError] = useState("");
  const [switching, setSwitching] = useState(false);
  const [reason, setReason] = useState("");
  const [key, setKey] = useState(newKey);
  const [busy, setBusy] = useState(false);
  const compact = useBreakpoint() === "movil";
  const load = () => api.patrimonyHistory(60).then(setData).catch(cause => setError(errorText(cause)));
  useEffect(() => { void load(); }, []);

  if (error && !data) return <InlineMessage tone="danger">{error}</InlineMessage>;
  if (!data) return <p className="chart-empty">Cargando…</p>;
  const manual = data.official_source.source === "MANUAL";
  const declared = data.sessions.filter(row => row.declared);
  const latest = declared[0]?.declared ?? null;
  const latestAuto = data.sessions.find(row => row.automatic.status === "SNAPSHOT")?.automatic;
  const trend = [...declared].reverse().map(row => ({ label: dateMedium.format(new Date(row.closed_at)), value: Number(row.declared!.declared_value) }));
  const target = manual ? "AUTOMATIC" : "MANUAL";

  async function changeSource() {
    setBusy(true); setError("");
    try { await api.setPatrimonySource({ source: target, reason: reason.trim(), idempotency_key: key }); setSwitching(false); setReason(""); setKey(newKey()); await load(); }
    catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  }

  return (
    <div className="ui-stack">
      <section className="cash-panel" aria-label="Patrimonio declarado">
        <header className="cash-panel__head"><div><h2>Patrimonio de mercancía</h2>
          <p>Fuente oficial: {data.official_source.label}{data.official_source.since ? ` · desde ${dateTime.format(new Date(data.official_source.since))}` : ""}</p></div>
          <StatusBadge tone={manual ? "info" : "success"}>{manual ? "Manual" : "Automática"}</StatusBadge></header>
        <div className="patrimony">
          <PatrimonyLegend />
          {latest ? (
            <dl className="account-card__rows">
              <div><dt>Último valor declarado</dt><dd className="ui-num">{money.format(Number(latest.declared_value))}</dd></div>
              <div><dt>Cierre</dt><dd>{dateMedium.format(new Date(latest.closed_at!))}</dd></div>
              <div><dt>Variación vs declaración anterior</dt><dd className="ui-num">{variationText(latest) ?? "Primera declaración"}</dd></div>
            </dl>
          ) : <InlineMessage tone="info">Todavía no hay valores declarados. El dueño puede registrarlo, si quiere, al cerrar la jornada.</InlineMessage>}
        </div>
      </section>

      {trend.length > 1 && (
        <section className="cash-panel" aria-label="Evolución del valor declarado">
          <header className="cash-panel__head"><div><h2>Evolución del valor declarado</h2><p>Solo jornadas con valor declarado (los días sin registro no se inventan)</p></div></header>
          <div className="patrimony-chart"><BalanceTrend data={trend} label="Valor declarado" /></div>
        </section>
      )}

      <DataTable caption="Patrimonio por jornada" className="reports-cart patrimony-history" module="informes" density="dense" stack={compact}>
        <thead><tr><Th>Cierre</Th><Th numeric>Declarado por el dueño</Th><Th numeric>Variación</Th>{!compact && <Th numeric>Calculado por el POS</Th>}</tr></thead>
        <tbody>
          {data.sessions.map(row => (
            <Tr key={row.cash_session_id}>
              <Td label="Cierre">{dateMedium.format(new Date(row.closed_at))}</Td>
              <Td numeric label="Declarado por el dueño" className="ui-num">{row.declared ? money.format(Number(row.declared.declared_value)) : <span className="cash-history__muted">Sin declarar</span>}</Td>
              <Td numeric label="Variación" className="ui-num">{row.declared ? (variationText(row.declared) ?? "Primera") : "—"}</Td>
              {!compact && <Td numeric label="Calculado por el POS" className="ui-num">
                {row.automatic.status === "SNAPSHOT"
                  ? <>{money.format(Number(row.automatic.inventory_value))} <small className="cash-history__accounts">{row.automatic.is_official ? "Oficial" : "No verificado"}</small></>
                  : <span className="cash-history__muted">—</span>}</Td>}
            </Tr>
          ))}
        </tbody>
      </DataTable>

      <section className="cash-panel" aria-label="Valor calculado por el POS">
        <header className="cash-panel__head"><div><h2>Valor calculado por el POS</h2><p>Diagnóstico del inventario registrado · no se suma ni se compara con el valor declarado</p></div></header>
        <div className="patrimony">
          {latestAuto ? <RegisteredValuationBlock data={latestAuto} /> : <p className="patrimony__coverage">Todavía no hay cierres con valoración registrada.</p>}
          <p className="patrimony__coverage">
            {manual
              ? `La valoración automática solo puede ser la fuente oficial después de un inventario físico confirmado en el POS (conteos confirmados: ${data.confirmed_physical_counts}).`
              : "La valoración automática es la fuente oficial. Las fotos anteriores al cambio siguen como diagnóstico y no se comparan con las nuevas."}
          </p>
          {data.source_changes.map(change => <p key={change.id} className="account-row__note">{dateTime.format(new Date(change.created_at))}: fuente oficial → {change.label}. Motivo: {change.reason}</p>)}
          <div className="account-row__actions">
            <TouchButton variant="secondary" module="informes" disabled={manual && data.confirmed_physical_counts === 0} onClick={() => { setError(""); setSwitching(true); }}>
              {manual ? "Usar la valoración automática como fuente oficial" : "Volver al valor declarado como fuente oficial"}</TouchButton>
          </div>
          {error && <p className="ui-field-error" role="alert">{error}</p>}
        </div>
      </section>

      <ConfirmDialog open={switching} module="informes" title="Cambiar la fuente oficial del patrimonio" confirmLabel="Cambiar fuente oficial" busy={busy}
        onCancel={() => setSwitching(false)} onConfirm={() => void changeSource()}>
        <p>{target === "AUTOMATIC"
          ? "Desde ahora el valor calculado por el POS será el patrimonio oficial. Hazlo solo con un inventario físico inicial validado."
          : "Desde ahora el valor declarado por el dueño vuelve a ser el patrimonio oficial."} El cambio queda registrado con fecha y motivo; no se comparan valores de bases distintas.</p>
        <label className="ui-field"><span>Motivo * (al menos 10 caracteres)</span><textarea rows={2} value={reason} onChange={event => setReason(event.target.value)} /></label>
        {error && <p className="ui-field-error" role="alert">{error}</p>}
      </ConfirmDialog>
    </div>
  );
}
