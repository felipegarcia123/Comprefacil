"use client";

import { InputHTMLAttributes, Ref, useEffect, useRef } from "react";

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "onSubmit"> & {
  value: string;
  onValueChange: (value: string) => void;
  onBarcode: (barcode: string) => void | Promise<void>;
  onChange?: React.ChangeEventHandler<HTMLInputElement>;
  autoSubmitRapidInput?: boolean;
  /** React 19 passes `ref` as a prop; it is forwarded to the <input> through {...props}. */
  ref?: Ref<HTMLInputElement>;
};

export function BarcodeCaptureInput({ value, onValueChange, onBarcode,
  autoSubmitRapidInput = true, onKeyDown, onChange, ...props }: Props) {
  const timing = useRef({ first: 0, last: 0, count: 0 });
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const valueRef = useRef(value);
  useEffect(() => { valueRef.current = value; }, [value]);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  function reset() { timing.current = { first: 0, last: 0, count: 0 }; }
  function submit(barcode = valueRef.current) {
    const clean = barcode.trim();
    if (timer.current) clearTimeout(timer.current);
    reset();
    if (clean) void onBarcode(clean);
  }

  return <input {...props} value={value} autoComplete="off" spellCheck={false}
    onKeyDown={event => {
      onKeyDown?.(event);
      if (event.defaultPrevented) return;
      if (event.key === "Enter") { event.preventDefault(); submit(); return; }
      if (event.key.length !== 1 || event.ctrlKey || event.altKey || event.metaKey) return;
      const now = performance.now(), state = timing.current;
      if (!state.last || now - state.last > 120) timing.current = { first: now, last: now, count: 1 };
      else timing.current = { first: state.first, last: now, count: state.count + 1 };
    }}
    onChange={event => {
      const next = event.target.value; valueRef.current = next; onValueChange(next);
      onChange?.(event);
      if (timer.current) clearTimeout(timer.current);
      const state = timing.current;
      const average = state.count > 1 ? (state.last - state.first) / (state.count - 1) : Infinity;
      if (autoSubmitRapidInput && state.count >= 4 && average < 50)
        timer.current = setTimeout(() => submit(next), 140);
    }}/>
}
