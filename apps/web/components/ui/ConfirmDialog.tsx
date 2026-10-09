"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";
import { TouchButton } from "./TouchButton";
import type { ModuleId } from "./modules";

type Props = {
  open: boolean;
  title: string;
  children?: ReactNode;
  /** Verb-first, names the action ("Cancelar borrador"), never just "Aceptar". */
  confirmLabel: string;
  cancelLabel?: string;
  tone?: "primary" | "danger";
  busy?: boolean;
  module?: ModuleId;
  onConfirm: () => void;
  onCancel: () => void;
};

/**
 * Touch-friendly replacement for window.confirm(), built on native <dialog> (focus trap, Esc, top layer).
 * Tapping the backdrop does NOT dismiss: an accidental touch must not lose a decision or data.
 */
export function ConfirmDialog({ open, title, children, confirmLabel, cancelLabel = "Volver", tone = "primary", busy, module, onConfirm, onCancel }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog ref={ref} className="ui-dialog" data-module={module} aria-labelledby={titleId} onCancel={event => { event.preventDefault(); if (!busy) onCancel(); }}>
      <div className="ui-dialog__head"><h2 id={titleId} className="ui-dialog__title">{title}</h2></div>
      {children && <div className="ui-dialog__body">{children}</div>}
      <div className="ui-dialog__actions">
        <TouchButton variant="secondary" size="lg" onClick={onCancel} disabled={busy}>{cancelLabel}</TouchButton>
        <TouchButton variant={tone === "danger" ? "danger" : "primary"} size="lg" onClick={onConfirm} loading={busy}>{confirmLabel}</TouchButton>
      </div>
    </dialog>
  );
}
