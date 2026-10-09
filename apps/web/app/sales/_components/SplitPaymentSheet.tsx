"use client";

import { useState } from "react";
import { Banknote, CreditCard, Smartphone } from "lucide-react";
import { Display, InlineMessage, NumberPad, Sheet, TouchButton } from "@/components/ui";
import { AccountPicker } from "@/components/accounts/AccountPicker";
import { cx } from "@/lib/cx";
import { money } from "@/lib/format";
import type { BusinessAccount, CardType, PaymentChannel } from "@/lib/types";

type Allocation = { channel_id: string; amount: number; received_amount?: number; card_type?: CardType; destination_account_id?: string };
type Props = {
  /** What the POS collects (the scale part of the comida a granel is NOT included). */
  total: number;
  /** Comida a granel settled by the scale: paid in cash by the customer, never a POS payment. */
  scaleTotal?: number;
  channels: PaymentChannel[];
  /** 0028: each electronic payment keeps its own destination account. */
  accounts: BusinessAccount[];
  busy: boolean;
  close: () => void;
  confirm: (payments: Allocation[]) => Promise<void>;
};

const KIND: Record<string, { label: string; Icon: typeof Banknote }> = {
  CASH: { label: "Efectivo", Icon: Banknote },
  TRANSFER: { label: "Digital", Icon: Smartphone },
  CARD: { label: "Tarjeta", Icon: CreditCard },
};

/**
 * Split payment (same rules and payload as before): amounts per channel must add up exactly to the total.
 * Presentation only: tap a channel row to make it active, type its amount on the keypad, "Restante" fills what is missing.
 */
