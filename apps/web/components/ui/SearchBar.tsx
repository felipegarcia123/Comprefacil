"use client";

import { Keyboard, Search, X } from "lucide-react";
import { useRef, useState, type FormEvent } from "react";
import { BarcodeCaptureInput } from "@/components/BarcodeCaptureInput";
import { TouchButton } from "./TouchButton";
import type { ModuleId } from "./modules";

type Props = {
  value: string;
  onChange: (value: string) => void;
  /** Called on Enter and when a hardware scanner burst finishes (BarcodeCaptureInput timing logic). */
  onSubmit?: (value: string) => void;
  placeholder?: string;
  label: string;
  module?: ModuleId;
  /**
   * Scanner mode: the field does NOT open the on-screen keyboard on focus (inputMode="none");
   * a keyboard button opens it on demand. Hardware scanners (keyboard wedge) keep working.
   */
  scanner?: boolean;
  autoFocus?: boolean;
  /** Ref to the underlying <input> so screens can return focus to the scanner after an action. */
  inputRef?: React.RefObject<HTMLInputElement | null>;
  onKeyDown?: React.KeyboardEventHandler<HTMLInputElement>;
};

/** Large search/scan field (64px). Enter or a scanner burst submits; clear button; on-demand keyboard for touch devices. */
export function SearchBar({ value, onChange, onSubmit, placeholder, label, module, scanner = false, autoFocus, inputRef, onKeyDown }: Props) {
  const own = useRef<HTMLInputElement>(null);
  const input = inputRef ?? own;
  const [keyboard, setKeyboard] = useState(false);

  function toggleKeyboard() {
    setKeyboard(current => !current);
    window.setTimeout(() => input.current?.focus(), 0);
  }

  return (
    <form className="ui-search" role="search" data-module={module} onSubmit={(event: FormEvent) => event.preventDefault()}>
      <Search size={28} aria-hidden="true" />
      <BarcodeCaptureInput
        ref={input}
        className="ui-search__input"
        type="search"
        aria-label={label}
        placeholder={placeholder}
        value={value}
        autoFocus={autoFocus}
        enterKeyHint="search"
        inputMode={scanner && !keyboard ? "none" : "text"}
        onValueChange={onChange}
        onBarcode={code => onSubmit?.(code)}
        onKeyDown={onKeyDown}
      />
      {value && <TouchButton variant="ghost" iconOnly icon={X} label="Borrar búsqueda" onClick={() => { onChange(""); input.current?.focus(); }} />}
      {scanner && <TouchButton variant="ghost" iconOnly icon={Keyboard} label={keyboard ? "Ocultar teclado" : "Mostrar teclado"} aria-pressed={keyboard} onClick={toggleKeyboard} />}
    </form>
  );
}
