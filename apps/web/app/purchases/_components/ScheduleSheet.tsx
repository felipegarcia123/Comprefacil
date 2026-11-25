"use client";

import { useState } from "react";
import { ConfirmDialog, Sheet, TouchButton } from "@/components/ui";
import { errorText, preciseMoney } from "@/lib/format";
import { businessDate, paymentTermsLabel, scheduleErrors } from "@/lib/smartPurchasing";
import type { Purchase, PurchasePaymentTerms, SchedulePurchaseIn } from "@/lib/types";

type OfferFields = { list: string; discount: string; note: string };

export function ScheduleSheet({ purchase, busy, close, confirm }: {
  purchase: Purchase; busy: boolean; close: () => void;
  confirm: (body: SchedulePurchaseIn) => Promise<void>;
}) {
  const [delivery, setDelivery] = useState(businessDate(1));
  const [terms, setTerms] = useState<PurchasePaymentTerms>("CASH_ON_DELIVERY");
  const [due, setDue] = useState("");
  const [notes, setNotes] = useState("");
  const [offers, setOffers] = useState<Record<string, OfferFields>>({});
  const [review, setReview] = useState(false);
  const [error, setError] = useState("");

  const body: SchedulePurchaseIn = {
    expected_delivery_date: delivery, payment_terms: terms,
    payment_due_date: terms === "CREDIT" ? due || null : null,
    offer_notes: notes.trim() || null,
    offers: purchase.items.flatMap(item => {
      const fields = offers[item.product_id];
      if (!fields || !(fields.list || fields.discount || fields.note.trim())) return [];
      return [{ product_id: item.product_id, list_unit_cost: fields.list || null,
                discount_per_unit: fields.discount || null, note: fields.note.trim() || null }];
    }),
  };

  function updateOffer(productId: string, patch: Partial<OfferFields>) {
    setOffers(current => ({ ...current, [productId]: {
      list: current[productId]?.list ?? "", discount: current[productId]?.discount ?? "",
      note: current[productId]?.note ?? "", ...patch,
    } }));
  }

  async function submit() {
    setError("");
    try { await confirm(body); }
    catch (cause) { setError(errorText(cause)); setReview(false); }
  }

  return (
    <Sheet open title="Programar pedido" persistent onClose={close} module="compras" wide>
      <div className="ui-stack">
        <p>Proveedor: <strong>{purchase.supplier_name}</strong></p>
        <p className="purchase-detail-code">Las cantidades, costes y ofertas quedarán guardados como el pedido original. Programar no mueve inventario ni caja.</p>
        <div className="purchase-schedule-grid">
          <label className="ui-field"><span>Entrega prevista</span><input type="date" value={delivery} onChange={event => setDelivery(event.target.value)} /></label>
          <label className="ui-field"><span>Condición de pago</span><select value={terms} onChange={event => setTerms(event.target.value as PurchasePaymentTerms)}>
            {(Object.keys(paymentTermsLabel) as PurchasePaymentTerms[]).map(term => <option key={term} value={term}>{paymentTermsLabel[term]}</option>)}
          </select></label>
          {terms === "CREDIT" && <label className="ui-field"><span>Vencimiento pactado</span><input type="date" value={due} onChange={event => setDue(event.target.value)} /></label>}
        </div>
        <h3>Productos y ofertas</h3>
        {purchase.items.map(item => {
          const offer = offers[item.product_id] ?? { list: "", discount: "", note: "" };
          return <div key={item.product_id} className="purchase-schedule-line">
            <strong>{item.product_name}</strong>
            <span>{Number(item.quantity)} unidades · coste estimado {preciseMoney.format(Number(item.unit_cost))} por unidad</span>
            <div className="purchase-schedule-grid">
              <label className="ui-field"><span>Precio de lista (opcional)</span><input type="number" min="0" step="0.01" value={offer.list} onChange={event => updateOffer(item.product_id, { list: event.target.value })} /></label>
              <label className="ui-field"><span>Descuento por unidad</span><input type="number" min="0" step="0.01" value={offer.discount} onChange={event => updateOffer(item.product_id, { discount: event.target.value })} /></label>
              <label className="ui-field"><span>Detalle de la oferta</span><input maxLength={500} value={offer.note} onChange={event => updateOffer(item.product_id, { note: event.target.value })} /></label>
            </div>
          </div>;
        })}
        <label className="ui-field"><span>Observaciones de ofertas (opcional)</span><textarea rows={2} maxLength={2000} value={notes} onChange={event => setNotes(event.target.value)} /></label>
        <div className="purchase-subtotal"><span>Compromiso estimado</span><strong>{preciseMoney.format(Number(purchase.total))}</strong></div>
        <p className="purchase-detail-code">Este importe es estimado. La obligación real se calculará al recibir la mercancía.</p>
        {error && <p className="ui-field-error" role="alert">{error}</p>}
        <div className="ui-form__actions">
          <TouchButton variant="secondary" onClick={close} disabled={busy}>Volver</TouchButton>
          <TouchButton module="compras" disabled={busy} onClick={() => {
            const errors = scheduleErrors(purchase, body);
            if (errors.length) { setError(errors.join(" ")); return; }
            setError(""); setReview(true);
          }}>Revisar programación</TouchButton>
        </div>
      </div>
      <ConfirmDialog open={review} module="compras" title="Programar este pedido" confirmLabel="Programar pedido"
        busy={busy} onCancel={() => setReview(false)} onConfirm={() => void submit()}>
        Entrega prevista: {delivery}. {paymentTermsLabel[terms]}. Importe estimado: {preciseMoney.format(Number(purchase.total))}. No se registrará pago ni movimiento de caja al programar.
      </ConfirmDialog>
    </Sheet>
  );
}
