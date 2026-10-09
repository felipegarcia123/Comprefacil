"use client";

import { useEffect, useMemo, useState } from "react";
import { ArrowLeftRight, Ban, MessageSquarePlus, RefreshCcw, Undo2 } from "lucide-react";
import { ConfirmDialog, InlineMessage, QtyStepper, Sheet, StatusBadge, TouchButton, type Tone } from "@/components/ui";
import { api } from "@/lib/api";
import { INVOICE_STATE } from "@/lib/invoiceStates";
import { cx } from "@/lib/cx";
import { dateTimeShort, errorText, formatQuantity } from "@/lib/format";
import type { PaymentChannel, PostSaleAction, ReturnReason, SaleDetail, SaleRefund } from "@/lib/types";
import { ExchangeFlow, unitSource } from "./ExchangeFlow";
import { AccountPicker, useBusinessAccounts } from "@/components/accounts/AccountPicker";
import { newKey, pesos } from "./postSale";

type Mode = "view" | "exchange" | "return" | "void" | "correct" | "note";

const STATUS_TONE: Record<SaleDetail["display_status"], Tone> = {
  OPEN: "info", CANCELLED: "neutral", COMPLETED: "success", PARTIALLY_RETURNED: "warning", RETURNED: "warning", VOIDED: "danger",
  EXCHANGED: "info", EXCHANGED_AND_RETURNED: "warning",
};
const KIND_LABEL = { PARTIAL: "Devolución parcial", TOTAL: "Devolución total", VOID: "Anulación" } as const;


/**
 * DETALLE DE VENTA (post-venta 0027). The backend decides every rule (allowed_actions + reasons, amounts,
 * channels); this sheet only shows the history and collects the cashier's input. The sale itself is never
 * edited: returns, VOID, corrections and notes are new records.
 */
export function SaleDetailSheet({ saleId, onClose, onChanged }: { saleId: string | null; onClose: () => void; onChanged: () => void }) {
  const [detail, setDetail] = useState<SaleDetail | null>(null);
  const [mode, setMode] = useState<Mode>("view");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState<{ text: string; tone: "success" | "info" } | null>(null);
  const [reasons, setReasons] = useState<ReturnReason[]>([]);
  const [exchangeReasons, setExchangeReasons] = useState<ReturnReason[]>([]);
  const [confirmRefund, setConfirmRefund] = useState<SaleRefund | null>(null);
  const [confirmAccount, setConfirmAccount] = useState("");
  const accounts = useBusinessAccounts();
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setDetail(null); setMode("view"); setError(""); setNotice(null);
    if (!saleId) return;
    api.saleDetail(saleId).then(setDetail).catch(cause => setError(errorText(cause)));
    if (!reasons.length) api.returnReasons().then(setReasons).catch(() => setReasons([]));
    if (!exchangeReasons.length) api.exchangeReasons().then(setExchangeReasons).catch(() => setExchangeReasons([]));
  }, [saleId]); // eslint-disable-line react-hooks/exhaustive-deps

  // A registered electronic refund is NOT money returned yet: never a green "done" message for it.
  function done(next: SaleDetail, message: string) {
    const pending = next.pending_refunds.length > 0 && message.includes("pendiente");
    setDetail(next); setMode("view"); setNotice({ text: message, tone: pending ? "info" : "success" }); setError(""); onChanged();
  }

  async function submitRefundConfirmation() {
    if (!detail || !confirmRefund) return;
    setBusy(true);
    try {
      const account = confirmRefund.account_id ? undefined : confirmAccount || undefined;
      const next = confirmRefund.kind === "EXCHANGE" && confirmRefund.exchange_id
        ? await api.confirmExchangeRefund(detail.id, confirmRefund.exchange_id, account)
        : await api.confirmRefund(detail.id, confirmRefund.id, account);
      done(next, `${confirmRefund.kind === "EXCHANGE" ? "Devolución de la diferencia" : "Reembolso"} por ${confirmRefund.channel_name} confirmada`);
      setConfirmRefund(null);
    }
    catch (cause) { setError(errorText(cause)); setConfirmRefund(null); }
    finally { setBusy(false); }
  }

  const title = detail ? `Venta ${detail.number}` : "Detalle de venta";
  return (
    <Sheet open={saleId !== null} title={title} module="caja" wide persistent={mode !== "view"} onClose={onClose}>
      <div className="sale-detail">
        {!detail && !error && <p className="ui-empty-hint">Cargando venta…</p>}
        {error && <InlineMessage tone="danger" onDismiss={() => setError("")}>{error}</InlineMessage>}
        {notice && <InlineMessage tone={notice.tone} onDismiss={() => setNotice(null)}>{notice.text}</InlineMessage>}
        {detail && mode === "view" && <DetailView detail={detail} onAction={next => { setNotice(null); setMode(next); }} onConfirmRefund={refund => { setConfirmAccount(""); setConfirmRefund(refund); }} />}
        {detail && mode === "exchange" && <ExchangeFlow detail={detail} reasons={exchangeReasons} onCancel={() => setMode("view")} onDone={done} />}
        {detail && mode === "return" && <ReturnForm detail={detail} reasons={reasons} onCancel={() => setMode("view")} onDone={done} />}
        {detail && mode === "void" && <VoidForm detail={detail} reasons={reasons} onCancel={() => setMode("view")} onDone={done} />}
        {detail && mode === "correct" && <CorrectionForm detail={detail} onCancel={() => setMode("view")} onDone={done} />}
        {detail && mode === "note" && <NoteForm detail={detail} onCancel={() => setMode("view")} onDone={done} />}
      </div>
      <ConfirmDialog open={confirmRefund !== null} module="caja" title="Confirmar reembolso" confirmLabel="Confirmar reembolso" busy={busy}
        onCancel={() => setConfirmRefund(null)} onConfirm={() => void submitRefundConfirmation()}>
        {confirmRefund && <p>Confirma que ya devolviste <b className="ui-num">{pesos(confirmRefund.amount)}</b> por {confirmRefund.channel_name}{confirmRefund.account_name ? ` desde ${confirmRefund.account_name}` : ""}. El POS solo lo registra: no envía dinero.</p>}
        {confirmRefund && !confirmRefund.affects_cash && !confirmRefund.account_id && (
          <AccountPicker accounts={accounts} value={confirmAccount} onChange={setConfirmAccount} label="¿De qué cuenta salió?" missing={Boolean(confirmRefund.requires_account) && !confirmAccount} />
        )}
      </ConfirmDialog>
    </Sheet>
  );
}

