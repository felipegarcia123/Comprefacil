import { CircleAlert, CircleCheck, CircleX, Info, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cx } from "@/lib/cx";

export type Tone = "success" | "warning" | "danger" | "info" | "neutral";

export const TONE_ICON: Record<Exclude<Tone, "neutral">, LucideIcon> = {
  success: CircleCheck, warning: CircleAlert, danger: CircleX, info: Info,
};

/** State chip: icon + text + border, never color alone. */
export function StatusBadge({ tone = "neutral", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  const Icon = tone === "neutral" ? null : TONE_ICON[tone];
  return (
    <span className={cx("ui-badge", `ui-badge--${tone}`, className)}>
      {Icon && <Icon size={16} aria-hidden="true" />}
      {children}
    </span>
  );
}
