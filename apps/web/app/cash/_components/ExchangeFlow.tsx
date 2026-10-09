"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { PackagePlus, Plus, Trash2 } from "lucide-react";
import { ConfirmDialog, InlineMessage, QtyStepper, SearchBar, TouchButton } from "@/components/ui";
import { AccountPicker } from "@/components/accounts/AccountPicker";
import { api } from "@/lib/api";
import { cx } from "@/lib/cx";
import { errorText, formatQuantity } from "@/lib/format";
import type { BusinessAccount, ExchangePreview, ExchangeReplacementInput, PaymentChannel, Product, ReturnReason, SaleDetail, SaleUnit, UnitSource } from "@/lib/types";
import { newKey, pesos } from "./postSale";

type Step = "returned" | "replacement" | "summary";
type Replacement = { key: string; name: string; unit_price: number; quantity: number; product_id?: string; manual_kind?: "SUPER" | "BULTO" | "PET_OTHER" };
const MANUAL_KINDS: { kind: "SUPER" | "BULTO" | "PET_OTHER"; label: string }[] = [
  { kind: "SUPER", label: "Súper" }, { kind: "BULTO", label: "Bulto" }, { kind: "PET_OTHER", label: "Otro mascota" }];
const SCAN_CODE = /^\d{8,14}$/;

export const unitSource = (unit: SaleUnit): UnitSource =>
  unit.source_type === "SALE_ITEM" ? { sale_item_id: unit.source_id } : { replacement_line_id: unit.source_id };

/**
 * CAMBIAR PRODUCTO — the business's usual post-sale case ("devuelve A, se lleva B"). Three steps:
 * what comes back (any unit the customer holds: from the sale or from an earlier exchange, credited at the
 * value it was delivered with), what the customer takes (current price; scanner or search like the POS)
 * and the summary computed by the backend. Same value → no money; more expensive → only the difference is
 * charged; cheaper → the normal next step is to add another product, giving the difference back is the
 * secondary, explicit choice (no store credit).
 */