// ---- view ------------------------------------------------------------------------------------------------

function DetailView({ detail, onAction, onConfirmRefund }: { detail: SaleDetail; onAction: (mode: Mode) => void; onConfirmRefund: (refund: SaleRefund) => void }) {
  const actions = detail.allowed_actions;
  // The business's usual post-sale case is a PRODUCT EXCHANGE: it is the main action; giving money back is the exception.
  const primary = { mode: "exchange" as Mode, label: "Cambiar producto", action: actions.exchange };
  const buttons: { mode: Mode; label: string; icon: typeof Undo2; action: PostSaleAction; variant: "secondary" | "danger" }[] = [
    { mode: "return", label: "Devolución de dinero", icon: Undo2, action: actions.return, variant: "secondary" },
    { mode: "correct", label: "Corregir pago", icon: RefreshCcw, action: actions.correct_payment, variant: "secondary" },
    { mode: "note", label: "Agregar aclaración", icon: MessageSquarePlus, action: actions.note, variant: "secondary" },
    { mode: "void", label: "Anular venta", icon: Ban, action: actions.void, variant: "danger" },
  ];
  // One explanation per distinct reason (the invoice reason usually blocks three actions at once).
  const blockedBy = new Map<string, string[]>();
  [primary, ...buttons].filter(b => !b.action.allowed && b.action.reason).forEach(b => blockedBy.set(b.action.reason as string, [...(blockedBy.get(b.action.reason as string) ?? []), b.label]));
  const listJoin = (labels: string[]) => labels.length > 1 ? `${labels.slice(0, -1).join(", ")} y ${labels[labels.length - 1]}` : labels[0];
  const blocked = Array.from(blockedBy, ([reason, labels]) => ({ reason, labels: listJoin(labels) }));
  const returned = Number(detail.returned_total) > 0;

  return (
    <div className="ui-stack">
      <div className="sale-detail__summary">
        <div>
          <StatusBadge tone={STATUS_TONE[detail.display_status]}>{detail.display_status_label}</StatusBadge>
          <p className="sale-detail__meta">
            {detail.completed_at ? dateTimeShort.format(new Date(detail.completed_at)) : dateTimeShort.format(new Date(detail.created_at))}
            {detail.customer && <> · {detail.customer.name}</>}
          </p>
        </div>
        <dl className="sale-detail__totals">
          <div><dt>{detail.external_items.length ? "Total POS" : "Total"}</dt><dd className="ui-num">{pesos(detail.total)}</dd></div>
          {returned && <div><dt>Devuelto</dt><dd className="ui-num sale-detail__out">−{pesos(detail.returned_total)}</dd></div>}
          {returned && <div><dt>Neto</dt><dd className="ui-num">{pesos(detail.net_total)}</dd></div>}
        </dl>
      </div>

      {detail.pending_refunds.map(refund => (
        <InlineMessage key={refund.id} tone="warning" title={`Pendiente devolver ${pesos(refund.amount)} por ${refund.channel_name}${refund.kind === "EXCHANGE" ? " (diferencia de un cambio)" : ""}`}
          actions={<TouchButton module="caja" onClick={() => onConfirmRefund(refund)}>Confirmar reembolso</TouchButton>}>
          Devuélvelo por {refund.channel_name} y luego confírmalo aquí.
        </InlineMessage>
      ))}

      <section aria-label="Productos" className="sale-detail__section">
        <h3>Productos</h3>
        <ul className="sale-detail__list">
          {detail.lines.map(line => (
            <li key={line.id}>
              <span>{line.product_name}
                <small>{formatQuantity(line.quantity)} × {pesos(line.unit_price)}
                  {Number(line.exchanged_quantity) > 0 && <> · cambiado {formatQuantity(line.exchanged_quantity)}</>}
                  {Number(line.returned_quantity) > 0 && <> · devuelto {formatQuantity(line.returned_quantity)}</>}
                  {line.is_granel && <> · granel</>}
                </small>
              </span>
              <b className="ui-num">{pesos(line.line_total)}</b>
            </li>
          ))}
          {detail.external_items.map((item, index) => (
            <li key={`ext-${index}`} className="sale-detail__external">
              <span>{item.description}<small>Cobrado en la báscula · fuera del POS</small></span>
              <b className="ui-num">{pesos(item.amount)}</b>
            </li>
          ))}
        </ul>
      </section>

      <section aria-label="Pagos" className="sale-detail__section">
        <h3>Pagos</h3>
        <ul className="sale-detail__list">
          {detail.payments.map(payment => (
            <li key={payment.id}>
              <span>
                <span className="sale-detail__channel">{payment.effective_channel_name}{payment.corrected && <StatusBadge tone="info">Corregido</StatusBadge>}</span>
                {payment.effective_account_name && <small>Llegó a {payment.effective_account_name}</small>}
                {payment.corrected && <small>Original: {payment.original_channel_name}{payment.original_account_name ? ` · ${payment.original_account_name}` : ""}</small>}
                {payment.corrections.map(correction => (
                  <span key={correction.id} className="sale-detail__correction">
                    <small>{payment.corrections.length > 1 && `${correction.from_channel_name} → ${correction.to_channel_name} · `}{correction.from_account_name !== correction.to_account_name && (correction.from_account_name || correction.to_account_name) ? `Cuenta: ${correction.from_account_name ?? "sin cuenta"} → ${correction.to_account_name ?? "sin cuenta"} · ` : ""}Motivo: {correction.reason}</small>
                    <small>Corregido {dateTimeShort.format(new Date(correction.created_at))}
                      {Number(correction.cash_effect) !== 0 && ` · efectivo del cajón ${Number(correction.cash_effect) > 0 ? "+" : "−"}${pesos(Math.abs(Number(correction.cash_effect)))}`}</small>
                  </span>
                ))}
              </span>
              <b className="ui-num">{pesos(payment.amount)}</b>
            </li>
          ))}
        </ul>
      </section>

      {detail.invoice && (
        <InlineMessage tone="info" title="Factura electrónica">
          {INVOICE_STATE[detail.invoice.display_status as keyof typeof INVOICE_STATE]?.label ?? detail.invoice.display_status}{detail.invoice.provider_number && ` · ${detail.invoice.provider_number}`}
        </InlineMessage>
      )}

      {detail.exchanges.length > 0 && (
        <section aria-label="Cambios de producto" className="sale-detail__section">
          <h3>Cambios de producto</h3>
          <ul className="sale-detail__list">
            {detail.exchanges.map(exchange => (
              <li key={exchange.id}>
                <span>Cambio {exchange.number}
                  <small>{dateTimeShort.format(new Date(exchange.created_at))} · {exchange.reason_label}{exchange.reason_text && `: ${exchange.reason_text}`}</small>
                  <small>Devuelve: {exchange.returned.map(line => `${formatQuantity(line.quantity)} × ${line.product_name ?? "producto"}${line.origin && line.origin !== "Venta" ? ` (de ${line.origin})` : ""}${line.restock ? "" : " (no vuelve al inventario)"}`).join(", ")} · {pesos(exchange.returned_credit)}</small>
                  <small>Se lleva: {exchange.replacements.map(line => `${formatQuantity(line.quantity)} × ${line.product_name}${Number(line.later_consumed_quantity) > 0 ? ` (luego cambiado/devuelto ${formatQuantity(line.later_consumed_quantity)})` : ""}`).join(", ")} · {pesos(exchange.replacement_total)}</small>
                  <span className="sale-detail__refund">
                    {exchange.settlement === "EVEN" && <StatusBadge tone="neutral">Sin diferencia por pagar</StatusBadge>}
                    {exchange.settlement === "CUSTOMER_PAYS" && <StatusBadge tone="success">Diferencia cobrada</StatusBadge>}
                    {exchange.settlement === "STORE_REFUNDS" && (exchange.money?.status === "PENDING"
                      ? <StatusBadge tone="warning">Diferencia pendiente de devolver</StatusBadge>
                      : <StatusBadge tone="success">Diferencia devuelta</StatusBadge>)}
                    {exchange.money && <small>{pesos(exchange.money.amount)} · {exchange.money.channel_name}{exchange.money.confirmed_at && exchange.settlement === "STORE_REFUNDS" && !exchange.money.affects_cash ? ` · ${dateTimeShort.format(new Date(exchange.money.confirmed_at))}` : ""}</small>}
                  </span>
                </span>
                <b className={cx("ui-num", exchange.settlement === "STORE_REFUNDS" && "sale-detail__out")}>
                  {exchange.settlement === "CUSTOMER_PAYS" ? `+${pesos(exchange.settlement_amount)}` : exchange.settlement === "STORE_REFUNDS" ? `−${pesos(exchange.settlement_amount)}` : pesos(0)}</b>
              </li>
            ))}
          </ul>
        </section>
      )}

      {detail.returns.length > 0 && (
        <section aria-label="Devoluciones" className="sale-detail__section">
          <h3>{detail.returns.some(r => r.kind === "VOID") ? "Anulación" : "Devoluciones"}</h3>
          <ul className="sale-detail__list">
            {detail.returns.map(item => (
              <li key={item.id}>
                <span>{KIND_LABEL[item.kind]}
                  <small>{dateTimeShort.format(new Date(item.created_at))} · {item.reason_label}{item.reason_text && `: ${item.reason_text}`}</small>
                  {item.refunds.map(refund => (
                    <span key={refund.id} className="sale-detail__refund">
                      {refund.status === "PENDING" ? <StatusBadge tone="warning">Pendiente de reembolso</StatusBadge>
                        : refund.affects_cash ? <StatusBadge tone="success">Entregado en efectivo</StatusBadge>
                        : <StatusBadge tone="success">Reembolso confirmado</StatusBadge>}
                      <small>{pesos(refund.amount)} · {refund.channel_name}{refund.account_name ? ` · ${refund.account_name}` : ""}{refund.status === "COMPLETED" && !refund.affects_cash && refund.confirmed_at && ` · ${dateTimeShort.format(new Date(refund.confirmed_at))}`}</small>
                    </span>
                  ))}
                </span>
                <b className="ui-num sale-detail__out">−{pesos(item.total_amount)}</b>
              </li>
            ))}
          </ul>
        </section>
      )}

      {detail.notes.length > 0 && (
        <section aria-label="Aclaraciones" className="sale-detail__section">
          <h3>Aclaraciones</h3>
          <ul className="sale-detail__list">
            {detail.notes.map(note => <li key={note.id}><span>{note.text}<small>{dateTimeShort.format(new Date(note.created_at))}</small></span></li>)}
          </ul>
        </section>
      )}

      <div className="sale-detail__footer">
      <TouchButton size="lg" module="caja" icon={ArrowLeftRight} block disabled={!primary.action.allowed}
        onClick={() => onAction("exchange")}>{primary.label}</TouchButton>
      <div className="sale-detail__actions">
        {buttons.map(button => (
          <TouchButton key={button.mode} size="lg" variant={button.variant} module="caja" icon={button.icon}
            disabled={!button.action.allowed} onClick={() => onAction(button.mode)}>{button.label}</TouchButton>
        ))}
      </div>
      {blocked.map(item => <p key={item.reason} className="sale-detail__blocked"><b>{item.labels}:</b> {item.reason}</p>)}
      </div>
    </div>
  );
}

