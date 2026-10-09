"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Banknote, Check, Columns2, CreditCard, Landmark, Search, Smartphone, UserRound } from "lucide-react";
import { Display, InlineMessage, NumberPad, PaymentButton, Sheet, TouchButton } from "@/components/ui";
import { AccountPicker } from "@/components/accounts/AccountPicker";
import { FINAL_CONSUMER_ID, formatIdentification } from "@/components/customers/identification";
import { cx } from "@/lib/cx";
import { money } from "@/lib/format";
import type { BusinessAccount, CardType, PaymentChannel, SaleCustomer } from "@/lib/types";

const BILLS = [2000, 5000, 10000, 20000, 50000, 100000];

/** Exact amount plus the two smallest bills that cover the total (what a Colombian cashier is actually handed). */
export function quickAmounts(total: number): number[] {
  return BILLS.filter(bill => bill >= total && bill !== total).slice(0, 2);
}

export type PayMode = "cash" | "electronic";

type Props = {
  total: number;
  itemCount: number;
  unitCount: number;
  /** First decision: cash or the "Transferencia" umbrella (every active non-cash channel). */
  mode: PayMode;
  onMode: (mode: PayMode) => void;
  hasCashChannel: boolean;
  /** Active non-cash channels, exactly as configured in the backend (never a hardcoded list). */
  electronicChannels: PaymentChannel[];
  channelId: string;
  onChannel: (id: string) => void;
  /** Card channels (Datáfono) need Débito/Crédito for the invoice; required before COBRAR. */
  cardType?: CardType;
  onCardType: (value: CardType) => void;
  /** 0028: business account where the electronic money lands (never inferred from the channel). */
  accounts: BusinessAccount[];
  accountId: string;
  onAccount: (id: string) => void;
  received: string;
  onReceived: (value: string) => void;
  /** "Factura electrónica" checkbox (OFF by default per sale). Hidden when Alegra isn't configured. */
  invoiceEnabled: boolean;
  electronicInvoice: boolean;
  onElectronicInvoice: (value: boolean) => void;
  /** Fiscal recipient while the checkbox is ON: a customer, or null = Consumidor final. */
  recipient: SaleCustomer | null;
  onPickCustomer: () => void;
  customerBusy: boolean;
  cajaOpen: boolean;
  busy: boolean;
  canCharge: boolean;
  hasItems: boolean;
  onCharge: () => void;
  onSplit: () => void;
  /** Comida a granel marked "Pago electrónico" with the cash channel chosen: COBRAR disabled. */
  granelInCash?: boolean;
  /** Comida a granel in the cart and how it was settled ("Báscula / efectivo" never becomes POS money). */
  granelTotal?: number;
  granelSettlement?: "EXTERNAL_SCALE" | "POS";
  onGranelSettlement?: (value: "EXTERNAL_SCALE" | "POS") => void;
  /** What the POS itself collects and records (customer total minus the scale part). */
  posTotal?: number;
  /** Only scale granel in the cart: nothing to charge in the POS. */
  scaleOnly?: boolean;
  /** Layout: docked side column (>=900px) or inside the payment sheet (tablet/phone). */
  variant: "dock" | "sheet";
};

/**
 * Total, how the customer pays, who the invoice goes to, and the COBRAR key. Payment rules are the
 * same as before: cash needs received >= total; the backend still decides everything else.
 */
