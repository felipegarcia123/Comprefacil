import type { ComponentType, SVGProps } from "react";
import type { ModuleId } from "@/components/ui/modules";

/**
 * Module illustrations for Home ("Mostrador" vocabulary). Flat shapes, 3px rounded strokes, no gradients or shadows.
 * Colors come only from tokens, so each one inherits its module via data-module (--mod-*). Decorative: aria-hidden.
 */
const STROKE = { stroke: "var(--mod-deep)", strokeWidth: 3, strokeLinejoin: "round", strokeLinecap: "round" } as const;

function Svg(props: SVGProps<SVGSVGElement>) {
  return <svg viewBox="0 0 200 150" preserveAspectRatio="xMaxYMax meet" aria-hidden="true" focusable="false" className="ui-illus" {...props} />;
}

/** Ventas: cash register with receipt and open drawer. */
export function RegisterIllustration() {
  return (
    <Svg>
      <path d="M128 52 V16 h34 v36 l-6 -5 -6 5 -6 -5 -6 5 -6 -5 -4 5Z" fill="var(--color-surface)" {...STROKE} />
      <path d="M136 26h18M136 34h18M136 42h12" stroke="var(--mod-strong)" strokeWidth={3} strokeLinecap="round" />
      <rect x="34" y="70" width="112" height="52" rx="8" fill="var(--mod-strong)" {...STROKE} />
      <rect x="46" y="30" width="72" height="46" rx="8" fill="var(--mod-tint)" {...STROKE} />
      <rect x="54" y="38" width="56" height="20" rx="4" fill="var(--color-lcd)" />
      <path d="M62 48h22M90 48h12" stroke="var(--color-lcd-ok)" strokeWidth={4} strokeLinecap="round" />
      <g fill="var(--mod-tint)" stroke="var(--mod-deep)" strokeWidth={2}>
        <rect x="48" y="82" width="16" height="12" rx="3" /><rect x="70" y="82" width="16" height="12" rx="3" /><rect x="92" y="82" width="16" height="12" rx="3" />
        <rect x="48" y="100" width="16" height="12" rx="3" /><rect x="70" y="100" width="16" height="12" rx="3" /><rect x="92" y="100" width="16" height="12" rx="3" />
      </g>
      <rect x="116" y="82" width="20" height="30" rx="4" fill="var(--color-surface)" stroke="var(--mod-deep)" strokeWidth={2} />
      <rect x="28" y="122" width="124" height="14" rx="5" fill="var(--mod-deep)" />
      <rect x="44" y="136" width="92" height="6" rx="3" fill="var(--color-surface)" {...STROKE} strokeWidth={2} />
    </Svg>
  );
}

/** Inventario: stacked boxes on a shelf with a barcode label. */
export function StockIllustration() {
  return (
    <Svg>
      <path d="M16 132h168" {...STROKE} />
      <rect x="24" y="84" width="60" height="48" rx="5" fill="var(--mod-strong)" {...STROKE} />
      <path d="M24 100h60" stroke="var(--mod-deep)" strokeWidth={3} />
      <rect x="92" y="96" width="44" height="36" rx="5" fill="var(--mod-tint)" {...STROKE} />
      <path d="M100 96v36" stroke="var(--mod-deep)" strokeWidth={3} />
      <rect x="46" y="40" width="52" height="44" rx="5" fill="var(--mod-tint)" {...STROKE} />
      <rect x="140" y="70" width="38" height="62" rx="5" fill="var(--color-surface)" {...STROKE} />
      <path d="M148 82v38M154 82v38M158 82v38M165 82v38M170 82v38" stroke="var(--color-ink)" strokeWidth={2.5} />
      <path d="M56 40v44M88 40v44" stroke="var(--mod-deep)" strokeWidth={2} />
    </Svg>
  );
}