// ---- shared pieces -----------------------------------------------------------------------------------------

function ReasonPicker({ reasons, value, onChange, text, onText }: { reasons: ReturnReason[]; value: string; onChange: (code: string) => void; text: string; onText: (text: string) => void }) {
  return (
    <div className="ui-field"><span>Motivo *</span>
      <div className="sale-detail__reasons" role="group" aria-label="Motivo">
        {reasons.map(reason => (
          <button key={reason.code} type="button" aria-pressed={value === reason.code}
            className={cx("ui-choice__item", value === reason.code && "ui-choice__item--on")} onClick={() => onChange(reason.code)}>{reason.label}</button>
        ))}
      </div>
      {value === "OTHER" && <textarea rows={2} aria-label="Describe el motivo" placeholder="Describe el motivo" value={text} onChange={event => onText(event.target.value)} />}
    </div>
  );
}

type FormProps = { detail: SaleDetail; onCancel: () => void; onDone: (detail: SaleDetail, message: string) => void };

// ---- return ------------------------------------------------------------------------------------------------

function ReturnForm({ detail, reasons, onCancel, onDone }: FormProps & { reasons: ReturnReason[] }) {
  const lines = detail.units.filter(line => Number(line.returnable_quantity) > 0);
  const [quantities, setQuantities] = useState<Record<string, number>>({});
  const [restock, setRestock] = useState<Record<string, boolean>>(() => Object.fromEntries(lines.map(line => [line.key, line.restockable])));
  const [reason, setReason] = useState("");
  const [text, setText] = useState("");
  // Money only goes back through a channel the sale was really paid with (effective channel).
  const channels = useMemo(() => {
    const seen = new Map<string, { id: string; name: string; cash: boolean }>();
    detail.payments.forEach(p => seen.set(p.effective_channel_id, { id: p.effective_channel_id, name: p.effective_channel_name, cash: p.effective_affects_cash }));
    // Money that came in as an exchange difference can also go back through its channel.
    detail.exchanges.forEach(e => { if (e.money?.direction === "IN") seen.set(e.money.channel_id, { id: e.money.channel_id, name: e.money.channel_name, cash: e.money.affects_cash }); });
    return Array.from(seen.values());
  }, [detail]);
  const [channelId, setChannelId] = useState(channels.length === 1 ? channels[0].id : "");
  const accounts = useBusinessAccounts();
  // Default: the account that channel's money landed in (if exactly one); the cashier can change it.
  const defaultAccount = (id: string) => {
    const known = new Set(detail.payments.filter(p => p.effective_channel_id === id && p.effective_account_id).map(p => p.effective_account_id!));
    return known.size === 1 ? [...known][0] : "";
  };
  const [accountId, setAccountId] = useState(channels.length === 1 ? defaultAccount(channels[0].id) : "");
  const [key] = useState(newKey);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const selected = lines.filter(line => (quantities[line.key] ?? 0) > 0);
  // Preview only — the backend computes the exact amount (and the rest of a line without rounding drift).
  const amount = selected.reduce((sum, line) => sum + quantities[line.key] * Number(line.unit_price), 0);
  const channel = channels.find(c => c.id === channelId);
  const valid = selected.length > 0 && reason !== "" && (reason !== "OTHER" || text.trim().length >= 3) && channel !== undefined;

  async function submit() {
    setBusy(true); setError("");
    try {
      const next = await api.createReturn(detail.id, {
        lines: selected.map(line => ({ ...unitSource(line), quantity: quantities[line.key], restock: line.restockable && restock[line.key] })),
        reason_code: reason, reason_text: reason === "OTHER" ? text.trim() : null, refund_channel_id: channelId, idempotency_key: key,
        refund_account_id: channel && !channel.cash && accountId ? accountId : null,
      });
      onDone(next, channel?.cash ? `Devolución registrada: entrega ${pesos(amount)} en efectivo` : `Devolución registrada: pendiente devolver ${pesos(amount)} por ${channel?.name}`);
    } catch (cause) { setError(errorText(cause)); setConfirming(false); }
    finally { setBusy(false); }
  }

  return (
    <div className="ui-form">
      <h3 className="sale-detail__form-title">Devolución</h3>
      <ul className="sale-detail__list sale-detail__list--form">
        {lines.map(line => (
          <li key={line.key}>
            <span>{line.product_name}
              <small>Quedan {formatQuantity(line.returnable_quantity)} · {pesos(line.unit_price)} c/u{!line.restockable && (line.is_granel ? " · granel: solo se devuelve el dinero" : " · producto manual: solo se devuelve el dinero")}</small>
              {line.restockable && (quantities[line.key] ?? 0) > 0 && (
                <label className="sale-detail__check">
                  <input type="checkbox" checked={restock[line.key]} onChange={event => setRestock(current => ({ ...current, [line.key]: event.target.checked }))} />
                  Vuelve al inventario
                </label>
              )}
            </span>
            <QtyStepper label={line.product_name} value={quantities[line.key] ?? 0}
              onChange={value => setQuantities(current => ({ ...current, [line.key]: Math.max(0, Math.min(value, Number(line.returnable_quantity))) }))} />
          </li>
        ))}
      </ul>
      <ReasonPicker reasons={reasons} value={reason} onChange={setReason} text={text} onText={setText} />
      {channels.length > 1 && (
        <div className="ui-field"><span>Devolver el dinero por *</span>
          <div className="ui-choice" role="group" aria-label="Medio de devolución">
            {channels.map(c => <button key={c.id} type="button" aria-pressed={channelId === c.id} className={cx("ui-choice__item", channelId === c.id && "ui-choice__item--on")} onClick={() => { setChannelId(c.id); setAccountId(defaultAccount(c.id)); }}>{c.name}</button>)}
          </div>
        </div>
      )}
      {channel && !channel.cash && accounts.length > 0 && (
        <AccountPicker accounts={accounts} value={accountId} onChange={setAccountId} label="Sale de" />
      )}
      <div className="sale-detail__formfoot">
      <div className="sale-detail__amount"><span>A devolver{channel && ` por ${channel.name}`}</span><b className="ui-num">{pesos(amount)}</b></div>
      {error && <p className="ui-field-error" role="alert">{error}</p>}
      <div className="ui-form__actions">
        <TouchButton variant="secondary" size="lg" onClick={onCancel} disabled={busy}>Volver</TouchButton>
        <TouchButton size="lg" module="caja" disabled={!valid || busy} onClick={() => setConfirming(true)}>Registrar devolución</TouchButton>
      </div>
      </div>
      <ConfirmDialog open={confirming} module="caja" title="Registrar devolución" confirmLabel="Registrar devolución" busy={busy}
        onCancel={() => setConfirming(false)} onConfirm={() => void submit()}>
        <ul className="sale-detail__confirm-list">
          {selected.map(line => (
            <li key={line.key}><b>{formatQuantity(quantities[line.key])} × {line.product_name}</b>
              <span>{!line.restockable ? "Solo dinero: no maneja inventario" : restock[line.key] ? "Vuelve al inventario" : "No vuelve al inventario"}</span></li>
          ))}
        </ul>
        <p>Se devuelven <b className="ui-num">{pesos(amount)}</b>{channel && ` por ${channel.name}`}.</p>
        <p>{channel?.cash ? "Entrega el dinero del cajón: el efectivo esperado baja en ese valor." : "Quedará pendiente hasta que confirmes el reembolso."}</p>
      </ConfirmDialog>
    </div>
  );
}

