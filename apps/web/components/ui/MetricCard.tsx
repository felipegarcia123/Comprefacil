import { cx } from "@/lib/cx";
import type { ModuleId } from "./modules";

type Props = {
  label: string;
  value: string;
  module?: ModuleId;
  className?: string;
  /** Small third line, e.g. a change-vs-previous-period reading. Never a second figure. */
  caption?: string;
  /** Tone for `caption` only — the label/value keep the module's own color either way. */
  captionTone?: "up" | "down";
};

/** Small operational stat block (e.g. "Total recibido"). For Caja/Resumen/Informes-style summaries, not decorative KPIs. */
export function MetricCard({ label, value, module, className, caption, captionTone }: Props) {
  return (
    <div className={cx("ui-metric-card", className)} data-module={module}>
      <span className="ui-metric-card__label">{label}</span>
      <b className="ui-metric-card__value ui-num">{value}</b>
      {caption && <span className={cx("ui-metric-card__caption", captionTone && `ui-metric-card__caption--${captionTone}`)}>{caption}</span>}
    </div>
  );
}
