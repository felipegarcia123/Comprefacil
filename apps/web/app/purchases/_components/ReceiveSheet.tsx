"use client";

import { useState } from "react";
import Link from "next/link";
import { ConfirmDialog, InlineMessage, PaymentButton, Sheet, TouchButton } from "@/components/ui";
import { errorText, money } from "@/lib/format";
import type { PaymentChannel, Purchase } from "@/lib/types";

type Props = {
  purchase: Purchase;
  channels: PaymentChannel[];
  cajaOpen: boolean;
  busy: boolean;
  close: () => void;
  /** Receive + pay in one transaction (existing contract; no bare "receive without paying" in this UI — see CLAUDE.md). */
  confirm: (channelId: string, reference: string, notes: string) => Promise<void>;
};

/**
 * Payment channel + explicit confirmation before RECIBIR COMPRA. Same rules as before the
 * redesign: only Efectivo or Transferencia, exact full amount, no credit/partial payments.
 * The action is irreversible (stock, product cost and — if cash — the drawer all move at once),
 * so a ConfirmDialog spells out exactly what is about to happen before it happens.
 */
export function ReceiveSheet({ purchase, channels, cajaOpen, busy, close, confirm }: Props) {
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
    try { await confirm(channel.id, reference, notes); }
    catch (cause) { setError(errorText(cause)); setConfirming(false); }
  }

  return (
    <Sheet open title="Recibir compra" persistent onClose={close} module="compras">
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
          <TouchButton size="lg" module="compras" disabled={!channel || busy || !cajaOpen} onClick={() => setConfirming(true)}>Recibir compra</TouchButton>
        </div>
      </div>

      <ConfirmDialog
        open={confirming} module="compras" title="Vas a recibir esta mercancía" confirmLabel="Recibir compra"
        cancelLabel="Revisar" busy={busy} onCancel={() => setConfirming(false)} onConfirm={() => void submit()}
      >
        Sumará {units} unidades al inventario, actualizará el costo de {purchase.items.length === 1 ? "ese producto" : "esos productos"} y
        {channel?.affects_cash ? <> descontará <b className="ui-num">{money.format(Number(purchase.total))}</b> de la caja</>
          : <> quedará registrada por transferencia, sin afectar el efectivo físico</>}. Esta acción no se puede deshacer.
      </ConfirmDialog>
    </Sheet>
  );
}
