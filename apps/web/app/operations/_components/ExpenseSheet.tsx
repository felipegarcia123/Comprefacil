"use client";

import { useEffect, useState } from "react";
import { Sheet, TouchButton } from "@/components/ui";
import { errorText } from "@/lib/format";
import type { PaymentChannel } from "@/lib/types";

type Body = { amount: number; category: string; description: string; payment_source: "CASH" | "ELECTRONIC" | "OTHER"; payment_channel_id?: string; notes?: string };

type Props = { open: boolean; channels: PaymentChannel[]; busy: boolean; onClose: () => void; onSubmit: (body: Body) => Promise<void> };

/** Gasto operativo — nunca se cuenta como venta (section 10). Un gasto en efectivo exige jornada abierta (guard existente, sin duplicar). */
export function ExpenseSheet({ open, channels, busy, onClose, onSubmit }: Props) {
  const [amount, setAmount] = useState(""); const [category, setCategory] = useState(""); const [description, setDescription] = useState("");
  const [channel, setChannel] = useState(""); const [notes, setNotes] = useState(""); const [error, setError] = useState("");

  useEffect(() => { if (open) { setAmount(""); setCategory(""); setDescription(""); setChannel(""); setNotes(""); setError(""); } }, [open]);

  const selected = channels.find(row => row.id === channel);
  const source = selected?.payment_group === "CASH" ? "CASH" : selected ? "ELECTRONIC" : "OTHER";
  const valid = Number(amount) > 0 && category.trim() !== "" && description.trim() !== "";

  async function submit() {
    if (!valid) return;
    setError("");
    try {
      await onSubmit({ amount: Number(amount), category: category.trim(), description: description.trim(),
        payment_source: source, payment_channel_id: channel || undefined, notes: notes.trim() || undefined });
    } catch (cause) { setError(errorText(cause)); }
  }

  return (
    <Sheet open={open} title="Registrar gasto" persistent module="caja" onClose={onClose}>
      <div className="ui-form">
        <label className="ui-field"><span>Monto</span><input autoFocus type="number" inputMode="numeric" min="0" step="1" value={amount} onChange={e => setAmount(e.target.value)} placeholder="$ 0" /></label>
        <label className="ui-field"><span>Categoría</span><input value={category} onChange={e => setCategory(e.target.value)} placeholder="Transporte, bolsas…" /></label>
        <label className="ui-field"><span>Descripción</span><input value={description} onChange={e => setDescription(e.target.value)} /></label>
        <label className="ui-field"><span>Origen del pago</span>
          <select className="inventory-select" value={channel} onChange={e => setChannel(e.target.value)}>
            <option value="">Otro / externo</option>
            {channels.map(row => <option key={row.id} value={row.id}>{row.name} · {row.payment_group}</option>)}
          </select>
        </label>
        <label className="ui-field"><span>Observación</span><textarea rows={2} value={notes} onChange={e => setNotes(e.target.value)} /></label>
        {error && <p className="ui-field-error" role="alert">{error}</p>}
        <div className="ui-form__actions">
          <TouchButton variant="secondary" size="lg" onClick={onClose} disabled={busy}>Cancelar</TouchButton>
          <TouchButton size="lg" module="caja" disabled={!valid || busy} loading={busy} onClick={() => void submit()}>Guardar</TouchButton>
        </div>
      </div>
    </Sheet>
  );
}
