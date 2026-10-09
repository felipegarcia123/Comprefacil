import type { ModuleId } from "./modules";

/** Striped band whose pattern identifies the module without depending on color. Decorative. */
export function Toldo({ module }: { module?: ModuleId }) {
  return <span className="ui-toldo" data-pattern={module} aria-hidden="true" />;
}
