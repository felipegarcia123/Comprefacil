"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ConfirmDialog, InlineMessage, PaymentButton, Sheet, TouchButton } from "@/components/ui";
import { errorText, money, preciseMoney } from "@/lib/format";
import { paymentTermsLabel, receiptPreview, receiptRequestIdentity } from "@/lib/smartPurchasing";
import type { PaymentChannel, Purchase, ReceiveComparisonIn, ReceivedItemIn } from "@/lib/types";

type Props = {
  purchase: Purchase;
  channels: PaymentChannel[];
  cajaOpen: boolean;
  busy: boolean;
  close: () => void;
  /** Receive + pay in one transaction (existing contract; no bare "receive without paying" in this UI — see CLAUDE.md). */
  confirm: (channelId: string, reference: string, notes: string) => Promise<void>;
  confirmExistingPayment: (channelId: string, reference: string, notes: string) => Promise<void>;
  confirmScheduled: (comparison: ReceiveComparisonIn, payment?: { channelId: string; reference: string; notes: string }) => Promise<void>;
};

/**
 * Payment channel + explicit confirmation before RECIBIR COMPRA. Same rules as before the
 * redesign: only Efectivo or Transferencia, exact full amount, no credit/partial payments.
 * The action is irreversible (stock, product cost and — if cash — the drawer all move at once),
 * so a ConfirmDialog spells out exactly what is about to happen before it happens.
 */
export function ReceiveSheet(props: Props) {
  return props.purchase.status === "SCHEDULED" ? <ScheduledReceiveSheet {...props} /> : <LegacyReceiveSheet {...props} />;
}

function LegacyReceiveSheet({ purchase, channels, cajaOpen, busy, close, confirm, confirmExistingPayment }: Props) {
  const allowed = channels.filter(channel => channel.affects_cash || channel.payment_type === "TRANSFER");
  const [channelId, setChannelId] = useState("");
  const [reference, setReference] = useState("");
  const [notes, setNotes] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");
  const channel = allowed.find(item => item.id === channelId);
  const units = purchase.items.reduce((sum, item) => sum + Number(item.quantity), 0);

  async function submit() {
    if (!channel) return;
    setError("");
    try { await (purchase.status === "RECEIVED" ? confirmExistingPayment(channel.id, reference, notes) : confirm(channel.id, reference, notes)); }
    catch (cause) { setError(errorText(cause)); setConfirming(false); }
  }

  return (
    <Sheet open title={purchase.status === "RECEIVED" ? "Pagar compra recibida" : "Recibir compra"} persistent onClose={close} module="compras">
      <div className="ui-stack">
        <p className="purchase-detail-code">{purchase.supplier_name} · {purchase.invoice_number ?? purchase.id.slice(0, 8)}</p>
        <p>{purchase.items.length} {purchase.items.length === 1 ? "referencia" : "referencias"} · {units} unidades · <b className="ui-num">{money.format(Number(purchase.total))}</b></p>
        {!cajaOpen && (
          <InlineMessage tone="warning" actions={<Link href="/cash" data-module="caja" className="ui-btn ui-btn--secondary ui-btn--md">Abrir caja</Link>}>
            La caja está cerrada. Recibir mercancía exige la jornada abierta, aunque el pago sea por transferencia.
          </InlineMessage>
        )}
        <div className="ui-form__channels" role="group" aria-label="Forma de pago">
          {allowed.map(item => <PaymentButton key={item.id} name={item.name} kind={item.payment_type} selected={item.id === channelId} onSelect={() => setChannelId(item.id)} />)}
        </div>
        <label className="ui-field"><span>Referencia (opcional)</span><input value={reference} onChange={event => setReference(event.target.value)} maxLength={120} /></label>
        <label className="ui-field"><span>Observación (opcional)</span><textarea rows={2} value={notes} onChange={event => setNotes(event.target.value)} maxLength={500} /></label>
        {error && <p className="ui-field-error" role="alert">{error}</p>}
        <div className="ui-form__actions">
          <TouchButton variant="secondary" size="lg" onClick={close} disabled={busy}>Volver</TouchButton>
          <TouchButton size="lg" module="compras" disabled={!channel || busy || !cajaOpen} onClick={() => setConfirming(true)}>{purchase.status === "RECEIVED" ? "Registrar pago" : "Recibir compra"}</TouchButton>
        </div>
      </div>

      <ConfirmDialog
        open={confirming} module="compras" title={purchase.status === "RECEIVED" ? "Registrar pago pendiente" : "Vas a recibir esta mercancía"} confirmLabel={purchase.status === "RECEIVED" ? "Pagar compra" : "Recibir compra"}
        cancelLabel="Revisar" busy={busy} onCancel={() => setConfirming(false)} onConfirm={() => void submit()}
      >
        {purchase.status !== "RECEIVED" && <>Sumará {units} unidades al inventario y actualizará el costo de los productos. </>}
        {channel?.affects_cash ? <>Se descontarán <b className="ui-num">{money.format(Number(purchase.total))}</b> de la caja</>
          : <>El pago quedará registrado por transferencia, sin afectar el efectivo físico</>}. Esta acción no se puede deshacer.
      </ConfirmDialog>
    </Sheet>
  );
}

