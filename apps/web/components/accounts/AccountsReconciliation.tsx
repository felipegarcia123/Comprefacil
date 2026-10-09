"use client";

import { useEffect, useState } from "react";
import { CircleAlert, CircleCheck, CircleMinus, PenLine } from "lucide-react";
import { ConfirmDialog, InlineMessage, Sheet, StatusBadge, TouchButton } from "@/components/ui";
import { api } from "@/lib/api";
import { cx } from "@/lib/cx";
import { dateTime, errorText, money } from "@/lib/format";
import type { AccountReconciliation, AccountReconciliationRow } from "@/lib/types";

export const ACCOUNTS_PENDING = "Conciliación de cuentas pendiente";

/** Electronic money of the period with no destination account: the reconciliation is never "verified". */
export function UnclassifiedWarning({ data }: { data: AccountReconciliation }) {
  const unclassified = data.unclassified;
  if (!unclassified?.count) return null;
  return (
    <InlineMessage tone="warning" title={`Hay ${unclassified.count} ${unclassified.count === 1 ? "operación electrónica" : "operaciones electrónicas"} sin cuenta destino (${money.format(Number(unclassified.amount))})`}>
      No se atribuyen a ninguna cuenta: la conciliación de este período no está completamente verificada.
      <ul className="patrimony__missing">{unclassified.items.map((item, index) => <li key={index}>{item.label} · {money.format(Number(item.amount))}</li>)}</ul>
    </InlineMessage>
  );
}

const newKey = () => (typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `k-${Date.now()}-${Math.random()}`);

/** Difference observed − expected, with icon + text (never color only). Baseline: no difference. */
export function AccountDifference({ difference, baseline }: { difference: number | null; baseline?: boolean }) {
  if (baseline) return <span className="account-diff account-diff--none">Primer saldo registrado</span>;
  if (difference === null) return <span className="account-diff account-diff--none">—</span>;
  if (difference === 0) return <span className="account-diff account-diff--ok"><CircleCheck size={18} aria-hidden="true" />Cuadra</span>;
  return (
    <span className={cx("account-diff", difference < 0 ? "account-diff--neg" : "account-diff--pos")}>
      {difference < 0 ? <CircleMinus size={18} aria-hidden="true" /> : <CircleAlert size={18} aria-hidden="true" />}
      <span className="ui-num">{difference < 0 ? "−" : "+"}{money.format(Math.abs(difference))}</span>
      {difference < 0 ? " falta" : " sobra"}
    </span>
  );
}

function Flows({ row }: { row: AccountReconciliationRow }) {
  const parts = [
    Number(row.pos_inflows) ? `Ventas y cobros POS +${money.format(Number(row.pos_inflows))}` : null,
    Number(row.pos_outflows) ? `Devoluciones POS −${money.format(Number(row.pos_outflows))}` : null,
    Number(row.registered_external_inflows) ? `Ingresos externos +${money.format(Number(row.registered_external_inflows))}` : null,
    Number(row.registered_external_outflows) ? `Salidas externas −${money.format(Number(row.registered_external_outflows))}` : null,
    Number(row.pending_outflows) ? `Pendiente por devolver ${money.format(Number(row.pending_outflows))} (aún no se descuenta)` : null,
  ].filter(Boolean);
  if (!parts.length) return null;
  return <p className="account-row__flows">{parts.map(part => <span key={part}>{part}</span>)}</p>;
}

