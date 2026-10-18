"use client";

import { X } from "lucide-react";
import { useEffect, useId, useRef, type MouseEvent, type ReactNode } from "react";
import { TouchButton } from "./TouchButton";
import type { ModuleId } from "./modules";

type Props = {
  open: boolean;
  title: string;
  onClose: () => void;
  children?: ReactNode;
  module?: ModuleId;
  /** true = the backdrop does not dismiss (forms with typed data). Esc and the close button still work. */
  persistent?: boolean;
  /** Wider panel from 640px (detail views with a table). Still a bottom sheet on phones. */
  wide?: boolean;
  /** Desktop side drawer anchored to the right edge. On narrow screens it remains a bottom sheet. */
  side?: boolean;
};

/**
 * Menu / picker surface on native <dialog>: bottom sheet on phones, centered panel from 640px.
 * For non-destructive content only: tapping the backdrop or pressing Esc closes it.
 * (Use ConfirmDialog for decisions that must not be dismissed by accident.)
 */
export function Sheet({ open, title, onClose, children, module, persistent, wide, side }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  // React 19 re-dispatches a nested <dialog>'s "close" (e.g. a ConfirmDialog inside this sheet) through the
  // component tree: only this dialog's own close may close the sheet, or "Volver" in a confirmation would
  // throw away the whole form behind it.
  function onBackdrop(event: MouseEvent<HTMLDialogElement>) {
    if (!persistent && event.target === ref.current) onClose(); // clicks on the ::backdrop target the dialog itself
  }

  return (
    <dialog ref={ref} className={`ui-sheet${wide ? " ui-sheet--wide" : ""}${side ? " ui-sheet--side" : ""}`} data-module={module} aria-labelledby={titleId} onClick={onBackdrop}
      onClose={event => { if (event.target === ref.current) onClose(); }}
      // Esc: close through React state (like ConfirmDialog) instead of relying on the native "close" event,
      // which a throttled/background renderer may never deliver — the sheet would look closed while the
      // screen still thinks it is open, and no other sale could be opened until a reload.
      onCancel={event => { if (event.target === ref.current) { event.preventDefault(); onClose(); } }}>
      <div className="ui-sheet__head">
        <h2 id={titleId} className="ui-sheet__title">{title}</h2>
        <TouchButton variant="ghost" iconOnly icon={X} label="Cerrar" onClick={onClose} />
      </div>
      <div className="ui-sheet__body">{children}</div>
    </dialog>
  );
}
