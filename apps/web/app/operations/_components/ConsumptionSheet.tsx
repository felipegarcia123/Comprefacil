"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Sheet, TouchButton } from "@/components/ui";
import { errorText } from "@/lib/format";

type Body = { description: string; estimated_value: number; notes?: string };
type Props = { open: boolean; busy: boolean; onClose: () => void; onSubmit: (body: Body) => Promise<void> };

/** Manual entries remain here unchanged. Catalog withdrawals have one home in /sales. */
export function ConsumptionSheet({ open, busy, onClose, onSubmit }: Props) {
  const [description, setDescription] = useState(""); const [value, setValue] = useState(""); const [notes, setNotes] = useState("");
  const [error, setError] = useState("");

  useEffect(() => { if (open) { setDescription(""); setValue(""); setNotes(""); setError(""); } }, [open]);
  const valid = description.trim() !== "" && Number(value) > 0;

  async function submit() {
    if (!valid) return;
    setError("");
    try { await onSubmit({ description: description.trim(), estimated_value: Number(value), notes: notes.trim() || undefined }); }
    catch (cause) { setError(errorText(cause)); }
  }

  return (
    <Sheet open={open} title="Casa / consumo interno" persistent module="caja" onClose={onClose}>
      <div className="ui-form">
        <p className="operations-note">Para retirar productos inventariados, usa el carrito de Ventas: permite varios productos, valida stock y conserva un solo comprobante.</p>
        <Link href="/sales?consumption=1" className="ui-btn ui-btn--secondary ui-btn--lg" onClick={onClose}>Ir a Casa / consumo interno</Link>
        <div className="ui-divider" />
        <h3>Registro manual</h3>
        <label className="ui-field"><span>Concepto</span><input value={description} onChange={e => setDescription(e.target.value)} /></label>
        <label className="ui-field"><span>Valor estimado</span><input inputMode="numeric" value={value} onChange={e => setValue(e.target.value.replace(/\D/g, ""))} placeholder="$ 0" /></label>
        <label className="ui-field"><span>Observación</span><textarea rows={2} value={notes} onChange={e => setNotes(e.target.value)} /></label>
        {error && <p className="ui-field-error" role="alert">{error}</p>}
        <div className="ui-form__actions">
          <TouchButton variant="secondary" size="lg" onClick={onClose} disabled={busy}>Cancelar</TouchButton>
          <TouchButton size="lg" module="caja" disabled={!valid || busy} loading={busy} onClick={() => void submit()}>Registrar</TouchButton>
        </div>
      </div>
    </Sheet>
  );
}