/** Inputs of the close sheet: one row per account, expected vs typed balance, live difference. */
export function AccountBalanceInputs({ data, values, onChange, disabled }: { data: AccountReconciliation; values: Record<string, string>; onChange: (accountId: string, value: string) => void; disabled?: boolean }) {
  return (
    <ul className="accounts-list accounts-panel">
      {data.accounts.map(row => {
        const typed = values[row.account_id] ?? "";
        const value = Number(typed);
        const has = typed.trim() !== "" && Number.isFinite(value) && value >= 0;
        const expected = row.expected_balance === null ? null : Number(row.expected_balance);
        return (
          <li key={row.account_id} className="account-row">
            <div className="account-row__head"><span className="account-row__name">{row.name}</span>
              {row.status === "LOCKED" && <StatusBadge tone="neutral">Ya registrada en una jornada posterior</StatusBadge>}</div>
            <div className="account-row__figures">
              <div className="account-row__figure"><span>Esperado</span><b className="ui-num">{expected === null ? "Sin saldo anterior" : money.format(expected)}</b></div>
              <label className="account-row__figure account-row__figure--input"><span>Saldo real al cierre</span>
                <input type="number" inputMode="numeric" min="0" step="1" placeholder="$ 0" value={typed} disabled={disabled || row.status === "LOCKED"}
                  aria-label={`Saldo real de ${row.name}`} onChange={event => onChange(row.account_id, event.target.value)} /></label>
              <div className="account-row__figure"><span>Diferencia</span>
                <b>{has ? <AccountDifference difference={expected === null ? null : value - expected} baseline={expected === null} /> : <span className="account-diff account-diff--none">—</span>}</b></div>
            </div>
            <Flows row={row} />
          </li>
        );
      })}
    </ul>
  );
}

/**
 * CUENTAS of a closed jornada: frozen balances (CAPTURED), what is still pending (typed here, after the close)
 * and the audited "Corregir saldo registrado". Never changes the cash drawer, sales or payments.
 */
export function AccountsReport({ sessionId, onChanged }: { sessionId: string; onChanged?: () => void }) {
  const [data, setData] = useState<AccountReconciliation | null>(null);
  const [error, setError] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [correcting, setCorrecting] = useState<AccountReconciliationRow | null>(null);

  useEffect(() => {
    setData(null); setError(""); setValues({});
    api.sessionAccounts(sessionId).then(setData).catch(cause => setError(errorText(cause)));
  }, [sessionId]);

  if (error) return <InlineMessage tone="danger">{error}</InlineMessage>;
  if (!data) return <p className="recon-report__note" role="status">Cargando cuentas…</p>;
  const captured = data.accounts.filter(row => row.status === "CAPTURED");
  const pending = data.accounts.filter(row => row.status === "PENDING");
  const typed = pending.filter(row => (values[row.account_id] ?? "").trim() !== "" && Number(values[row.account_id]) >= 0);

  async function register() {
    setBusy(true); setError("");
    try {
      setData(await api.registerAccountBalances(sessionId, typed.map(row => ({ account_id: row.account_id, observed_balance: Number(values[row.account_id]) }))));
      setValues({}); setConfirming(false); onChanged?.();
    } catch (cause) { setError(errorText(cause)); setConfirming(false); }
    finally { setBusy(false); }
  }

  return (
    <div className="ui-stack accounts-panel">
      {data.status !== "COMPLETE" && <InlineMessage tone="warning">{ACCOUNTS_PENDING}. Puedes completarla ahora: no cambia la caja ya cerrada.</InlineMessage>}
      <UnclassifiedWarning data={data} />
      {data.verified && <InlineMessage tone="success">Conciliación de cuentas completa y verificada.</InlineMessage>}
      {captured.length > 0 && (
        <ul className="accounts-list">
          {captured.map(row => (
            <li key={row.account_id} className="account-row">
              <div className="account-row__head"><span className="account-row__name">{row.name}</span>
                <AccountDifference difference={row.difference === null ? null : Number(row.difference)} baseline={row.is_baseline} /></div>
              <div className="account-row__figures">
                <div className="account-row__figure"><span>Saldo anterior</span><b className="ui-num">{row.previous_observed_balance === null ? "—" : money.format(Number(row.previous_observed_balance))}</b></div>
                <div className="account-row__figure"><span>Esperado</span><b className="ui-num">{row.expected_balance === null ? "—" : money.format(Number(row.expected_balance))}</b></div>
                <div className="account-row__figure"><span>Saldo real</span><b className="ui-num">{money.format(Number(row.observed_balance))}</b></div>
              </div>
              <Flows row={row} />
              {row.corrections.map(c => (
                <p key={c.id} className="account-row__note">Corregido el {dateTime.format(new Date(c.created_at))}: {money.format(Number(c.old_observed_balance))} → {money.format(Number(c.new_observed_balance))} · {c.reason}</p>
              ))}
              {row.can_correct && <div className="account-row__actions"><TouchButton variant="ghost" module="caja" icon={PenLine} onClick={() => setCorrecting(row)}>Corregir saldo registrado</TouchButton></div>}
            </li>
          ))}
        </ul>
      )}
      {pending.length > 0 && <>
        <AccountBalanceInputs data={{ ...data, accounts: pending }} values={values} onChange={(id, value) => setValues(current => ({ ...current, [id]: value }))} disabled={busy} />
        <div className="ui-form__actions"><TouchButton module="caja" size="lg" disabled={!typed.length || busy} onClick={() => setConfirming(true)}>Registrar saldos</TouchButton></div>
      </>}
      {data.accounts.some(row => row.status === "LOCKED") && <p className="account-row__note">Las cuentas ya registradas en una jornada posterior no se pueden completar aquí.</p>}

      <ConfirmDialog open={confirming} module="caja" title="Registrar saldos de cuentas" confirmLabel="Registrar saldos" busy={busy}
        onCancel={() => setConfirming(false)} onConfirm={() => void register()}>
        <p>Se guardan como el saldo real al cierre de esta jornada: {typed.map(row => `${row.name} ${money.format(Number(values[row.account_id]))}`).join(" · ")}. No mueve dinero ni cambia la caja.</p>
      </ConfirmDialog>
      <CorrectBalanceSheet row={correcting} onClose={() => setCorrecting(null)} onDone={next => { setData(next); setCorrecting(null); onChanged?.(); }} />
    </div>
  );
}

