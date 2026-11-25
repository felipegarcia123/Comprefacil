"use client";

import { useEffect, useState } from "react";
import { Sheet, TouchButton } from "@/components/ui";
import { api } from "@/lib/api";
import { errorText, preciseMoney } from "@/lib/format";
import { businessDate, paymentTermsLabel } from "@/lib/smartPurchasing";
import type { PurchaseAgendaItem, PurchaseCommitments } from "@/lib/types";

export function PurchaseAgendaSheet({ open, close, openPurchase }: {
  open: boolean; close: () => void; openPurchase: (id: string) => void;
}) {
  const [agenda, setAgenda] = useState<PurchaseAgendaItem[]>([]);
  const [forecast, setForecast] = useState<PurchaseCommitments | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    if (!open) return;
    let active = true;
    const from = businessDate();
    setLoading(true); setError("");
    Promise.all([api.purchaseAgenda(from, 90), api.purchaseCommitments(from, businessDate(90))])
      .then(([orders, commitments]) => { if (active) { setAgenda(orders); setForecast(commitments); } })
      .catch(cause => { if (active) { setError(errorText(cause)); setAgenda([]); setForecast(null); } })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [open, revision]);

  const overdue = agenda.filter(order => order.alert === "OVERDUE");
  const upcoming = agenda.filter(order => order.alert !== "OVERDUE");
  const amount = (value: string | null | undefined) => preciseMoney.format(Number(value ?? 0));

  function orderList(rows: PurchaseAgendaItem[]) {
    return <ul className="ui-menu-list">{rows.map(order => <li key={order.purchase_id}>
      <button type="button" className="ui-menu-link purchase-history-row" onClick={() => openPurchase(order.purchase_id)}>
        <span><strong>{order.supplier_name}</strong><small>Entrega {order.expected_delivery_date} · {paymentTermsLabel[order.payment_terms]}{order.payment_due_date ? ` · vence ${order.payment_due_date}` : ""}</small></span>
        <span className="purchase-history-row__end"><b>{amount(order.estimated_total)}</b></span>
      </button>
    </li>)}</ul>;
  }

  return <Sheet open={open} title="Agenda y previsión de compras" onClose={close} module="compras" wide>
    <div className="ui-stack">
      <div className="ui-row"><p>Próximas entregas y vencimientos de 90 días, con entregas atrasadas.</p><TouchButton variant="secondary" onClick={() => setRevision(value => value + 1)} disabled={loading}>Actualizar</TouchButton></div>
      {loading && <p role="status">Cargando agenda y compromisos…</p>}
      {error && <p className="ui-field-error" role="alert">{error}</p>}
      {!loading && !error && <>
        <h3>Entregas atrasadas</h3>
        {overdue.length ? orderList(overdue) : <p className="ui-empty-hint">No hay entregas atrasadas.</p>}
        <h3>Próximas entregas</h3>
        {upcoming.length ? orderList(upcoming) : <p className="ui-empty-hint">No hay pedidos programados en los próximos 90 días.</p>}
        {forecast && <>
          <h3>Previsión financiera</h3>
          <p className="purchase-detail-code">Los compromisos estimados son pedidos aún no recibidos. Las obligaciones reales son compras recibidas y sin pagar; ningún importe indica saldo disponible de caja o banco.</p>
          <div className="purchase-finance-summary">
            <div><small>Compromisos estimados del período</small><strong>{amount(forecast.estimated_total)}</strong></div>
            <div><small>Obligaciones reales del período</small><strong>{amount(forecast.actual_outstanding_total)}</strong></div>
            <div><small>Deudas reales vencidas antes del {forecast.overdue_as_of}</small><strong>{amount(forecast.overdue_outstanding_total)}</strong></div>
          </div>
          <div className="purchase-finance-summary">
            <div><small>Vencido: efectivo</small><strong>{amount(forecast.overdue_outstanding_cash)}</strong></div>
            <div><small>Vencido: digital</small><strong>{amount(forecast.overdue_outstanding_digital)}</strong></div>
            <div><small>Vencido: crédito</small><strong>{amount(forecast.overdue_outstanding_credit)}</strong></div>
            <div><small>Vencido: sin modalidad histórica</small><strong>{amount(forecast.overdue_outstanding_unclassified)}</strong></div>
          </div>
          {forecast.days.length ? <div className="ui-table-wrap"><table className="ui-table purchase-forecast-table">
            <caption>Compromisos estimados y obligaciones reales por fecha</caption>
            <thead><tr><th className="ui-th">Fecha</th><th className="ui-th ui-th--num">Efectivo estimado</th><th className="ui-th ui-th--num">Digital estimado</th><th className="ui-th ui-th--num">Crédito estimado</th><th className="ui-th ui-th--num">Crédito real por vencer</th><th className="ui-th ui-th--num">Obligación real total</th></tr></thead>
            <tbody>{forecast.days.map(day => <tr key={day.date} className="ui-tr"><td className="ui-td">{day.date}</td><td className="ui-td ui-th--num">{amount(day.estimated_cash)}</td><td className="ui-td ui-th--num">{amount(day.estimated_digital)}</td><td className="ui-td ui-th--num">{amount(day.estimated_credit)}</td><td className="ui-td ui-th--num">{amount(day.actual_outstanding_credit)}</td><td className="ui-td ui-th--num">{amount(day.actual_outstanding_total)}</td></tr>)}</tbody>
          </table></div> : <p className="ui-empty-hint">Sin compromisos ni vencimientos dentro del período.</p>}
        </>}
      </>}
    </div>
  </Sheet>;
}
