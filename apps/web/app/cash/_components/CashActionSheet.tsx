"use client";

import { useEffect, useState } from "react";
import { Sheet, TouchButton } from "@/components/ui";
import { errorText, money } from "@/lib/format";
import type { WithdrawalReason } from "@/lib/types";

export type CashAction = "open" | "deposit" | "withdrawal";

type Props = {
  action: CashAction | null;
  busy: boolean;
  onClose: () => void;
  onSubmit: (amount: number, note: string, withdrawalReason?: WithdrawalReason) => Promise<void>;
};

const TITLE: Record<CashAction, string> = { open: "Abrir caja", deposit: "Registrar ingreso", withdrawal: "Registrar retiro" };
const AMOUNT_LABEL: Record<CashAction, string> = { open: "Monto inicial", deposit: "Monto del ingreso", withdrawal: "Monto del retiro" };

/** Open / Ingreso / Retiro: same shape (monto + observación), withdrawal adds the required Casa/Otro reason. */
export function CashActionSheet({ action, busy, onClose, onSubmit }: Props) {
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [reason, setReason] = useState<WithdrawalReason | "">("");
  const [error, setError] = useState("");

  useEffect(() => { setAmount(""); setNote(""); setReason(""); setError(""); }, [action]);

  const value = Number(amount);
  const isWithdrawal = action === "withdrawal";
  const requiresNote = action !== "open";
  const valid = amount.trim() !== "" && Number.isFinite(value) && value > 0
    && (!requiresNote || note.trim().length >= 3) && (!isWithdrawal || reason !== "");

  async function submit() {
    if (!action || !valid) return;
    setError("");
    try { await onSubmit(value, note.trim(), isWithdrawal ? (reason as WithdrawalReason) : undefined); }
    catch (cause) { setError(errorText(cause)); }
  }

  return (
    <Sheet open={action !== null} title={action ? TITLE[action] : ""} persistent module="caja" onClose={onClose}>
      {action && (
        <div className="ui-form">
          <label className="ui-field"><span>{AMOUNT_LABEL[action]}</span>
            <input autoFocus type="number" inputMode="numeric" min="0" step="1" value={amount} placeholder="$ 0"
              onChange={event => { setAmount(event.target.value); setError(""); }} />
            {amount.trim() !== "" && Number.isFinite(value) && <small className="cash-amount-preview">{money.format(value)}</small>}
          </label>
          {isWithdrawal && (
            <div className="ui-field"><span>Motivo del retiro *</span>
              <div className="cash-reason-buttons">
                <TouchButton type="button" variant={reason === "HOME" ? "primary" : "secondary"} module="caja" onClick={() => setReason("HOME")}>Casa</TouchButton>
                <TouchButton type="button" variant={reason === "OTHER" ? "primary" : "secondary"} module="caja" onClick={() => setReason("OTHER")}>Otro</TouchButton>
              </div>
            </div>
          )}
          <label className="ui-field"><span>{action === "open" ? "Observación (opcional)" : "Motivo / observación *"}</span>
            <textarea rows={2} value={note} onChange={event => { setNote(event.target.value); setError(""); }} />
          </label>
          {error && <p className="ui-field-error" role="alert">{error}</p>}
          <div className="ui-form__actions">
            <TouchButton variant="secondary" size="lg" onClick={onClose} disabled={busy}>Cancelar</TouchButton>
            <TouchButton size="lg" module="caja" disabled={!valid || busy} loading={busy} onClick={() => void submit()}>Confirmar</TouchButton>
          </div>
        </div>
      )}
    </Sheet>
  );
}