export function PaymentPanel(props: Props) {
  const { total, itemCount, unitCount, mode, onMode, hasCashChannel, electronicChannels, channelId, onChannel, cardType, onCardType, accounts, accountId, onAccount, received, onReceived,
    invoiceEnabled, electronicInvoice, onElectronicInvoice, recipient, onPickCustomer, customerBusy,
    cajaOpen, busy, canCharge, hasItems, onCharge, onSplit, granelInCash, variant,
    granelTotal = 0, granelSettlement = "EXTERNAL_SCALE", onGranelSettlement, posTotal = total, scaleOnly } = props;
  const hasGranel = granelTotal > 0;
  const scale = hasGranel && granelSettlement === "EXTERNAL_SCALE";
  const [padOpen, setPadOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const cash = mode === "cash";
  const receivedNumber = Number(received || 0);
  const change = cash ? Math.max(0, receivedNumber - total) : 0;
  const missingChannel = !cash && !electronicChannels.some(channel => channel.id === channelId);
  const isCard = !cash && electronicChannels.find(channel => channel.id === channelId)?.payment_type === "CARD";
  const missingCardType = isCard && !cardType;
  const selectedChannel = !cash ? electronicChannels.find(channel => channel.id === channelId) : undefined;
  const missingAccount = Boolean(selectedChannel?.requires_destination_account) && !accountId;
  const missingReceived = cash && hasItems && receivedNumber < total;
  const selectedAccount = accounts.find(account => account.id === accountId);
  // On the 1366x680 kiosk the account keys can sit below the fold: bring them into view when a channel is picked.
  const accountRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (selectedChannel?.requires_destination_account) accountRef.current?.scrollIntoView({ block: "nearest" });
  }, [selectedChannel?.id, selectedChannel?.requires_destination_account]);

  return (
    <div className={`pay pay--${variant}`} data-module="ventas">
      <div className="pay__top">
        <Display label={hasGranel ? "Total cliente" : "Total"} value={money.format(total)} caption={`${itemCount} ${itemCount === 1 ? "producto" : "productos"} · ${unitCount} ${unitCount === 1 ? "unidad" : "unidades"}`} tickKey={total} live />
      </div>

      <div className="pay__scroll">
        {hasGranel && (
          <section className="pay__granel" aria-label="Comida a granel">
            <p className="pay__granel-row"><span>Productos POS</span><b className="ui-num">{money.format(total - granelTotal)}</b></p>
            <p className="pay__granel-row"><span>Comida a granel</span><b className="ui-num">{money.format(granelTotal)}</b></p>
            <div className="ui-choice pay__granel-choice" role="radiogroup" aria-label="¿Cómo se registró la comida a granel?">
              {([["EXTERNAL_SCALE", "Báscula / efectivo"], ["POS", "Pago electrónico"]] as const).map(([value, label]) => (
                <button key={value} type="button" role="radio" aria-checked={granelSettlement === value} disabled={busy}
                  className={cx("ui-choice__item", granelSettlement === value && "ui-choice__item--on")} onClick={() => onGranelSettlement?.(value)}>{label}</button>
              ))}
            </div>
            {scale && <small className="pay__granel-note">El valor de granel en efectivo ya queda registrado en la báscula.</small>}
            {scale && !cash && !scaleOnly && <small className="pay__granel-note">Por transferencia: <b className="ui-num">{money.format(posTotal)}</b>. La comida a granel se paga aparte en efectivo.</small>}
          </section>
        )}
        {/* Efectivo / Transferencia are the choice; "Dividir" (several methods) opens its own sheet, so it sits
            beside the radiogroup, never inside it, and is always in view instead of at the bottom of the scroll. */}
        <div className="pay__methods">
          <div className="pay__modes" role="radiogroup" aria-label="Forma de pago">
            <ModeKey icon={Banknote} label="Efectivo" selected={cash} disabled={!hasCashChannel} onSelect={() => onMode("cash")} />
            <ModeKey icon={Smartphone} label="Transferencia" selected={!cash} disabled={!electronicChannels.length} onSelect={() => onMode("electronic")} />
          </div>
          <button type="button" className="pay__split" onClick={onSplit} disabled={busy || !hasItems} aria-label="Pago dividido: el cliente pagó de varias formas">
            <Columns2 size={22} aria-hidden="true" /><span>Dividir</span>
          </button>
        </div>

        {cash ? (
          <div className="pay__cash">
            <div className="pay__readouts">
              <Display size="small" label="Recibido" value={money.format(receivedNumber)} />
              <Display size="small" tone="amber" label="Cambio" value={money.format(change)} tickKey={change} live />
            </div>
            <div className="pay__quick" role="group" aria-label="Monto recibido">
              <TouchButton variant="secondary" onClick={() => onReceived(String(total))} disabled={!hasItems}>Exacto</TouchButton>
              {quickAmounts(total).map(amount => (
                <TouchButton key={amount} variant="secondary" className="ui-num" onClick={() => onReceived(String(amount))} disabled={!hasItems}>{money.format(amount)}</TouchButton>
              ))}
              <TouchButton variant="secondary" onClick={() => { setDraft(""); setPadOpen(true); }} disabled={!hasItems}>Otro</TouchButton>
            </div>
            {!cajaOpen && (
              <InlineMessage tone="warning" actions={<Link href="/cash" className="ui-btn ui-btn--secondary ui-btn--md">Abrir caja</Link>}>
                La caja está cerrada: no se puede recibir efectivo todavía.
              </InlineMessage>
            )}
          </div>
        ) : (
          <fieldset className="pay__electronic">
            {/* The keys name themselves: the legend is only visible while it is an instruction. */}
            <legend className={cx(!missingChannel && "ui-sr-only")}>{missingChannel ? "Elige el canal exacto" : "Canal"}</legend>
            <div className="pay__channels" role="group" aria-label="Canal electrónico">
              {electronicChannels.map(item => (
                <PaymentButton key={item.id} name={item.name} kind={item.payment_type} selected={item.id === channelId} onSelect={() => onChannel(item.id)} />
              ))}
            </div>
            {isCard && (
              <div className="pay__card-type" role="radiogroup" aria-label="Tipo de tarjeta">
                {(["DEBIT", "CREDIT"] as const).map(value => (
                  <button key={value} type="button" role="radio" aria-checked={cardType === value}
                    className={cx("ui-choice__item", cardType === value && "ui-choice__item--on")} onClick={() => onCardType(value)}>
                    {value === "DEBIT" ? "Débito" : "Crédito"}
                  </button>
                ))}
              </div>
            )}
            {selectedChannel?.requires_destination_account && accounts.length > 0 && (
              <div ref={accountRef}>
                <AccountPicker accounts={accounts} value={accountId} onChange={onAccount} disabled={busy} missing={missingAccount && hasItems} />
              </div>
            )}
          </fieldset>
        )}
      </div>

      <div className="pay__cta">
        {/* Pinned next to COBRAR (never scrolled away at 1366x680): one compact row whatever its state. */}
        {invoiceEnabled && (
          <div className={cx("pay__invoice", electronicInvoice && "pay__invoice--on")}>
            <label className="pay__check">
              <input type="checkbox" checked={electronicInvoice} disabled={customerBusy || busy} onChange={event => onElectronicInvoice(event.target.checked)} />
              <span className="pay__check-box" aria-hidden="true"><Check size={20} /></span>
              <span className="pay__check-text">Factura electrónica{!electronicInvoice && <small>Solo si el cliente la pide</small>}</span>
            </label>
            {electronicInvoice && (
              <div className="pay__recipient">
                <span className="pay__check-text">
                  <small>Cliente</small>
                  <span className="pay__customer-name"><UserRound size={18} aria-hidden="true" /><span>{recipient ? recipient.name : "Consumidor final"}</span></span>
                  <small className="ui-num">{recipient ? formatIdentification(recipient.identification_type, recipient.identification_number, recipient.dv) : FINAL_CONSUMER_ID}</small>
                </span>
                <TouchButton variant="ghost" icon={Search} onClick={onPickCustomer} loading={customerBusy} disabled={busy}>Buscar otro cliente</TouchButton>
              </div>
            )}
          </div>
        )}
        {/* Why COBRAR is still disabled: ONE line, the first pending step (same conditions as before, one at a time). */}
        {(() => {
          const hint: [typeof Banknote, string] | null =
            scaleOnly ? [Banknote, "Solo hay comida a granel de báscula: no hay nada que cobrar en el POS"]
            : !hasItems ? null
            : granelInCash ? [Smartphone, "Granel con pago electrónico: cóbralo por transferencia (o Pago dividido)"]
            : missingChannel ? [Smartphone, "Elige el canal de la transferencia"]
            : missingCardType ? [CreditCard, "Elige si la tarjeta es Débito o Crédito"]
            : missingAccount ? [Landmark, "Elige a qué cuenta llegó el dinero"]
            : missingReceived ? [Banknote, "Indica cuánto entregó el cliente"]
            : null;
          if (!hint) return null;
          const [HintIcon, text] = hint;
          return <p className="pay__cta-hint" role="status"><HintIcon size={18} aria-hidden="true" />{text}</p>;
        })()}
        <TouchButton size="cta" block loading={busy} disabled={!canCharge} onClick={onCharge} className="pay__charge">
          {busy ? "Cobrando…" : (
            <span className="pay__charge-text">
              <span>COBRAR <span className="ui-num">{money.format(scale && !cash ? posTotal : total)}</span></span>
              {/* What this key records, on the key itself: channel + destination account (0028) or cash change. */}
              {selectedChannel && hasItems && (
                <span className="pay__charge-sub">
                  {selectedChannel.payment_type === "CARD" ? <CreditCard size={18} aria-hidden="true" /> : <Smartphone size={18} aria-hidden="true" />}
                  {selectedChannel.name}{isCard && cardType ? ` ${cardType === "DEBIT" ? "débito" : "crédito"}` : ""}
                  {selectedChannel.requires_destination_account && selectedAccount && <><Landmark size={18} aria-hidden="true" />llega a {selectedAccount.name}</>}
                </span>
              )}
              {cash && hasItems && receivedNumber >= total && (
                <span className="pay__charge-sub"><Banknote size={18} aria-hidden="true" />Efectivo, cambio <span className="ui-num">{money.format(change)}</span></span>
              )}
            </span>
          )}
        </TouchButton>
      </div>

      <Sheet open={padOpen} title="Dinero recibido" persistent onClose={() => setPadOpen(false)} module="ventas">
        {padOpen && (
          <div className="pay__pad">
            <Display size="figure" label="Recibido" value={money.format(Number(draft || 0))} caption={`Total ${money.format(total)}`} />
            <NumberPad value={draft} onChange={setDraft} module="ventas" />
            <TouchButton size="xl" block onClick={() => { onReceived(draft); setPadOpen(false); }}>Listo</TouchButton>
          </div>
        )}
      </Sheet>
    </div>
  );
}

function ModeKey({ icon: Icon, label, hint, selected, disabled, onSelect }: { icon: typeof Banknote; label: string; hint?: string; selected: boolean; disabled?: boolean; onSelect: () => void }) {
  return (
    <button type="button" role="radio" aria-checked={selected} disabled={disabled} className={cx("pay__mode", selected && "pay__mode--on")} onClick={onSelect}>
      <Icon size={28} aria-hidden="true" />
      <span className="pay__mode-label">{label}{hint && <small>{hint}</small>}</span>
      {selected && <Check size={22} aria-hidden="true" className="pay__mode-check" />}
    </button>
  );
}
