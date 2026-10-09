"use client";

import { useEffect, useState } from "react";
import { DataTable, InlineMessage, Td, Th, Tr } from "@/components/ui";
import { BalanceTrend } from "@/components/report-charts";
import { AccountDifference } from "@/components/accounts/AccountsReconciliation";
import { api } from "@/lib/api";
import { cx } from "@/lib/cx";
import { dateMedium, errorText, money } from "@/lib/format";
import { useBreakpoint } from "@/lib/useBreakpoint";
import type { AccountsHistory } from "@/lib/types";

const pesos = (value?: string | null) => (value == null ? "—" : money.format(Number(value)));

/**
 * CUENTAS: lo que el dueño vio en Nequi / Davivienda / Nu / DaviPlata al cerrar vs lo esperado. Son saldos, no utilidad:
 * nunca se suman con el efectivo ni entre sí para llamarlo ganancia.
 */
export function AccountsTab() {
  const [data, setData] = useState<AccountsHistory | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState("");
  const compact = useBreakpoint() === "movil";
  useEffect(() => {
    api.accountsHistory(60).then(next => { setData(next); setSelected(next.accounts[0]?.id ?? ""); }).catch(cause => setError(errorText(cause)));
  }, []);

  if (error) return <InlineMessage tone="danger">{error}</InlineMessage>;
  if (!data) return <p className="chart-empty">Cargando…</p>;
  const account = data.accounts.find(item => item.id === selected);
  const rows = data.sessions.map(session => ({ session, cell: session.accounts.find(cell => cell.account_id === selected) }));
  const trend = rows.filter(row => row.cell?.status === "CAPTURED").reverse()
    .map(row => ({ label: dateMedium.format(new Date(row.session.closed_at)), value: Number(row.cell!.observed_balance) }));

  return (
    <div className="ui-stack">
      <div className="accounts-cards">
        {data.accounts.map(item => {
          const last = data.latest[item.id];
          return (
            <section key={item.id} className="cash-panel" aria-label={item.name}>
              <header className="cash-panel__head"><div><h2>{item.name}</h2><p>{last ? "Último cierre registrado" : "Sin saldos registrados todavía"}</p></div>
                {last && <AccountDifference difference={last.difference == null ? null : Number(last.difference)} baseline={last.is_baseline} />}</header>
              {last && (
                <dl className="account-card__rows">
                  <div><dt>Saldo observado</dt><dd className="ui-num">{pesos(last.observed_balance)}</dd></div>
                  <div><dt>Saldo esperado</dt><dd className="ui-num">{last.is_baseline ? "Primer saldo" : pesos(last.expected_balance)}</dd></div>
                  <div><dt>Ingresos POS del período</dt><dd className="ui-num">{pesos(last.pos_inflows)}</dd></div>
                  {Number(last.pos_outflows ?? 0) > 0 && <div><dt>Devoluciones POS</dt><dd className="ui-num">−{pesos(last.pos_outflows)}</dd></div>}
                  <div><dt>Ingresos externos</dt><dd className="ui-num">+{pesos(last.registered_external_inflows)}</dd></div>
                  <div><dt>Salidas externas</dt><dd className="ui-num">−{pesos(last.registered_external_outflows)}</dd></div>
                </dl>
              )}
            </section>
          );
        })}
      </div>

      <div className="ui-choice" role="radiogroup" aria-label="Cuenta" style={{ gridTemplateColumns: `repeat(${data.accounts.length || 1}, minmax(0, 1fr))` }}>
        {data.accounts.map(item => (
          <button key={item.id} type="button" role="radio" aria-checked={selected === item.id}
            className={cx("ui-choice__item", selected === item.id && "ui-choice__item--on")} onClick={() => setSelected(item.id)}>{item.name}</button>
        ))}
      </div>
      {trend.length > 1 && (
        <section className="cash-panel" aria-label={`Histórico de ${account?.name}`}>
          <header className="cash-panel__head"><div><h2>Histórico de saldos · {account?.name}</h2><p>Saldo observado en cada cierre</p></div></header>
          <div className="patrimony-chart"><BalanceTrend data={trend} label="Saldo" /></div>
        </section>
      )}
      <DataTable caption={`Saldos de ${account?.name ?? "la cuenta"} por jornada`} className="reports-cart accounts-history" module="informes" density="dense" stack={compact}>
        <thead><tr><Th>Cierre</Th><Th numeric>Saldo observado</Th>{!compact && <Th numeric>Esperado</Th>}<Th numeric>Diferencia</Th>{!compact && <Th numeric>Ingresos POS</Th>}</tr></thead>
        <tbody>
          {rows.map(({ session, cell }) => (
            <Tr key={session.cash_session_id}>
              <Td label="Cierre">{dateMedium.format(new Date(session.closed_at))}</Td>
              <Td numeric label="Saldo observado" className="ui-num">{cell?.status === "CAPTURED" ? pesos(cell.observed_balance) : <span className="cash-history__muted">Sin saldo registrado</span>}</Td>
              {!compact && <Td numeric label="Esperado" className="ui-num">{cell?.status === "CAPTURED" ? (cell.is_baseline ? "Primer saldo" : pesos(cell.expected_balance)) : "—"}</Td>}
              <Td numeric label="Diferencia">{cell?.status === "CAPTURED" ? <AccountDifference difference={cell.difference == null ? null : Number(cell.difference)} baseline={cell.is_baseline} /> : "—"}</Td>
              {!compact && <Td numeric label="Ingresos POS" className="ui-num">{cell?.status === "CAPTURED" ? pesos(cell.pos_inflows) : "—"}</Td>}
            </Tr>
          ))}
        </tbody>
      </DataTable>
      <p className="account-row__note">Los saldos de las cuentas son patrimonio en cuentas, no ganancia: no se suman con el efectivo.</p>
    </div>
  );
}
