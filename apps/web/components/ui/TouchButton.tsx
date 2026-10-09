import { LoaderCircle, type LucideIcon } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cx } from "@/lib/cx";
import type { ModuleId } from "./modules";

export type TouchButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  /** md 48px · lg 56px · xl 64px · cta 88px (COBRAR-class actions). */
  size?: "md" | "lg" | "xl" | "cta";
  module?: ModuleId;
  icon?: LucideIcon;
  /** Renders a square icon-only button. `label` becomes the accessible name. */
  iconOnly?: boolean;
  label?: string;
  block?: boolean;
  loading?: boolean;
  children?: ReactNode;
};

/** The "tecla": a keycap with a thick bottom edge that sinks when pressed. */
export function TouchButton({
  variant = "primary", size = "md", module, icon: Icon, iconOnly, label, block, loading, disabled,
  className, children, type = "button", ...rest
}: TouchButtonProps) {
  const inactive = disabled || loading;
  return (
    <button
      type={type}
      data-module={module}
      aria-label={iconOnly ? label : undefined}
      aria-busy={loading || undefined}
      disabled={inactive}
      className={cx("ui-btn", `ui-btn--${variant}`, `ui-btn--${size}`, iconOnly && "ui-btn--icon", block && "ui-btn--block", className)}
      {...rest}
    >
      {loading ? <LoaderCircle className="ui-btn__spin" size={24} aria-hidden="true" /> : Icon ? <Icon size={size === "cta" ? 32 : 24} aria-hidden="true" /> : null}
      {!iconOnly && children}
    </button>
  );
}