export function SplitPaymentSheet({ total, scaleTotal = 0, channels, accounts, busy, close, confirm }: Props) {
  const [amounts, setAmounts] = useState<Record<string, string>>({});
  const [active, setActive] = useState(channels[0]?.id ?? "");
  const [cardTypes, setCardTypes] = useState<Record<string, CardType>>({});
  // Destination per channel: the channel's default is only a preselection, the cashier can change it.
  const [destinations, setDestinations] = useState<Record<string, string>>(() =>
    Object.fromEntries(channels.filter(channel => !channel.affects_cash && channel.default_account_id).map(channel => [channel.id, channel.default_account_id!])));
  const paid = channels.reduce((sum, channel) => sum + Number(amounts[channel.id] || 0), 0);
  const pending = total - paid;
  // Every card channel with an amount needs Débito/Crédito (the invoice's payment method depends on it).
  const cardsReady = channels.every(channel => channel.payment_type !== "CARD" || !(Number(amounts[channel.id]) > 0) || cardTypes[channel.id]);
  const accountsReady = channels.every(channel => !channel.requires_destination_account || !(Number(amounts[channel.id]) > 0) || destinations[channel.id]);
  const valid = paid === total && paid > 0 && cardsReady && accountsReady;
  // Same resolution as the backend (payment_methods.py): one fiscal method per invoice. Mixing two is allowed
  // for the sale (cash/payments untouched) but its electronic invoice will be BLOCKED for review.
  const fiscalMethods = new Set(channels.filter(channel => Number(amounts[channel.id]) > 0).map(channel =>
    channel.payment_type === "CARD" ? (cardTypes[channel.id] ? `CARD_${cardTypes[channel.id]}` : null) : channel.alegra_payment_method ?? `CHANNEL_${channel.id}`,
  ).filter(Boolean));
  const mixedFiscal = fiscalMethods.size > 1;
  const activeChannel = channels.find(channel => channel.id === active);

  function submit() {
    if (!valid || busy) return;
    const firstCash = channels.find(channel => channel.affects_cash && Number(amounts[channel.id]) > 0)?.id;
    void confirm(channels.filter(channel => Number(amounts[channel.id]) > 0).map(channel => ({
      channel_id: channel.id, amount: Number(amounts[channel.id]),
      // The customer's cash also covers the scale part (exact, no change): never part of `amount` (POS money).
      received_amount: channel.affects_cash ? Number(amounts[channel.id]) + (channel.id === firstCash ? scaleTotal : 0) : undefined,
      card_type: channel.payment_type === "CARD" ? cardTypes[channel.id] : undefined,
      destination_account_id: channel.affects_cash ? undefined : destinations[channel.id] || undefined,
    })));
  }

  return (
    <Sheet open title="Dividir pago" persistent wide onClose={close} module="ventas">
      <div className="split">
        <div className="split__totals">
          <Display size="small" label={scaleTotal ? "Total POS" : "Total de la venta"} value={money.format(total)} caption={scaleTotal ? `+ ${money.format(scaleTotal)} granel en báscula (efectivo aparte)` : undefined} />
          <Display size="small" tone="amber" label={pending >= 0 ? "Pendiente" : "Excede"} value={money.format(Math.abs(pending))} tickKey={pending} live />
        </div>
        {/* >=900: channels + their account/card choices on the left, keypad on the right, Confirmar always in view. */}
        <div className="split__body">
        <div className="split__col">
        <ul className="split__rows" aria-label="Monto por canal">
          {channels.map(channel => {
            const kind = KIND[channel.payment_type] ?? KIND.CASH;
            const value = amounts[channel.id] ?? "";
            return (
              <li key={channel.id}>
                <button type="button" className={cx("split__row", channel.id === active && "split__row--active")} aria-pressed={channel.id === active} onClick={() => setActive(channel.id)}>
                  <kind.Icon size={22} aria-hidden="true" />
                  <span className="split__name">{channel.name}<small>{channel.payment_type === "CARD" && cardTypes[channel.id] ? (cardTypes[channel.id] === "DEBIT" ? "Débito" : "Crédito") : kind.label}{!channel.affects_cash && destinations[channel.id] ? ` · ${accounts.find(account => account.id === destinations[channel.id])?.name ?? ""}` : ""}</small></span>
                  <strong className="ui-num">{money.format(Number(value || 0))}</strong>
                </button>
              </li>
            );
          })}
        </ul>
        {activeChannel?.payment_type === "CARD" && (
          <div className="ui-choice" role="radiogroup" aria-label={`Tipo de tarjeta en ${activeChannel.name}`}>
            {(["DEBIT", "CREDIT"] as const).map(value => (
              <button key={value} type="button" role="radio" aria-checked={cardTypes[active] === value}
                className={cx("ui-choice__item", cardTypes[active] === value && "ui-choice__item--on")}
                onClick={() => setCardTypes(current => ({ ...current, [active]: value }))}>
                {value === "DEBIT" ? "Débito" : "Crédito"}
              </button>
            ))}
          </div>
        )}
        {activeChannel?.requires_destination_account && accounts.length > 0 && (
          <AccountPicker accounts={accounts} value={destinations[active] ?? ""} onChange={id => setDestinations(current => ({ ...current, [active]: id }))}
            label={`${activeChannel.name} llega a`} missing={Number(amounts[active]) > 0 && !destinations[active]} />
        )}
        {!cardsReady && paid === total && <p className="pay__cta-hint" role="status">Indica si la tarjeta del datáfono es Débito o Crédito</p>}
        {cardsReady && !accountsReady && paid === total && <p className="pay__cta-hint" role="status">Elige a qué cuenta llegó cada pago electrónico</p>}
        </div>
        <div className="split__col">
        <TouchButton variant="secondary" block disabled={pending <= 0 || !active} onClick={() => setAmounts(current => ({ ...current, [active]: String(Number(current[active] || 0) + pending) }))}>
          Poner el restante en {channels.find(channel => channel.id === active)?.name ?? "el canal activo"}
        </TouchButton>
        <NumberPad value={amounts[active] ?? ""} onChange={value => setAmounts(current => ({ ...current, [active]: value }))} module="ventas" />
        </div>
        </div>
        {mixedFiscal && <InlineMessage tone="warning">Esta combinación de pagos requerirá revisión antes de emitir la factura electrónica.</InlineMessage>}
        <div className="split__actions">
          <TouchButton variant="secondary" size="lg" onClick={close} disabled={busy}>Volver</TouchButton>
          <TouchButton size="lg" loading={busy} disabled={!valid} onClick={submit}>Confirmar pago</TouchButton>
        </div>
      </div>
    </Sheet>
  );
}
