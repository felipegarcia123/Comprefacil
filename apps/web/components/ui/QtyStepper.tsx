"use client";

import { Minus, Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { TouchButton } from "./TouchButton";

type Props = {
  value: number;
  /** Called with the new quantity. 0 means "remove" (the caller decides). */
  onChange: (value: number) => void;
  /** Accessible subject, e.g. the product name. */
  label: string;
  disabled?: boolean;
};

/**
 * − [ n ] + with 48px targets. The number is an input: tap it to type an exact quantity (numeric keypad on touch devices),
 * Enter/blur commits, Esc reverts. No modal is needed to change a quantity.
 */
export function QtyStepper({ value, onChange, label, disabled }: Props) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);

  function commit() {
    const parsed = Number(draft.replace(",", "."));
    if (!Number.isFinite(parsed) || draft.trim() === "") return setDraft(String(value));
    if (parsed !== value) onChange(Math.max(0, parsed));
  }

  return (
    <div className="ui-qty" role="group" aria-label={`Cantidad de ${label}`}>
      <TouchButton variant="secondary" iconOnly icon={Minus} label={`Quitar una unidad de ${label}`} disabled={disabled} onClick={() => onChange(value - 1)} />
      <input
        className="ui-qty__input ui-num"
        inputMode="decimal"
        aria-label={`Cantidad de ${label}`}
        value={draft}
        disabled={disabled}
        onFocus={event => event.currentTarget.select()}
        onChange={event => setDraft(event.target.value.replace(/[^0-9.,]/g, ""))}
        onBlur={commit}
        onKeyDown={event => {
          if (event.key === "Enter") { event.preventDefault(); commit(); event.currentTarget.blur(); }
          if (event.key === "Escape") { setDraft(String(value)); event.currentTarget.blur(); }
        }}
      />
      <TouchButton variant="secondary" iconOnly icon={Plus} label={`Agregar una unidad de ${label}`} disabled={disabled} onClick={() => onChange(value + 1)} />
    </div>
  );
}
