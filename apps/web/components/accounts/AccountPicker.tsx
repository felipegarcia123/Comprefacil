"use client";

import { useEffect, useState } from "react";
import { Check, Landmark } from "lucide-react";
import { api } from "@/lib/api";
import { cx } from "@/lib/cx";
import type { BusinessAccount } from "@/lib/types";

type Props = {
  accounts: BusinessAccount[];
  value: string;
  onChange: (id: string) => void;
  /** "Llega a" (money in) or "Sale de" (money out). */
  label?: string;
  disabled?: boolean;
  /** Required and still empty: the legend says so (never only color). */
  missing?: boolean;
};

/**
 * WHERE the money landed / left — a business account, never the payment channel (0028). Three compact
 * keys (48 px) in one row; the channel's default is only a preselection, the cashier can always change it.
 */
export function AccountPicker({ accounts, value, onChange, label = "Llega a", disabled, missing }: Props) {
  return (
    <fieldset className="account-picker">
      <legend><Landmark size={16} aria-hidden="true" />{label}{missing && <span className="account-picker__missing"> · elige la cuenta</span>}</legend>
      <div className="account-picker__keys" role="radiogroup" aria-label={label}>
        {accounts.map(account => (
          <button key={account.id} type="button" role="radio" aria-checked={value === account.id} disabled={disabled}
            className={cx("ui-choice__item", "account-picker__key", value === account.id && "ui-choice__item--on")}
            onClick={() => onChange(account.id)}>
            <span>{account.name}</span>
            {value === account.id && <Check size={18} aria-hidden="true" />}
          </button>
        ))}
      </div>
    </fieldset>
  );
}

/** Active business accounts (loaded once per screen that needs a picker). */
export function useBusinessAccounts(): BusinessAccount[] {
  const [accounts, setAccounts] = useState<BusinessAccount[]>([]);
  useEffect(() => { api.businessAccounts(true).then(setAccounts).catch(() => setAccounts([])); }, []);
  return accounts;
}