export function ExchangeFlow({ detail, reasons, onCancel, onDone }: { detail: SaleDetail; reasons: ReturnReason[]; onCancel: () => void; onDone: (next: SaleDetail, message: string) => void }) {
  const units = detail.units.filter(unit => Number(unit.returnable_quantity) > 0);
  const [step, setStep] = useState<Step>("returned");
  const [quantities, setQuantities] = useState<Record<string, number>>({});
  const [restock, setRestock] = useState<Record<string, boolean>>(() => Object.fromEntries(units.map(unit => [unit.key, unit.restockable])));
  const [reason, setReason] = useState("");
  const [reasonText, setReasonText] = useState("");
  const [cart, setCart] = useState<Replacement[]>([]);
  const [preview, setPreview] = useState<ExchangePreview | null>(null);
  const [refunding, setRefunding] = useState(false);              // the cashier chose "Devolver $X"
  const [channels, setChannels] = useState<PaymentChannel[]>([]);
  const [channelId, setChannelId] = useState("");
  const [accounts, setAccounts] = useState<BusinessAccount[]>([]);
  const [accountId, setAccountId] = useState("");
  const [received, setReceived] = useState("");
  const [key] = useState(newKey);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const selected = units.filter(unit => (quantities[unit.key] ?? 0) > 0);
  const credit = selected.reduce((sum, unit) => sum + quantities[unit.key] * Number(unit.unit_price), 0);
  const newTotal = cart.reduce((sum, item) => sum + item.quantity * item.unit_price, 0);
  const returnLines = selected.map(unit => ({ ...unitSource(unit), quantity: quantities[unit.key], restock: unit.restockable && restock[unit.key] }));
  const replacementLines: ExchangeReplacementInput[] = cart.map(item => item.product_id
    ? { product_id: item.product_id, quantity: item.quantity }
    : { manual_kind: item.manual_kind, description: item.name, amount: item.unit_price, quantity: item.quantity });
  const step1Valid = selected.length > 0 && reason !== "" && (reason !== "OTHER" || reasonText.trim().length >= 3);

  useEffect(() => {
    api.paymentChannels(true).then(setChannels).catch(() => setChannels([]));
    api.businessAccounts(true).then(setAccounts).catch(() => setAccounts([]));
  }, []);
  const pickChannel = (id: string) => { setChannelId(id); setAccountId(channels.find(c => c.id === id)?.default_account_id ?? ""); };

  async function toSummary() {
    setBusy(true); setError(""); setRefunding(false); setChannelId(""); setReceived("");
    try { setPreview(await api.previewExchange(detail.id, { return_lines: returnLines, replacement_lines: replacementLines })); setStep("summary"); }
    catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  }

  const difference = preview && step === "summary" ? Number(preview.difference) : newTotal - credit;
  const owed = Math.abs(difference);
  const channel = channels.find(c => c.id === channelId);
  const receivedValue = received.trim() === "" ? owed : Number(received);
  const needsChannel = difference > 0 || (difference < 0 && refunding);
  // 0028: an electronic difference says which business account it entered / left from.
  const needsAccount = Boolean(channel && !channel.affects_cash && channel.requires_destination_account);
  const paymentValid = !needsChannel || (channel !== undefined && (difference < 0 || !channel.affects_cash || receivedValue >= owed) && (!needsAccount || Boolean(accountId)));

  async function submit() {
    setBusy(true); setError("");
    try {
      const next = await api.createExchange(detail.id, {
        return_lines: returnLines, replacement_lines: replacementLines, reason_code: reason,
        reason_text: reason === "OTHER" ? reasonText.trim() : null, idempotency_key: key,
        settlement: needsChannel && channel ? { channel_id: channel.id, received_amount: difference > 0 && channel.affects_cash ? receivedValue : undefined,
          account_id: !channel.affects_cash && accountId ? accountId : undefined } : null,
      });
      onDone(next, difference > 0 ? `Cambio registrado: diferencia de ${pesos(owed)} cobrada por ${channel?.name}`
        : difference < 0 ? (channel?.affects_cash ? `Cambio registrado: entrega ${pesos(owed)} en efectivo` : `Cambio registrado: pendiente devolver ${pesos(owed)} por ${channel?.name}`)
        : "Cambio registrado sin diferencia por pagar");
    } catch (cause) { setError(errorText(cause)); setConfirming(false); }
    finally { setBusy(false); }
  }

  const addMore = () => { setPreview(null); setRefunding(false); setStep("replacement"); };

  return (
    <div className="ui-form exchange">
      <ol className="exchange__steps" aria-label="Pasos del cambio">
        {(["returned", "replacement", "summary"] as Step[]).map((id, index) => (
          <li key={id} aria-current={step === id ? "step" : undefined} className={cx(step === id && "is-current")}>
            <b>{index + 1}</b>{id === "returned" ? "¿Qué devuelve?" : id === "replacement" ? "¿Qué se lleva?" : "Resumen"}
          </li>
        ))}
      </ol>

      {step === "returned" && <>
        <ul className="sale-detail__list sale-detail__list--form">
          {units.map(unit => (
            <li key={unit.key}>
              <span>{unit.product_name}
                <small>{unit.source_type === "SALE_ITEM" ? `Compró ${formatQuantity(unit.quantity)}` : `Recibido en ${unit.origin} (${formatQuantity(unit.quantity)})`} · disponible {formatQuantity(unit.returnable_quantity)} · vale {pesos(unit.unit_price)} c/u</small>
                {unit.restockable && (quantities[unit.key] ?? 0) > 0 && (
                  <label className="sale-detail__check">
                    <input type="checkbox" checked={restock[unit.key]} onChange={event => setRestock(current => ({ ...current, [unit.key]: event.target.checked }))} />
                    Vuelve al inventario
                  </label>
                )}
                {!unit.restockable && <small>{unit.is_granel ? "Granel: no vuelve al inventario" : "Producto manual: no maneja inventario"}</small>}
              </span>
              <QtyStepper label={unit.product_name} value={quantities[unit.key] ?? 0}
                onChange={value => setQuantities(current => ({ ...current, [unit.key]: Math.max(0, Math.min(value, Number(unit.returnable_quantity))) }))} />
            </li>
          ))}
        </ul>
        <div className="ui-field"><span>Motivo *</span>
          <div className="sale-detail__reasons" role="group" aria-label="Motivo del cambio">
            {reasons.map(item => (
              <button key={item.code} type="button" aria-pressed={reason === item.code}
                className={cx("ui-choice__item", reason === item.code && "ui-choice__item--on")} onClick={() => setReason(item.code)}>{item.label}</button>
            ))}
          </div>
          {reason === "OTHER" && <textarea rows={2} aria-label="Describe el motivo" placeholder="Describe el motivo" value={reasonText} onChange={event => setReasonText(event.target.value)} />}
        </div>
      </>}

      {step === "replacement" && <ReplacementPicker cart={cart} setCart={setCart} onError={setError} remaining={cart.length && difference < 0 ? owed : 0} />}

      {step === "summary" && preview && (
        <div className="ui-stack">
          <section className="sale-detail__section" aria-label="Devuelve">
            <h3>Devuelve</h3>
            <ul className="sale-detail__list">
              {preview.returned.map(line => (
                <li key={line.source_id}><span>{formatQuantity(line.quantity)} × {line.product_name}
                  <small>{line.origin === "Venta" ? "De la venta" : `Recibido en ${line.origin}`} · {line.restock ? "vuelve al inventario" : "no vuelve al inventario"} · vale {pesos(line.unit_credit)} c/u</small></span>
                  <b className="ui-num">{pesos(line.line_credit)}</b></li>
              ))}
            </ul>
          </section>
          <section className="sale-detail__section" aria-label="Se lleva">
            <h3>Se lleva</h3>
            <ul className="sale-detail__list">
              {preview.replacements.map((line, index) => (
                <li key={index}><span>{formatQuantity(line.quantity)} × {line.product_name}
                  <small>{line.product_id ? "Sale del inventario" : "Producto manual: sin inventario"} · {pesos(line.unit_price)} c/u</small></span>
                  <b className="ui-num">{pesos(line.line_total)}</b></li>
              ))}
            </ul>
          </section>
          {preview.settlement === "STORE_REFUNDS" && !refunding && (
            <InlineMessage tone="info" title={`Al cliente le quedan ${pesos(owed)} por usar`}>
              Lo normal es que se lleve otro producto. Si prefiere el dinero, puedes devolverle solo la diferencia.
            </InlineMessage>
          )}
          {needsChannel && (
            <div className="ui-field"><span>{difference > 0 ? `¿Cómo paga el cliente la diferencia de ${pesos(owed)}?` : `¿Por qué medio se devuelven los ${pesos(owed)}?`} *</span>
              <div className="sale-detail__reasons" role="group" aria-label={difference > 0 ? "Medio de pago de la diferencia" : "Medio para devolver la diferencia"}>
                {channels.map(c => <button key={c.id} type="button" aria-pressed={channelId === c.id} className={cx("ui-choice__item", channelId === c.id && "ui-choice__item--on")} onClick={() => pickChannel(c.id)}>{c.name}</button>)}
              </div>
              {needsAccount && <AccountPicker accounts={accounts} value={accountId} onChange={setAccountId} label={difference > 0 ? "Llega a" : "Sale de"} missing={!accountId} />}
              {difference > 0 && channel?.affects_cash && (
                <label className="ui-field"><span>Recibido en efectivo</span>
                  <input type="number" inputMode="numeric" min="0" step="1" placeholder={String(owed)} value={received} onChange={event => setReceived(event.target.value)} />
                  {receivedValue >= owed ? <small className="cash-amount-preview">Cambio para el cliente: {pesos(receivedValue - owed)}</small>
                    : <small className="ui-field-error">El dinero recibido es menor que la diferencia</small>}
                </label>
              )}
              {difference < 0 && channel && <small className="sale-detail__meta">{channel.affects_cash ? `Entrega ${pesos(owed)} del cajón.` : `Quedará pendiente devolver ${pesos(owed)} por ${channel.name} hasta que lo confirmes.`}</small>}
            </div>
          )}
        </div>
      )}

      <div className="sale-detail__formfoot">
        <dl className="exchange__totals">
          <div><dt>Valor disponible</dt><dd className="ui-num">{pesos(preview && step === "summary" ? preview.returned_credit : credit)}</dd></div>
          <div><dt>Productos nuevos</dt><dd className="ui-num">{pesos(preview && step === "summary" ? preview.replacement_total : newTotal)}</dd></div>
          {/* Until the customer picks what they take there is no difference yet; a remainder is a normal state, never an error. */}
          <div className={cx("exchange__difference", cart.length > 0 && difference > 0 && "is-pay", cart.length > 0 && difference < 0 && "is-left")}>
            <dt>{!cart.length ? "Diferencia" : difference > 0 ? "Diferencia a pagar" : difference < 0 ? (refunding ? "Se devuelve al cliente" : "Te quedan por usar") : "Diferencia"}</dt>
            <dd className={cx("ui-num", cart.length > 0 && difference === 0 && "is-text")}>{!cart.length ? "—" : difference === 0 ? "Sin diferencia por pagar" : pesos(owed)}</dd>
          </div>
        </dl>
        {error && <p className="ui-field-error" role="alert">{error}</p>}
        <div className="ui-form__actions">
          {step === "returned" && <>
            <TouchButton variant="secondary" size="lg" onClick={onCancel}>Volver</TouchButton>
            <TouchButton size="lg" module="caja" disabled={!step1Valid} onClick={() => { setError(""); setStep("replacement"); }}>Elegir lo que se lleva</TouchButton>
          </>}
          {step === "replacement" && <>
            <TouchButton variant="secondary" size="lg" onClick={() => setStep("returned")}>Atrás</TouchButton>
            <TouchButton size="lg" module="caja" disabled={!cart.length || busy} loading={busy} onClick={() => void toSummary()}>Ver resumen</TouchButton>
          </>}
          {step === "summary" && preview?.settlement === "STORE_REFUNDS" && !refunding && <>
            <TouchButton variant="secondary" size="lg" onClick={() => setRefunding(true)}>Devolver {pesos(owed)}</TouchButton>
            <TouchButton size="lg" module="caja" icon={Plus} onClick={addMore}>Agregar otro producto</TouchButton>
          </>}
          {step === "summary" && (preview?.settlement !== "STORE_REFUNDS" || refunding) && <>
            <TouchButton variant="secondary" size="lg" disabled={busy} onClick={refunding ? () => { setRefunding(false); setChannelId(""); } : addMore}>
              {refunding ? "Mejor agregar producto" : "Atrás"}</TouchButton>
            <TouchButton size="lg" module="caja" disabled={!preview || !paymentValid || busy} onClick={() => setConfirming(true)}>
              {difference > 0 ? `Continuar al pago de ${pesos(owed)}` : difference < 0 ? `Devolver ${pesos(owed)} y confirmar` : "Confirmar cambio"}</TouchButton>
          </>}
        </div>
      </div>

      <ConfirmDialog open={confirming} module="caja" title="Confirmar cambio de producto" busy={busy}
        confirmLabel={difference > 0 ? `Cobrar ${pesos(owed)} y confirmar` : difference < 0 ? `Devolver ${pesos(owed)} y confirmar` : "Confirmar cambio"}
        onCancel={() => setConfirming(false)} onConfirm={() => void submit()}>
        {preview && <>
          <p><b>Devuelve:</b> {preview.returned.map(l => `${formatQuantity(l.quantity)} × ${l.product_name}`).join(", ")}</p>
          <p><b>Se lleva:</b> {preview.replacements.map(l => `${formatQuantity(l.quantity)} × ${l.product_name}`).join(", ")}</p>
          <p>{difference > 0
            ? `El cliente paga solo la diferencia: ${pesos(owed)} por ${channel?.name}${channel?.affects_cash ? " (entra al cajón)" : ""}.`
            : difference < 0
              ? `Se devuelve al cliente solo la diferencia: ${pesos(owed)} por ${channel?.name}${channel?.affects_cash ? " (sale del cajón)" : " (queda pendiente hasta confirmar)"}.`
              : "Sin diferencia por pagar: no se mueve dinero."}</p>
        </>}
      </ConfirmDialog>
    </div>
  );
}

