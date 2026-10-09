import Link from "next/link";
import { ChevronRight } from "lucide-react";
import type { ReactNode } from "react";
import { cx } from "@/lib/cx";
import { Toldo } from "./Toldo";
import { MODULES, type ModuleId } from "./modules";

type Props = {
  module: ModuleId;
  /** Overrides the module route (default: MODULES[module].href). */
  href?: string;
  /** Verb-first call to action (default: MODULES[module].cta). */
  cta?: string;
  description?: string;
  /** Optional count of items in progress, shown as a pill (e.g. 2 + "abiertas"). */
  count?: number;
  countLabel?: string;
  /** Small contextual slot in the footer row (e.g. a StatusBadge). */
  meta?: ReactNode;
  /** Decorative illustration (aria-hidden); rendered bottom-right of the card. */
  art?: ReactNode;
  /** "feature" = the primary module of a screen: bigger type and art. */
  size?: "standard" | "feature";
  className?: string;
};

/**
 * Big touch target: toldo + icon + title + short description + CTA. Module identity = color + icon + title + toldo pattern.
 * The whole card is one link (>=48px footer, far larger body). Pressed state sinks the key; hover only on pointer devices.
 */
export function ModuleCard({ module, href, cta, description, count, countLabel, meta, art, size = "standard", className }: Props) {
  const info = MODULES[module];
  const Icon = info.icon;
  return (
    <Link href={href ?? info.href} className={cx("ui-module", size === "feature" && "ui-module--feature", className)} data-module={module}>
      <Toldo module={module} />
      <span className="ui-module__body">
        <span className="ui-module__head">
          <span className="ui-module__icon"><Icon size={size === "feature" ? 44 : 36} aria-hidden="true" /></span>
          <h2 className="ui-module__title">{info.label}</h2>
        </span>
        <p className="ui-module__desc">{description ?? info.description}</p>
        {art && <span className="ui-module__art" aria-hidden="true">{art}</span>}
      </span>
      <span className="ui-module__cta">
        <span>{cta ?? info.cta}</span>
        <span className="ui-module__meta">
          {meta}
          {count ? <span className="ui-module__count ui-num">{count}{countLabel ? ` ${countLabel}` : ""}</span> : null}
          <ChevronRight size={24} aria-hidden="true" />
        </span>
      </span>
    </Link>
  );
}
