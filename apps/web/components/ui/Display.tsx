import { cx } from "@/lib/cx";

type Props = {
  label: string;
  value: string;
  caption?: string;
  tone?: "ok" | "amber";
  /** "total" = TOTAL-size readout (fluid clamp); "figure" = key figure; "small" = compact readout inside a panel. */
  size?: "total" | "figure" | "small";
  /** Announce changes to screen readers (use for the live sale total). */
  live?: boolean;
  /** When this value changes the readout plays a short 160ms "tick" (transform/opacity only). */
  tickKey?: string | number;
  className?: string;
};

/** Dark cash-register readout for TOTAL, change and sums. Bricolage + tabular digits; the size is fluid. */
export function Display({ label, value, caption, tone = "ok", size = "total", live, tickKey, className }: Props) {
  return (
    <div className={cx("ui-display", tone === "amber" && "ui-display--amber", size !== "total" && `ui-display--${size}`, className)}>
      <span className="ui-display__label">{label}</span>
      <output key={tickKey} data-tick={tickKey !== undefined || undefined} className="ui-display__value" aria-live={live ? "polite" : "off"}>{value}</output>
      {caption && <span className="ui-display__caption">{caption}</span>}
    </div>
  );
}
