"use client";

import { FormEvent, useEffect, useState } from "react";
import { Scale } from "lucide-react";
import { api } from "@/lib/api";
import { errorText, money } from "@/lib/format";
import { InlineMessage, TouchButton } from "@/components/ui";
import type { ReconciliationBlock1 } from "@/lib/types";

/**
 * The scale's own closing for a cash session: Total báscula + Pagos digitales báscula. Efectivo báscula is
 * derived (total - digital), never typed; 0 <= digital <= total. Reconciliation data only: saving it never
 * creates a payment or a cash movement and does not change the expected cash.
 */
export function ScaleClosingForm({ data, onSaved }: { data: ReconciliationBlock1; onSaved: (next: ReconciliationBlock1) => void }) {
  const [total, setTotal] = useState("");
  const [digital, setDigital] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setTotal(data.scale ? String(Number(data.scale.total)) : "");
    setDigital(data.scale ? String(Number(data.scale.digital)) : "");
  }, [data.cash_session_id, data.scale?.total, data.scale?.digital]);

  const totalNumber = Number(total || 0);
  const digitalNumber = Number(digital || 0);
  const invalid = digitalNumber > totalNumber;
  const incomplete = total === "" || digital === "";

  async function save(event: FormEvent) {
    event.preventDefault();
    if (invalid || incomplete) return;
    setBusy(true); setError("");
    try { onSaved(await api.saveScaleClosing(data.cash_session_id, totalNumber, digitalNumber)); }
    catch (failure) { setError(errorText(failure)); }
    finally { setBusy(false); }
  }

  return (
    <form className="recon__scale" onSubmit={save}>
      <p className="recon__title"><Scale size={18} aria-hidden="true" />Cierre de la báscula</p>
      <div className="recon__inputs">
        <label className="ui-field"><span>Total báscula</span><input inputMode="numeric" value={total} onChange={event => setTotal(event.target.value.replace(/\D/g, ""))} placeholder="$ 0" /></label>
        <label className="ui-field"><span>Pagos digitales báscula</span><input inputMode="numeric" value={digital} onChange={event => setDigital(event.target.value.replace(/\D/g, ""))} placeholder="$ 0" /></label>
      </div>
      <p className="operations-row"><span>Efectivo báscula</span><b className="ui-num">{incomplete || invalid ? "—" : money.format(totalNumber - digitalNumber)}</b></p>
      {invalid && <InlineMessage tone="warning">Los pagos digitales no pueden ser mayores que el total de la báscula.</InlineMessage>}
      {error && <InlineMessage tone="danger" onDismiss={() => setError("")}>{error}</InlineMessage>}
      <TouchButton type="submit" variant="secondary" module="caja" loading={busy} disabled={busy || invalid || incomplete}>Guardar báscula</TouchButton>
    </form>
  );
}
