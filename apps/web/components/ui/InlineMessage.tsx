import { X } from "lucide-react";
import type { ReactNode } from "react";
import { cx } from "@/lib/cx";
import { TONE_ICON, type Tone } from "./StatusBadge";
import { TouchButton } from "./TouchButton";

type Props = {
  tone?: Exclude<Tone, "neutral">;
  title?: string;
  children: ReactNode;
  /** Action buttons (e.g. "Repetir preview"). */
  actions?: ReactNode;
  onDismiss?: () => void;
  className?: string;
};

/** In-page feedback. Errors/warnings use role="alert"; others role="status". Says what happened and how to fix it. */
export function InlineMessage({ tone = "info", title, children, actions, onDismiss, className }: Props) {
  const Icon = TONE_ICON[tone];
  return (
    <div className={cx("ui-msg", `ui-msg--${tone}`, className)} role={tone === "danger" || tone === "warning" ? "alert" : "status"}>
      <Icon size={24} aria-hidden="true" />
      <div className="ui-msg__main">
        {title && <strong className="ui-msg__title">{title}</strong>}
        {children}
      </div>
      {(actions || onDismiss) && (
        <div className="ui-msg__actions">
          {actions}
          {onDismiss && <TouchButton variant="ghost" iconOnly icon={X} label="Cerrar mensaje" onClick={onDismiss} />}
        </div>
      )}
    </div>
  );
}