/** "Corregir saldo registrado": old → new + reason, audited; the frozen snapshot keeps its original value. */
function CorrectBalanceSheet({ row, onClose, onDone }: { row: AccountReconciliationRow | null; onClose: () => void; onDone: (next: AccountReconciliation) => void }) {
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  const [key, setKey] = useState(newKey);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { setValue(""); setReason(""); setError(""); setKey(newKey()); }, [row]);
  const number = Number(value);
  const valid = Boolean(row) && value.trim() !== "" && Number.isFinite(number) && number >= 0 && number !== Number(row?.observed_balance) && reason.trim().length >= 3;

  async function submit() {
    if (!row?.snapshot_id) return;
    setBusy(true); setError("");
    try { onDone(await api.correctAccountBalance(row.snapshot_id, { new_observed_balance: number, reason: reason.trim(), idempotency_key: key })); }
    catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  }

  return (
    <Sheet open={row !== null} title="Corregir saldo registrado" persistent module="caja" onClose={onClose}>
      {row && (
        <div className="ui-form">
          <p className="account-row__note">{row.name}: registrado <b className="ui-num">{money.format(Number(row.observed_balance))}</b>. La corrección queda auditada (valor anterior, nuevo, motivo y fecha); el registro original no se borra.</p>
          <label className="ui-field"><span>Saldo correcto *</span>
            <input autoFocus type="number" inputMode="numeric" min="0" step="1" value={value} onChange={event => setValue(event.target.value)} /></label>
          <label className="ui-field"><span>Motivo *</span>
            <textarea rows={2} value={reason} placeholder="Ej.: se digitó un cero de más" onChange={event => setReason(event.target.value)} /></label>
          {error && <p className="ui-field-error" role="alert">{error}</p>}
          <div className="ui-form__actions">
            <TouchButton variant="secondary" size="lg" onClick={onClose} disabled={busy}>Cancelar</TouchButton>
            <TouchButton module="caja" size="lg" disabled={!valid || busy} loading={busy} onClick={() => void submit()}>Guardar corrección</TouchButton>
          </div>
        </div>
      )}
    </Sheet>
  );
}