// ---- void --------------------------------------------------------------------------------------------------

function VoidForm({ detail, reasons, onCancel, onDone }: FormProps & { reasons: ReturnReason[] }) {
  const [reason, setReason] = useState("");
  const [text, setText] = useState("");
  const [restock, setRestock] = useState(true);
  const [key] = useState(newKey);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const restockable = detail.lines.some(line => line.restockable);
  const valid = reason !== "" && (reason !== "OTHER" || text.trim().length >= 3);

  async function submit() {
    setBusy(true); setError("");
    try {
      const next = await api.voidSale(detail.id, { reason_code: reason, reason_text: reason === "OTHER" ? text.trim() : null, restock, idempotency_key: key });
      onDone(next, next.pending_refunds.length ? "Venta anulada: queda pendiente devolver el dinero electrónico" : "Venta anulada");
    } catch (cause) { setError(errorText(cause)); setConfirming(false); }
    finally { setBusy(false); }
  }

  return (
    <div className="ui-form">
      <h3 className="sale-detail__form-title">Anular venta</h3>
      <p className="sale-detail__meta">Se devuelve todo y cada pago vuelve por su medio. La venta queda en el historial como Anulada.</p>
      <ul className="sale-detail__list">
        {detail.payments.map(p => (
          <li key={p.id}><span>{p.effective_channel_name}<small>{p.effective_affects_cash ? "Sale del cajón ahora" : "Queda pendiente de devolver"}</small></span><b className="ui-num">{pesos(p.amount)}</b></li>
        ))}
      </ul>
      {restockable && (
        <label className="sale-detail__check">
          <input type="checkbox" checked={restock} onChange={event => setRestock(event.target.checked)} />
          Los productos vuelven al inventario
        </label>
      )}
      <ReasonPicker reasons={reasons} value={reason} onChange={setReason} text={text} onText={setText} />
      <div className="sale-detail__formfoot">
      {error && <p className="ui-field-error" role="alert">{error}</p>}
      <div className="ui-form__actions">
        <TouchButton variant="secondary" size="lg" onClick={onCancel} disabled={busy}>Volver</TouchButton>
        <TouchButton size="lg" variant="danger" disabled={!valid || busy} onClick={() => setConfirming(true)}>Anular venta</TouchButton>
      </div>
      </div>
      <ConfirmDialog open={confirming} module="caja" tone="danger" title={`Anular venta ${detail.number}`} confirmLabel="Anular venta" busy={busy}
        onCancel={() => setConfirming(false)} onConfirm={() => void submit()}>
        <p>Esta acción reversa la venta y no se puede deshacer. La venta original permanece en el historial como Anulada.</p>
        <dl className="sale-detail__void-summary">
          <div><dt>Total</dt><dd className="ui-num">{pesos(detail.total)}</dd></div>
          {detail.payments.map(p => (
            <div key={p.id}><dt>Devolver por {p.effective_channel_name}</dt>
              <dd className="ui-num">{pesos(p.amount)}<small>{p.effective_affects_cash ? "sale del cajón ahora" : "queda pendiente"}</small></dd></div>
          ))}
          <div><dt>Vuelven al inventario</dt><dd>{restockable && restock ? detail.lines.filter(l => l.restockable).map(l => <span key={l.id}>{formatQuantity(l.quantity)} × {l.product_name}</span>) : "Nada"}</dd></div>
        </dl>
      </ConfirmDialog>
    </div>
  );
}

