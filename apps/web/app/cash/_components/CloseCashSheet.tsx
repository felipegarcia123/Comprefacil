"use client";

import { useEffect, useState } from "react";
import { CircleAlert, CircleCheck } from "lucide-react";
import { ConfirmDialog, Display, Sheet, StatusBadge, TouchButton } from "@/components/ui";
import { api } from "@/lib/api";
import { errorText, money } from "@/lib/format";
import { SCALE_MISSING } from "@/components/reconciliation/Block1Report";
import { ScaleClosingForm } from "@/components/reconciliation/ScaleClosingForm";
import { ACCOUNTS_PENDING, AccountBalanceInputs, UnclassifiedWarning } from "@/components/accounts/AccountsReconciliation";
import { PatrimonyLegend, RegisteredValuationBlock } from "@/components/accounts/PatrimonyBlock";
import type { AccountReconciliation, CashSummary, InventoryValuation, PatrimonyDeclaration, ReconciliationBlock1 } from "@/lib/types";

export type AccountBalance = { account_id: string; observed_balance: number };
export type DeclaredPatrimony = { value: number; note?: string } | null;

type Props = {
  open: boolean;
  summary: CashSummary | null;
  busy: boolean;
  onClose: () => void;
  onSubmit: (counted: number, note: string, balances: AccountBalance[], declared: DeclaredPatrimony) => Promise<void>;
};

/**
 * CERRAR CAJA in four blocks that never add up with each other:
 *  1. Cuadre de caja — Efectivo esperado -> contado -> diferencia (unchanged rules; the counted amount is never
 *     pre-filled). 2. Báscula / cuadre parcial — what already existed. 3. Cuentas digitales — the balance the
 *     owner sees in Nequi / Davivienda / Nu / DaviPlata vs the expected one (optional: can be completed after the close).
 *  4. Patrimonio de inventario — computed by the system, read only.
 * A `ConfirmDialog` is the actual point of no return.
 */