/** Compras: open box receiving goods from above. */
export function ReceivingIllustration() {
  return (
    <Svg>
      <path d="M40 78 L20 58 h44 l6 20Z M160 78 l20 -20 h-44 l-6 20Z" fill="var(--mod-tint)" {...STROKE} />
      <path d="M40 78h120v52a6 6 0 0 1 -6 6H46a6 6 0 0 1 -6 -6Z" fill="var(--mod-strong)" {...STROKE} />
      <path d="M84 78h32v20l-16 -8 -16 8Z" fill="var(--mod-deep)" />
      <rect x="66" y="34" width="26" height="30" rx="5" fill="var(--color-surface)" {...STROKE} transform="rotate(-8 79 49)" />
      <circle cx="122" cy="46" r="15" fill="var(--color-surface)" {...STROKE} />
      <path d="M116 46h12" stroke="var(--mod-deep)" strokeWidth={3} strokeLinecap="round" />
      <path d="M100 10v18M92 20l8 8 8 -8" stroke="var(--mod-deep)" strokeWidth={4} fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </Svg>
  );
}

/** Proveedores: delivery truck carrying boxes. */
export function TruckIllustration() {
  return (
    <Svg>
      <path d="M8 130h184" {...STROKE} />
      <rect x="14" y="48" width="98" height="70" rx="6" fill="var(--mod-tint)" {...STROKE} />
      <rect x="26" y="26" width="34" height="22" rx="4" fill="var(--mod-strong)" {...STROKE} />
      <rect x="66" y="34" width="30" height="14" rx="4" fill="var(--color-surface)" {...STROKE} />
      <path d="M112 66h34l22 24v28h-56Z" fill="var(--mod-strong)" {...STROKE} />
      <path d="M124 74h20l12 14h-32Z" fill="var(--color-surface)" stroke="var(--mod-deep)" strokeWidth={2.5} strokeLinejoin="round" />
      <g fill="var(--color-ink)" stroke="var(--color-surface)" strokeWidth={4}>
        <circle cx="44" cy="120" r="12" /><circle cx="138" cy="120" r="12" />
      </g>
      <circle cx="44" cy="120" r="4" fill="var(--color-surface)" /><circle cx="138" cy="120" r="4" fill="var(--color-surface)" />
      <path d="M30 88h50" stroke="var(--mod-deep)" strokeWidth={3} strokeLinecap="round" />
    </Svg>
  );
}

/** Caja: banknotes and coins. */
export function CashIllustration() {
  return (
    <Svg>
      <rect x="34" y="34" width="112" height="60" rx="6" fill="var(--mod-tint)" {...STROKE} transform="rotate(-10 90 64)" />
      <rect x="30" y="54" width="120" height="64" rx="6" fill="var(--mod-strong)" {...STROKE} />
      <circle cx="90" cy="86" r="18" fill="var(--mod-tint)" {...STROKE} />
      <path d="M90 76v20M84 82h9a4 4 0 0 1 0 8h-8" stroke="var(--mod-deep)" strokeWidth={3} fill="none" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="44" cy="66" r="4" fill="var(--mod-deep)" /><circle cx="136" cy="106" r="4" fill="var(--mod-deep)" />
      <g fill="var(--color-surface)" {...STROKE}>
        <ellipse cx="160" cy="118" rx="24" ry="9" /><ellipse cx="160" cy="108" rx="24" ry="9" /><ellipse cx="160" cy="98" rx="24" ry="9" />
      </g>
    </Svg>
  );
}

/** Informes: receipt with a bar chart and a rising trend. */
export function ReportIllustration() {
  return (
    <Svg>
      <path d="M46 14h84v112l-10 -7 -10 7 -10 -7 -10 7 -10 -7 -10 7 -10 -7 -14 7Z" fill="var(--color-surface)" {...STROKE} />
      <path d="M60 30h40M60 40h28" stroke="var(--mod-deep)" strokeWidth={3} strokeLinecap="round" />
      <g fill="var(--mod-strong)" stroke="var(--mod-deep)" strokeWidth={2.5} strokeLinejoin="round">
        <rect x="60" y="82" width="14" height="22" rx="2" /><rect x="80" y="68" width="14" height="36" rx="2" /><rect x="100" y="54" width="14" height="50" rx="2" />
      </g>
      <path d="M118 74l24 -22 14 10 26 -30" stroke="var(--mod-deep)" strokeWidth={4} fill="none" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M168 32h12v12" stroke="var(--mod-deep)" strokeWidth={4} fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </Svg>
  );
}

export const ILLUSTRATIONS: Record<ModuleId, ComponentType> = {
  ventas: RegisterIllustration,
  inventario: StockIllustration,
  compras: ReceivingIllustration,
  proveedores: TruckIllustration,
  caja: CashIllustration,
  informes: ReportIllustration,
};