type SavedReceipt = {
  lines: ReceivedItemIn[]; invoice: string; channelId: string; reference: string; notes: string;
  request: { signature: string; id: string } | null;
};

function ScheduledReceiveSheet({ purchase, channels, cajaOpen, busy, close, confirmScheduled }: Props) {
  const storageKey = `pos.purchase.receipt.${purchase.id}`;
  const [lines, setLines] = useState<ReceivedItemIn[]>(() => purchase.items.map(item => ({
    product_id: item.product_id, received_quantity: item.ordered_quantity ?? item.quantity,
    actual_unit_cost: item.ordered_unit_cost ?? item.unit_cost, difference_reason: null,
  })));
  const [invoice, setInvoice] = useState(purchase.invoice_number ?? "");
  const [channelId, setChannelId] = useState("");
  const [reference, setReference] = useState("");
  const [notes, setNotes] = useState("");
  const [review, setReview] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [hydrated, setHydrated] = useState(false);
  const submitting = useRef(false);
  const request = useRef<SavedReceipt["request"]>(null);

  useEffect(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(storageKey) || "null") as SavedReceipt | null;
      if (saved && saved.lines.length === purchase.items.length &&
          saved.lines.every(row => purchase.items.some(item => item.product_id === row.product_id))) {
        setLines(saved.lines); setInvoice(saved.invoice); setChannelId(saved.channelId);
        setReference(saved.reference); setNotes(saved.notes); request.current = saved.request;
      }
    } catch { /* private storage can be unavailable */ }
    setHydrated(true);
  }, [purchase.id, purchase.items, storageKey]);

  useEffect(() => {
    if (!hydrated) return;
    try { sessionStorage.setItem(storageKey, JSON.stringify({ lines, invoice, channelId, reference, notes, request: request.current })); }
    catch { /* retry still works while this sheet stays mounted */ }
  }, [hydrated, storageKey, lines, invoice, channelId, reference, notes]);

  const terms = purchase.payment_terms;
  const allowed = channels.filter(channel => terms === "CASH_ON_DELIVERY" ? channel.affects_cash
    : terms === "DIGITAL_ON_DELIVERY" ? !channel.affects_cash && channel.payment_type === "TRANSFER" : false);
  const preview = receiptPreview(purchase, lines);
  const channel = allowed.find(item => item.id === channelId);
  const formErrors = [...preview.errors];
  if (!terms) formErrors.push("La condición de pago del pedido no está disponible.");
  if (purchase.invoice_number && !invoice.trim()) formErrors.push("No se puede borrar aquí la factura ya registrada.");
  if (terms !== "CREDIT" && !channel) formErrors.push("Selecciona el canal de pago acordado.");

  function updateLine(productId: string, patch: Partial<ReceivedItemIn>) {
    setLines(current => current.map(row => row.product_id === productId ? { ...row, ...patch } : row));
    setError("");
  }

  async function submit() {
    if (submitting.current || busy || formErrors.length) { setError(formErrors.join(" ")); setReview(false); return; }
    submitting.current = true; setSending(true); setError("");
    const comparisonBase = { invoice_number: invoice.trim() || null, items: lines.map(row => ({
      ...row, difference_reason: row.difference_reason?.trim() || null,
    })) };
    const payment = terms === "CREDIT" ? undefined : { channelId, reference, notes };
    const signature = JSON.stringify({ comparisonBase, payment });
    request.current = receiptRequestIdentity(request.current, signature, () => crypto.randomUUID());
    const requestId = request.current.id;
    try {
      sessionStorage.setItem(storageKey, JSON.stringify({ lines, invoice, channelId, reference, notes, request: request.current }));
      await confirmScheduled({ request_id: requestId, ...comparisonBase }, payment);
      sessionStorage.removeItem(storageKey);
    } catch (cause) {
      setError(errorText(cause)); setReview(false);
    } finally { submitting.current = false; setSending(false); }
  }

  return <Sheet open title="Recepción comparada" persistent onClose={close} module="compras" wide>
    <div className="ui-stack">
      <p><strong>{purchase.supplier_name}</strong> · {terms ? paymentTermsLabel[terms] : "Condición pendiente"}</p>
      <p className="purchase-detail-code">Pedido original: {preciseMoney.format(Number(purchase.estimated_total ?? purchase.total))}. La entrega incompleta se cierra como recepción final.</p>
      {!cajaOpen && <InlineMessage tone="warning" actions={<Link href="/cash" data-module="caja" className="ui-btn ui-btn--secondary ui-btn--md">Abrir caja</Link>}>
        Abre la jornada antes de recibir, incluso si el pedido es a crédito o digital.
      </InlineMessage>}
      <label className="ui-field"><span>Factura del proveedor (si se conoce)</span><input maxLength={80} value={invoice} onChange={event => setInvoice(event.target.value)} /></label>
      <h3>Comparar mercancía</h3>
      {purchase.items.map(item => {
        const row = lines.find(line => line.product_id === item.product_id);
        if (!row) return null;
        const orderedQty = Number(item.ordered_quantity ?? item.quantity);
        const orderedCost = Number(item.ordered_unit_cost ?? item.unit_cost);
        const receivedQty = Number(row.received_quantity);
        const receivedCost = row.actual_unit_cost === null ? null : Number(row.actual_unit_cost);
        const changed = receivedQty !== orderedQty || (receivedQty > 0 && receivedCost !== orderedCost);
        return <section className="purchase-compare-line" key={item.product_id}>
          <strong>{item.product_name}</strong>
          <p>Pedido: {orderedQty} unidades · {preciseMoney.format(orderedCost)} por unidad{item.offer_note ? ` · Oferta: ${item.offer_note}` : ""}</p>
          <div className="purchase-schedule-grid">
            <label className="ui-field"><span>Cantidad recibida</span><input type="number" min="0" step="0.001" value={row.received_quantity} onChange={event => {
              const quantity = event.target.value;
              updateLine(item.product_id, { received_quantity: quantity,
                actual_unit_cost: quantity !== "" && Number(quantity) === 0 ? null : row.actual_unit_cost ?? String(orderedCost) });
            }} /></label>
            <label className="ui-field"><span>Costo unitario real</span><input type="number" min="0" step="0.01" disabled={row.received_quantity !== "" && Number(row.received_quantity) === 0} value={row.actual_unit_cost ?? ""} onChange={event => updateLine(item.product_id, { actual_unit_cost: event.target.value })} /></label>
          </div>
          <p className={changed ? "purchase-compare-difference" : "purchase-detail-code"}>
            Diferencia: {Number.isFinite(receivedQty) ? receivedQty - orderedQty : "—"} unidades
            {receivedQty > 0 && receivedCost !== null ? ` · ${preciseMoney.format(receivedCost - orderedCost)} por unidad` : ""}
          </p>
          {(changed || row.difference_reason) && <label className="ui-field"><span>Motivo de la diferencia</span><input maxLength={500} value={row.difference_reason ?? ""} onChange={event => updateLine(item.product_id, { difference_reason: event.target.value })} /></label>}
        </section>;
      })}
      <div className="purchase-subtotal"><span>Importe real previsto de esta recepción</span><strong>{preciseMoney.format(Number(preview.totalCents) / 100)}</strong></div>
      <p className="purchase-detail-code">Vista previa con redondeo por línea a centavos; FastAPI confirma el importe final. {preview.differences} productos con diferencias.</p>
      {terms === "CREDIT" ? <p>Se registrará una obligación real por el importe recibido. Vencimiento pactado: <strong>{purchase.payment_due_date ?? "Sin fecha"}</strong>. Si ya pasó, permanecerá vencida.</p> : <>
        <div className="ui-form__channels" role="group" aria-label="Canal de pago contra entrega">
          {allowed.map(item => <PaymentButton key={item.id} name={item.name} kind={item.payment_type} selected={item.id === channelId} onSelect={() => setChannelId(item.id)} />)}
        </div>
        {!allowed.length && <p className="ui-field-error">No hay un canal activo compatible con la modalidad acordada.</p>}
        <label className="ui-field"><span>Referencia (opcional)</span><input maxLength={120} value={reference} onChange={event => setReference(event.target.value)} /></label>
        <label className="ui-field"><span>Observación del pago (opcional)</span><textarea rows={2} maxLength={500} value={notes} onChange={event => setNotes(event.target.value)} /></label>
      </>}
      {error && <p className="ui-field-error" role="alert">{error}</p>}
      <div className="ui-form__actions">
        <TouchButton variant="secondary" onClick={close} disabled={busy || sending}>Volver</TouchButton>
        <TouchButton module="compras" disabled={busy || sending || !cajaOpen || formErrors.length > 0}
          onClick={() => { setError(""); setReview(true); }}>Revisar recepción</TouchButton>
      </div>
    </div>
    <ConfirmDialog open={review} module="compras" title="Confirmar recepción final" confirmLabel="Recibir pedido"
      busy={busy || sending} onCancel={() => setReview(false)} onConfirm={() => void submit()}>
      Se registrarán {Number(preview.totalQuantityMilli) / 1000} unidades por {preciseMoney.format(Number(preview.totalCents) / 100)}. {terms === "CREDIT" ? "Quedará como obligación del proveedor, sin pago ni salida de caja ahora." : terms === "CASH_ON_DELIVERY" ? "El pago saldrá de la caja." : "El pago digital no disminuirá el efectivo físico."} No se podrá recibir este pedido otra vez.
    </ConfirmDialog>
  </Sheet>;
}