export function CloseCashSheet({ open, summary, busy, onClose, onSubmit }: Props) {
  const [counted, setCounted] = useState("");
  const [note, setNote] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");
  const [balances, setBalances] = useState<Record<string, string>>({});
  const [declared, setDeclared] = useState("");
  const [declaredNote, setDeclaredNote] = useState("");

  useEffect(() => { setCounted(""); setNote(""); setConfirming(false); setError(""); setBalances({}); setDeclared(""); setDeclaredNote(""); }, [open]);
  // Partial reconciliation (block 1): only informative here. The scale closing can be completed before closing;
  // it never blocks the close and never changes the expected cash below.
  const [recon, setRecon] = useState<ReconciliationBlock1 | null>(null);
  const [accounts, setAccounts] = useState<AccountReconciliation | null>(null);
  const [patrimony, setPatrimony] = useState<InventoryValuation | null>(null);
  const [lastDeclared, setLastDeclared] = useState<PatrimonyDeclaration | null>(null);
  const [editScale, setEditScale] = useState(false);
  useEffect(() => {
    if (!open || !summary) { setRecon(null); setAccounts(null); setPatrimony(null); return; }
    setEditScale(false);
    api.reconciliation(summary.session.id).then(setRecon).catch(() => setRecon(null));
    api.sessionAccounts(summary.session.id).then(setAccounts).catch(() => setAccounts(null));
    api.inventoryValuationCurrent().then(setPatrimony).catch(() => setPatrimony(null));
    api.sessionPatrimonyDeclaration(summary.session.id).then(setLastDeclared).catch(() => setLastDeclared(null));
  }, [open, summary]);

  const expected = Number(summary?.expected_cash ?? 0);
  const value = Number(counted);
  const hasAmount = counted.trim() !== "" && Number.isFinite(value) && value >= 0;
  const difference = hasAmount ? value - expected : 0;
  const noteRequired = hasAmount && difference !== 0;
  const typedBalances: AccountBalance[] = (accounts?.accounts ?? [])
    .filter(row => row.status === "PENDING" && (balances[row.account_id] ?? "").trim() !== "")
    .map(row => ({ account_id: row.account_id, observed_balance: Number(balances[row.account_id]) }));
  const balancesValid = typedBalances.every(item => Number.isFinite(item.observed_balance) && item.observed_balance >= 0);
  const declaredNumber = Number(declared);
  const declaredValid = declared.trim() === "" || (Number.isFinite(declaredNumber) && declaredNumber >= 0);
  const valid = hasAmount && (!noteRequired || note.trim().length >= 3) && balancesValid && declaredValid;
  const pendingAccounts = (accounts?.accounts.filter(row => row.status === "PENDING").length ?? 0) - typedBalances.length;

  async function submit() {
    setError("");
    try { await onSubmit(value, note.trim(), typedBalances, declared.trim() === "" ? null : { value: declaredNumber, note: declaredNote.trim() || undefined }); }
    catch (cause) { setError(errorText(cause)); setConfirming(false); }
  }

  const diffTone = difference < 0 ? "danger" : difference > 0 ? "warning" : "success";
  const diffLabel = difference < 0 ? "Faltante" : difference > 0 ? "Sobrante" : "Caja cuadrada";

  return (
    <Sheet open={open} title="Cerrar caja" persistent module="caja" onClose={onClose}>
      <div className="ui-form close-flow">
        <details className="close-block" open>
          <summary><span className="close-block__n">1</span>Cuadre de caja</summary>
          <div className="close-block__body">
            <Display size="figure" label="Efectivo esperado en cajón" value={money.format(expected)} />
            <p className="cash-close-hint">No se precarga el conteo: escribe el resultado del conteo físico del cajón.</p>
            <label className="ui-field"><span>Efectivo contado</span>
              <input autoFocus type="number" inputMode="numeric" min="0" step="1" value={counted} placeholder="$ 0"
                onChange={event => { setCounted(event.target.value); setError(""); }} />
            </label>
            {hasAmount && (
              <p className={`cash-difference cash-difference--${diffTone}`}>
                <StatusBadge tone={diffTone}>{diffLabel}</StatusBadge>
                <b className="ui-num">{money.format(Math.abs(difference))}</b>
              </p>
            )}
            <label className="ui-field"><span>{noteRequired ? "Observación del cierre *" : "Observación (opcional)"}</span>
              <textarea rows={2} value={note} onChange={event => { setNote(event.target.value); setError(""); }} />
            </label>
          </div>
        </details>

        {recon && (
          <details className="close-block" open={!recon.scale}>
            <summary><span className="close-block__n">2</span>Báscula y cuadre parcial
              <span className="close-block__hint">{recon.scale ? `Báscula registrada · ${money.format(Number(recon.scale.cash))} en efectivo` : "Falta la báscula"}</span></summary>
            <div className="close-block__body recon-preclose">
              {!recon.scale && <p className="recon-preclose__missing" role="status">{SCALE_MISSING}</p>}
              {recon.scale && !editScale ? (
                <p className="operations-row">
                  <span>Báscula registrada · efectivo báscula <b className="ui-num">{money.format(Number(recon.scale.cash))}</b></span>
                  <TouchButton variant="ghost" module="caja" onClick={() => setEditScale(true)}>Revisar</TouchButton>
                </p>
              ) : (
                <ScaleClosingForm data={recon} onSaved={next => { setRecon(next); setEditScale(false); }} />
              )}
            </div>
          </details>
        )}

        {accounts && accounts.accounts.length > 0 && (
          <details className="close-block">
            <summary><span className="close-block__n">3</span>Cuentas digitales
              <span className="close-block__hint">{typedBalances.length ? `${typedBalances.length} de ${accounts.accounts.length} con saldo` : "Opcional · se puede completar después"}</span></summary>
            <div className="close-block__body">
              <p className="account-row__note">Escribe el saldo que ves en cada app al cerrar. No mueve dinero, no cambia la caja ni el cuadre de báscula.</p>
              <UnclassifiedWarning data={accounts} />
              <AccountBalanceInputs data={accounts} values={balances} onChange={(id, next) => setBalances(current => ({ ...current, [id]: next }))} disabled={busy} />
            </div>
          </details>
        )}

        <details className="close-block">
          <summary><span className="close-block__n">4</span>Patrimonio de mercancía
            <span className="close-block__hint ui-num">{declared.trim() !== "" && declaredValid ? `Declarado ${money.format(declaredNumber)}` : "Opcional · lo declara el dueño"}</span></summary>
          <div className="close-block__body">
            <PatrimonyLegend />
            {lastDeclared?.last_declared_value != null && (
              <p className="patrimony__coverage">Último valor declarado: <b className="ui-num">{money.format(Number(lastDeclared.last_declared_value))}</b>
                {lastDeclared.last_declared_closed_at ? ` (cierre del ${new Date(lastDeclared.last_declared_closed_at).toLocaleDateString("es-CO")})` : ""}</p>
            )}
            <label className="ui-field"><span>Valor total de la mercancía declarado por el dueño (opcional)</span>
              <input type="number" inputMode="numeric" min="0" step="1" placeholder="$ 0" value={declared} disabled={busy}
                onChange={event => { setDeclared(event.target.value); setError(""); }} /></label>
            {!declaredValid && <p className="ui-field-error" role="alert">Escribe un valor válido (0 o más)</p>}
            <label className="ui-field"><span>Observación (opcional)</span>
              <input value={declaredNote} disabled={busy} placeholder="Ej.: estimado por bodega y góndolas" onChange={event => setDeclaredNote(event.target.value)} /></label>
            {patrimony && (
              <details className="patrimony__diag">
                <summary className="patrimony__coverage">Ver el valor calculado por el POS (no verificado)</summary>
                <RegisteredValuationBlock data={patrimony} />
              </details>
            )}
          </div>
        </details>

        {error && <p className="ui-field-error" role="alert">{error}</p>}
        <div className="ui-form__actions">
          <TouchButton variant="secondary" size="lg" onClick={onClose} disabled={busy}>Cancelar</TouchButton>
          <TouchButton variant="danger" size="lg" disabled={!valid || busy} onClick={() => setConfirming(true)}>Cerrar caja</TouchButton>
        </div>
      </div>

      <ConfirmDialog
        open={confirming} module="caja" tone="danger" title="Confirmar cierre de caja"
        confirmLabel="Confirmar y cerrar" cancelLabel="Revisar" busy={busy}
        onCancel={() => setConfirming(false)} onConfirm={() => void submit()}
      >
        {difference === 0
          ? <span className="cash-confirm-line"><CircleCheck size={20} aria-hidden="true" />La caja cuadra exactamente. Se cerrará la jornada actual.</span>
          : <span className="cash-confirm-line"><CircleAlert size={20} aria-hidden="true" />{diffLabel.toLowerCase()} de <b>{money.format(Math.abs(difference))}</b>. Esta acción cierra la jornada y no se puede deshacer.</span>}
        {accounts && pendingAccounts > 0 && <span className="cash-confirm-line"><CircleAlert size={20} aria-hidden="true" />{ACCOUNTS_PENDING}: {pendingAccounts} cuenta{pendingAccounts === 1 ? "" : "s"} sin saldo. Se puede completar después del cierre.</span>}
      </ConfirmDialog>
    </Sheet>
  );
}
