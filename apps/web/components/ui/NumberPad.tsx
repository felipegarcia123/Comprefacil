"use client";

import { Delete } from "lucide-react";
import { useRef } from "react";
import { TouchButton } from "./TouchButton";
import type { ModuleId } from "./modules";

type Props = {
  /** Digits only, as a string ("" = empty). The caller owns the value. */
  value: string;
  onChange: (value: string) => void;
  module?: ModuleId;
  /** Maximum digits (default 9: up to 999.999.999 COP). */
  maxLength?: number;
};

const KEYS = ["7", "8", "9", "4", "5", "6", "1", "2", "3", "00", "0"] as const;

/**
 * 3x4 numeric keypad with 64px keys ("teclas"). Digits only; no decimals (COP).
 * Used on demand (sheet/drawer), never as a permanent block: it replaces the on-screen OS keyboard for amounts.
 */
export function NumberPad({ value, onChange, module, maxLength = 9 }: Props) {
  // Track the latest value locally so two taps landing before a re-render (multi-touch, fast typists) never drop a digit.
  const latest = useRef(value);
  latest.current = value;
  function commit(next: string) { latest.current = next; onChange(next); }
  function press(key: string) {
    const current = latest.current;
    const next = current === "" || current === "0" ? key.replace(/^0+/, "") : current + key;
    if (next.length <= maxLength) commit(next);
  }
  return (
    <div className="ui-pad" role="group" aria-label="Teclado numérico" data-module={module}>
      {KEYS.map(key => (
        <TouchButton key={key} variant="secondary" size="xl" className="ui-pad__key ui-num" onClick={() => press(key)}>{key}</TouchButton>
      ))}
      <TouchButton variant="secondary" size="xl" iconOnly icon={Delete} label="Borrar último dígito" className="ui-pad__key" onClick={() => commit(latest.current.slice(0, -1))} />
    </div>
  );
}
