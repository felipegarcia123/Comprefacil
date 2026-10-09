"use client";

import { useEffect, useState } from "react";
import { ArrowLeftRight, Landmark, Undo2 } from "lucide-react";
import { ConfirmDialog, InlineMessage, Sheet, TouchButton } from "@/components/ui";
import { AccountPicker } from "@/components/accounts/AccountPicker";
import { api } from "@/lib/api";
import { cx } from "@/lib/cx";
import { errorText, money, time } from "@/lib/format";
import type { AccountMovement, BusinessAccount } from "@/lib/types";

type Kind = "OTHER_OUT" | "OTHER_IN" | "TRANSFER_BETWEEN_ACCOUNTS";
const KINDS: { kind: Kind; label: string; hint: string }[] = [
  { kind: "OTHER_OUT", label: "Salida", hint: "Se pagó algo desde la cuenta" },
  { kind: "OTHER_IN", label: "Ingreso", hint: "Entró dinero que no es una venta" },
  { kind: "TRANSFER_BETWEEN_ACCOUNTS", label: "Transferencia", hint: "Entre dos cuentas del negocio" },
];
const newKey = () => (typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `k-${Date.now()}-${Math.random()}`);

/**
 * MOVIMIENTO DE CUENTA: an explicit external movement of Nequi / Davivienda / Nu / DaviPlata (never a sale, never the cash
 * drawer). A transfer moves money between two business accounts: it is never income.
 */
export function AccountMovementSheet({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: (rows: AccountMovement[]) => void }) {
  const [accounts, setAccounts] = useState<BusinessAccount[]>([]);
  const [kind, setKind] = useState<Kind>("OTHER_OUT");
  const [account, setAccount] = useState("");
  const [target, setTarget] = useState("");
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [key, setKey] = useState(newKey);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    setKind("OTHER_OUT"); setAccount(""); setTarget(""); setAmount(""); setNote(""); setError(""); setKey(newKey());
    api.businessAccounts(true).then(setAccounts).catch(cause => setError(errorText(cause)));
  }, [open]);
  const transfer = kind === "TRANSFER_BETWEEN_ACCOUNTS";
  const value = Number(amount);
  const valid = Boolean(account) && value > 0 && (transfer ? Boolean(target) && target !== account : note.trim().length >= 3);

  async function submit() {
    setBusy(true); setError("");
    try {
      onDone(await api.createAccountMovement({ reason: kind, account_id: account, to_account_id: transfer ? target : undefined, amount: value, note: note.trim() || undefined, idempotency_key: key }));
    } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  }

  return (
    <Sheet open={open} title="Movimiento de cuenta" persistent module="caja" onClose={onClose}>
      <div className="ui-form">
        <div className="ui-choice" role="radiogroup" aria-label="Tipo de movimiento" style={{ gridTemplateColumns: "repeat(3, minmax(0, 1fr))" }}>
          {KINDS.map(item => (
            <button key={item.kind} type="button" role="radio" aria-checked={kind === item.kind}
              className={cx("ui-choice__item", kind === item.kind && "ui-choice__item--on")} onClick={() => setKind(item.kind)}>
              {item.label}<small>{item.hint}</small>
            </button>
          ))}
        </div>
        <AccountPicker accounts={accounts} value={account} onChange={setAccount} label={transfer ? "Sale de" : kind === "OTHER_IN" ? "Entra a" : "Sale de"} />
        {transfer && <AccountPicker accounts={accounts.filter(item => item.id !== account)} value={target} onChange={setTarget} label="Llega a" />}
        <label className="ui-field"><span>Valor *</span>
          <input type="number" inputMode="numeric" min="1" step="1" placeholder="$ 0" value={amount} onChange={event => setAmount(event.target.value)} /></label>
        <label className="ui-field"><span>{transfer ? "Nota (opcional)" : "¿Qué fue? *"}</span>
          <textarea rows={2} value={note} placeholder={transfer ? "" : "Ej.: pago del arriendo desde Nequi"} onChange={event => setNote(event.target.value)} /></label>
        <p className="account-row__note">No mueve el efectivo del cajón ni crea una venta: solo explica el saldo de la cuenta. Si es un gasto o un pago a proveedor ya registrado en Resumen, anótalo aquí solo si salió de una cuenta.</p>
        {error && <InlineMessage tone="danger">{error}</InlineMessage>}
        <div className="ui-form__actions">
          <TouchButton variant="secondary" size="lg" onClick={onClose} disabled={busy}>Cancelar</TouchButton>
          <TouchButton module="caja" size="lg" disabled={!valid || busy} loading={busy} onClick={() => void submit()}>Registrar movimiento</TouchButton>
        </div>
      </div>
    </Sheet>
  );
}

/** Movements of a jornada with "Revertir" (an audited opposite movement; the original stays). */
export function AccountMovementsPanel({ sessionId, refreshKey, onChanged }: { sessionId: string; refreshKey?: number; onChanged?: () => void }) {
  const [rows, setRows] = useState<AccountMovement[]>([]);
  const [reversing, setReversing] = useState<AccountMovement | null>(null);
  const [reason, setReason] = useState("");
  const [key, setKey] = useState(newKey);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { api.accountMovements(sessionId).then(setRows).catch(() => setRows([])); }, [sessionId, refreshKey]);

  async function reverse() {
    if (!reversing) return;
    setBusy(true); setError("");
    try {
      await api.reverseAccountMovement(reversing.id, { reason: reason.trim(), idempotency_key: key });
      setReversing(null); setReason(""); setKey(newKey());
      setRows(await api.accountMovements(sessionId)); onChanged?.();
    } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  }

  if (!rows.length) return <p className="ui-empty-hint">No hay movimientos de cuentas en esta jornada.</p>;
  return (
    <>
      <ul className="account-moves">
        {rows.map(row => (
          <li key={row.id}>
            <span>{row.reason === "TRANSFER_BETWEEN_ACCOUNTS" ? <ArrowLeftRight size={18} aria-hidden="true" /> : <Landmark size={18} aria-hidden="true" />}
              {" "}{row.account_name} · {row.reason_label}{row.reversed ? " · revertido" : ""}
              <small>{time.format(new Date(row.created_at))}{row.note ? ` · ${row.note}` : ""}</small></span>
            <span className="ui-row">
              <b className="ui-num">{row.direction === "IN" ? "+" : "−"}{money.format(Number(row.amount))}</b>
              {row.reason !== "REVERSAL" && !row.reversed && (row.reason !== "TRANSFER_BETWEEN_ACCOUNTS" || row.direction === "OUT") && (
                <TouchButton variant="ghost" module="caja" icon={Undo2} onClick={() => { setReversing(row); setError(""); }}>Revertir</TouchButton>
              )}
            </span>
          </li>
        ))}
      </ul>
      <ConfirmDialog open={reversing !== null} module="caja" title="Revertir movimiento de cuenta" confirmLabel="Revertir movimiento" busy={busy}
        onCancel={() => setReversing(null)} onConfirm={() => void reverse()}>
        <p>Se registra un movimiento contrario por {reversing ? money.format(Number(reversing.amount)) : ""}{reversing?.transfer_group_id ? " en las dos cuentas de la transferencia" : ""}. El original no se borra.</p>
        <label className="ui-field"><span>Motivo *</span><input value={reason} onChange={event => setReason(event.target.value)} /></label>
        {error && <p className="ui-field-error" role="alert">{error}</p>}
      </ConfirmDialog>
    </>
  );
}
