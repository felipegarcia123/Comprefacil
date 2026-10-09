import { Toldo } from "./Toldo";
import { MODULES, type ModuleId } from "./modules";

/** Compact "where am I" label: toldo pattern + module icon + context title (e.g. "Venta 2", "Recepción · Bavaria"). */
export function ContextBadge({ module, label }: { module: ModuleId; label?: string }) {
  const meta = MODULES[module];
  const Icon = meta.icon;
  return (
    <span className="ui-context" data-module={module}>
      <Toldo module={module} />
      <span className="ui-context__body">
        <Icon size={20} aria-hidden="true" />
        {label ?? meta.label}
      </span>
    </span>
  );
}
