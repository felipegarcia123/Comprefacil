import { ChevronLeft, ChevronRight } from "lucide-react";
import { TouchButton } from "./TouchButton";
import type { ModuleId } from "./modules";

type Props = {
  page: number;
  totalPages: number;
  total: number;
  itemLabel?: string;
  module?: ModuleId;
  onChange: (page: number) => void;
};

/** Server-side pagination controls: Anterior/Siguiente + "Página X de Y" + total count. Large touch targets. */
export function Pagination({ page, totalPages, total, itemLabel = "resultados", module, onChange }: Props) {
  return (
    <div className="ui-pagination" data-module={module}>
      <span className="ui-pagination__count ui-num">{total.toLocaleString("es-CO")} {itemLabel}</span>
      <div className="ui-pagination__nav">
        <TouchButton variant="secondary" iconOnly icon={ChevronLeft} label="Página anterior" disabled={page <= 1} onClick={() => onChange(page - 1)} />
        <span className="ui-pagination__page ui-num">Página {page} de {Math.max(totalPages, 1)}</span>
        <TouchButton variant="secondary" iconOnly icon={ChevronRight} label="Página siguiente" disabled={page >= totalPages} onClick={() => onChange(page + 1)} />
      </div>
    </div>
  );
}