/** "¿Qué se lleva?": scanner first (8–14 digits → by barcode, UPC/EAN rule in the backend), name search, or a manual line. */
function ReplacementPicker({ cart, setCart, onError, remaining }: { cart: Replacement[]; setCart: (update: (current: Replacement[]) => Replacement[]) => void; onError: (message: string) => void; remaining: number }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Product[]>([]);
  const [manual, setManual] = useState(false);
  const [manualKind, setManualKind] = useState<"SUPER" | "BULTO" | "PET_OTHER">("SUPER");
  const [manualName, setManualName] = useState("");
  const [manualAmount, setManualAmount] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  function add(item: Omit<Replacement, "key" | "quantity">) {
    setCart(current => {
      const existing = item.product_id ? current.find(entry => entry.product_id === item.product_id) : undefined;
      if (existing) return current.map(entry => entry === existing ? { ...entry, quantity: entry.quantity + 1 } : entry);
      return [...current, { ...item, quantity: 1, key: `${item.product_id ?? "m"}-${Date.now()}` }];
    });
    setQuery(""); setResults([]); window.setTimeout(() => inputRef.current?.focus(), 0);
  }

  async function submit(value: string) {
    const code = value.trim();
    if (!code) return;
    onError("");
    try {
      if (SCAN_CODE.test(code)) {
        const product = await api.productByBarcode(code);
        if (!product.active) return onError(`${product.name} está inactivo`);
        add({ product_id: product.id, name: product.name, unit_price: Number(product.sale_price) });
      } else setResults(await api.search(code));
    } catch (cause) { onError(errorText(cause)); }
  }

  useEffect(() => {
    if (query.trim().length < 2 || SCAN_CODE.test(query.trim())) { setResults([]); return; }
    const timer = window.setTimeout(() => { api.search(query.trim()).then(setResults).catch(() => setResults([])); }, 300);
    return () => window.clearTimeout(timer);
  }, [query]);

  const manualValid = manualName.trim().length >= 2 && Number(manualAmount) > 0;
  const list = useMemo(() => results.slice(0, 6), [results]);

  return (
    <div className="ui-stack">
      <SearchBar scanner autoFocus module="caja" label="Escanear o buscar el producto que se lleva"
        placeholder={remaining > 0 ? `Le quedan ${pesos(remaining)}: escanea o busca otro producto` : "Escanea o busca el producto nuevo"}
        value={query} onChange={setQuery} onSubmit={value => void submit(value)} inputRef={inputRef} />
      {list.length > 0 && (
        <ul className="exchange__results" aria-label="Resultados">
          {list.map(product => (
            <li key={product.id}>
              <button type="button" className="exchange__result" onClick={() => add({ product_id: product.id, name: product.name, unit_price: Number(product.sale_price) })}>
                <span>{product.name}<small>Stock {formatQuantity(product.stock)}{product.barcode && ` · ${product.barcode}`}</small></span>
                <b className="ui-num">{pesos(product.sale_price)}</b>
              </button>
            </li>
          ))}
        </ul>
      )}
      {cart.length ? (
        <ul className="sale-detail__list sale-detail__list--form" aria-label="Se lleva">
          {cart.map(item => (
            <li key={item.key}>
              <span>{item.name}<small>{pesos(item.unit_price)} c/u{item.manual_kind && " · producto manual"} · {pesos(item.unit_price * item.quantity)}</small></span>
              <span className="exchange__cart-actions">
                <QtyStepper label={item.name} value={item.quantity}
                  onChange={value => setCart(current => value <= 0 ? current.filter(entry => entry !== item) : current.map(entry => entry === item ? { ...entry, quantity: value } : entry))} />
                <TouchButton variant="ghost" iconOnly icon={Trash2} label={`Quitar ${item.name}`} onClick={() => setCart(current => current.filter(entry => entry !== item))} />
              </span>
            </li>
          ))}
        </ul>
      ) : <p className="ui-empty-hint">Escanea o busca el producto que se lleva el cliente.</p>}
      {!manual ? (
        <TouchButton variant="ghost" module="caja" icon={PackagePlus} onClick={() => setManual(true)}>Producto sin código (manual)</TouchButton>
      ) : (
        <div className="exchange__manual">
          <div className="ui-choice" role="group" aria-label="Tipo de producto manual">
            {MANUAL_KINDS.map(item => <button key={item.kind} type="button" aria-pressed={manualKind === item.kind} className={cx("ui-choice__item", manualKind === item.kind && "ui-choice__item--on")} onClick={() => setManualKind(item.kind)}>{item.label}</button>)}
          </div>
          <label className="ui-field"><span>Descripción</span><input value={manualName} onChange={event => setManualName(event.target.value)} /></label>
          <label className="ui-field"><span>Valor</span><input type="number" inputMode="numeric" min="0" step="1" value={manualAmount} onChange={event => setManualAmount(event.target.value)} /></label>
          <div className="ui-row">
            <TouchButton variant="secondary" onClick={() => setManual(false)}>Cancelar</TouchButton>
            <TouchButton module="caja" disabled={!manualValid} onClick={() => { add({ manual_kind: manualKind, name: manualName.trim(), unit_price: Number(manualAmount) }); setManual(false); setManualName(""); setManualAmount(""); }}>Agregar</TouchButton>
          </div>
        </div>
      )}
    </div>
  );
}
