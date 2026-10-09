import { Banknote, Check, CreditCard, Smartphone, type LucideIcon } from "lucide-react";
import { cx } from "@/lib/cx";

type PaymentKind = "CASH" | "TRANSFER" | "CARD" | string;

const ICONS: Record<string, LucideIcon> = { CASH: Banknote, TRANSFER: Smartphone, CARD: CreditCard };

type Props = {
  name: string;
  /** payment_type of the channel: CASH, TRANSFER or CARD. */
  kind: PaymentKind;
  selected: boolean;
  onSelect: () => void;
  disabled?: boolean;
};

/** Payment-channel key. Type is identified by icon + selected state by check + border (never color alone); 56px. */
export function PaymentButton({ name, kind, selected, onSelect, disabled }: Props) {
  const Icon = ICONS[kind] ?? Banknote;
  return (
    <button type="button" className={cx("ui-pay", selected && "ui-pay--selected")} aria-pressed={selected} disabled={disabled} onClick={onSelect}>
      <Icon size={22} aria-hidden="true" />
      <span className="ui-pay__name">{name}</span>
      {selected && <Check size={18} aria-hidden="true" className="ui-pay__check" />}
    </button>
  );
}