// ---- payment correction -----------------------------------------------------------------------------------

function CorrectionForm({ detail, onCancel, onDone }: FormProps) {
  const [paymentId, setPaymentId] = useState(detail.payments.length === 1 ? detail.payments[0].id : "");
  const [channels, setChannels] = useState<PaymentChannel[]>([]);
  const [targetId, setTargetId] = useState("");
  const [accountId, setAccountId] = useState("");
  const accounts = useBusinessAccounts();
  const [reason, setReason] = useState("");
  const [key] = useState(newKey);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => { api.paymentChannels(true).then(setChannels).catch(cause => setError(errorText(cause))); }, []);

  const payment = detail.payments.find(p => p.id === paymentId);
  // Same channel is offered too: the money may have landed in another account (0028). A payment without a known
  // account (before the account control) is never assigned one retroactively.
  const tracked = Boolean(payment?.effective_affects_cash || payment?.effective_account_id);
  const options = channels.filter(c => c.id !== payment?.effective_channel_id || (tracked && !c.affects_cash));
  const target = channels.find(c => c.id === targetId);
  const pickTarget = (c: PaymentChannel) => { setTargetId(c.id); setAccountId(payment?.effective_account_id ?? c.default_account_id ?? ""); };
  const needsAccount = Boolean(target && !target.affects_cash && tracked && target.requires_destination_account);
  const accountChanged = Boolean(target) && target!.id === payment?.effective_channel_id ? accountId !== (payment?.effective_account_id ?? "") : true;
  const cashEffect = !payment || !target ? 0
    : payment.effective_affects_cash && !target.affects_cash ? -Number(payment.amount)
    : !payment.effective_affects_cash && target.affects_cash ? Number(payment.amount) : 0;
  const valid = payment !== undefined && target !== undefined && reason.trim().length >= 3 && (!needsAccount || Boolean(accountId)) && accountChanged;

  async function submit() {
    if (!payment || !target) return;
    setBusy(true); setError("");
    const toAccount = !target.affects_cash && tracked && accountId ? accountId : undefined;
    try { onDone(await api.correctPayment(detail.id, payment.id, { to_channel_id: target.id, to_account_id: toAccount, reason: reason.trim(), idempotency_key: key }), `Pago corregido a ${target.name}${toAccount ? ` · ${accounts.find(a => a.id === toAccount)?.name ?? ""}` : ""}`); }
    catch (cause) { setError(errorText(cause)); setConfirming(false); }
    finally { setBusy(false); }
  }

  return (
    <div className="ui-form">
      <h3 className="sale-detail__form-title">Corregir pago</h3>
      <p className="sale-detail__meta">El pago original se conserva; la corrección queda en el historial con su motivo.</p>
      {detail.payments.length > 1 && (
        <div className="ui-field"><span>Pago *</span>
          <div className="ui-choice" role="group" aria-label="Pago a corregir">
            {detail.payments.map(p => <button key={p.id} type="button" aria-pressed={paymentId === p.id} className={cx("ui-choice__item", paymentId === p.id && "ui-choice__item--on")} onClick={() => { setPaymentId(p.id); setTargetId(""); }}>{p.effective_channel_name}<small>{pesos(p.amount)}</small></button>)}
          </div>
        </div>
      )}
      {payment && (
        <div className="ui-field"><span>Pago de {pesos(payment.amount)} registrado como {payment.effective_channel_name}. ¿Por qué medio fue realmente? *</span>
          <div className="sale-detail__reasons" role="group" aria-label="Medio correcto">
            {options.map(c => {
              const movesCash = c.affects_cash !== payment.effective_affects_cash;
              const blocked = movesCash && !detail.allowed_actions.correct_payment_cash.allowed;
              return <button key={c.id} type="button" disabled={blocked} aria-pressed={targetId === c.id} className={cx("ui-choice__item", targetId === c.id && "ui-choice__item--on")} onClick={() => pickTarget(c)}>{c.id === payment.effective_channel_id ? `${c.name} (otra cuenta)` : c.name}</button>;
            })}
          </div>
          {!detail.allowed_actions.correct_payment_cash.allowed && options.some(c => c.affects_cash !== payment.effective_affects_cash) && (
            <small className="sale-detail__meta">No disponible {payment.effective_affects_cash ? "pasar a un medio electrónico" : "pasar a Efectivo"}: {detail.allowed_actions.correct_payment_cash.reason}</small>
          )}
        </div>
      )}
      {target && !target.affects_cash && tracked && accounts.length > 0 && (
        <AccountPicker accounts={accounts} value={accountId} onChange={setAccountId} label="Llegó a" missing={needsAccount && !accountId} />
      )}
      {target && !target.affects_cash && !tracked && <p className="sale-detail__meta">Este pago no tiene cuenta destino registrada (es anterior al control de cuentas): la corrección no le asigna una.</p>}
      <label className="ui-field"><span>Motivo *</span>
        <textarea rows={2} value={reason} onChange={event => setReason(event.target.value)} placeholder="Ej.: el cliente pagó por Daviplata, no por Nequi" />
      </label>
      {target && (
        <p className="sale-detail__meta">
          {cashEffect === 0 ? "No cambia el efectivo del cajón." : cashEffect < 0 ? `El efectivo esperado baja ${pesos(-cashEffect)}.` : `El efectivo esperado sube ${pesos(cashEffect)}.`}
        </p>
      )}
      <div className="sale-detail__formfoot">
      {error && <p className="ui-field-error" role="alert">{error}</p>}
      <div className="ui-form__actions">
        <TouchButton variant="secondary" size="lg" onClick={onCancel} disabled={busy}>Volver</TouchButton>
        <TouchButton size="lg" module="caja" disabled={!valid || busy} onClick={() => setConfirming(true)}>Corregir pago</TouchButton>
      </div>
      </div>
      <ConfirmDialog open={confirming} module="caja" title="Corregir pago" confirmLabel="Corregir pago" busy={busy}
        onCancel={() => setConfirming(false)} onConfirm={() => void submit()}>
        {payment && target && <>
          <p>El pago de <b className="ui-num">{pesos(payment.amount)}</b> pasa de {payment.effective_channel_name} a {target.name}. No es una devolución: el cliente no recibe dinero.</p>
          <p>{cashEffect === 0 ? "No cambia el efectivo del cajón." : cashEffect < 0 ? `El efectivo esperado del cajón baja ${pesos(-cashEffect)}.` : `El efectivo esperado del cajón sube ${pesos(cashEffect)}.`}</p>
        </>}
      </ConfirmDialog>
    </div>
  );
}

// ---- note --------------------------------------------------------------------------------------------------

function NoteForm({ detail, onCancel, onDone }: FormProps) {
  const [text, setText] = useState("");
  const [key] = useState(newKey);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit() {
    setBusy(true); setError("");
    try { onDone(await api.addSaleNote(detail.id, text.trim(), key), "Aclaración agregada"); }
    catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  }

  return (
    <div className="ui-form">
      <h3 className="sale-detail__form-title">Agregar aclaración</h3>
      <label className="ui-field"><span>Aclaración *</span>
        <textarea autoFocus rows={3} maxLength={500} value={text} onChange={event => setText(event.target.value)} />
      </label>
      <p className="sale-detail__meta">No cambia dinero, inventario, pagos ni factura. No se puede editar ni borrar.</p>
      <div className="sale-detail__formfoot">
      {error && <p className="ui-field-error" role="alert">{error}</p>}
      <div className="ui-form__actions">
        <TouchButton variant="secondary" size="lg" onClick={onCancel} disabled={busy}>Volver</TouchButton>
        <TouchButton size="lg" module="caja" disabled={text.trim().length < 3 || busy} loading={busy} onClick={() => void submit()}>Guardar aclaración</TouchButton>
      </div>
      </div>
    </div>
  );
}
