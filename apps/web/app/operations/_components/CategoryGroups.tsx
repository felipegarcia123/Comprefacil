import { money } from "@/lib/format";
import type { DailyOperationsSummary, OperationalCategory } from "@/lib/types";

type CategoryRow = DailyOperationsSummary["categories"][number];

// Fixed presentation order for the business's operating groups, independent of
// OperationalCategory.display_order (a seed-time default, not a UX decision).
const GROUP_ORDER = ["CIGARROS", "LICORES", "MASCOTAS_CON_CODIGO", "MASCOTAS_SIN_CODIGO", "BULTOS", "SUPER"];
const ZERO_ROW = { cash: "0", electronic: "0", other: "0", total: "0", channels: [] as CategoryRow["channels"] };

export function groupRows(summary: DailyOperationsSummary | null, categories: OperationalCategory[]) {
  const byCode = new Map((summary?.categories ?? []).map(row => [row.category_code, row]));
  const nameByCode = new Map(categories.map(c => [c.code, c.name]));
  const primary = GROUP_ORDER.map(code => byCode.get(code) ?? {
    category_code: code, category_name: nameByCode.get(code) ?? code, ...ZERO_ROW,
  });
  // SIN_CLASIFICAR (and any other code outside the fixed groups) is shown separately, never merged into SUPER.
  const extra = (summary?.categories ?? []).filter(row => !GROUP_ORDER.includes(row.category_code) && Number(row.total) !== 0);
  return { primary, extra };
}

/** GRUPO · EFECTIVO · ELECTRÓNICO · TOTAL, expandable to the exact electronic channel (section 9). Cards, not a dense table — explicitly allowed for Resumen operativo. */
export function CategoryGroups({ primary, extra }: { primary: CategoryRow[]; extra: CategoryRow[] }) {
  return (
    <div className="operations-groups">
      <div className="operations-groups__head"><span>Grupo</span><span>Efectivo</span><span>Electrónico</span><span>Total</span></div>
      {primary.map(row => <CategoryGroupRow key={row.category_code} row={row} />)}
      {extra.length > 0 && (
        <div className="operations-groups__extra">
          <small>Otras ventas sin familia asignada</small>
          {extra.map(row => <CategoryGroupRow key={row.category_code} row={row} />)}
        </div>
      )}
    </div>
  );
}

function CategoryGroupRow({ row }: { row: CategoryRow }) {
  const detail = [...row.channels].filter(channel => channel.payment_group !== "CASH").sort((a, b) => Number(b.amount) - Number(a.amount));
  if (Number(row.total) === 0) {
    return <div className="operations-groups__row operations-groups__row--flat"><strong>{row.category_name}</strong><span className="ui-num">{money.format(0)}</span><span className="ui-num">{money.format(0)}</span><b className="ui-num">{money.format(0)}</b></div>;
  }
  return (
    <details className="operations-groups__row">
      <summary><strong>{row.category_name}</strong><span className="ui-num">{money.format(Number(row.cash))}</span><span className="ui-num">{money.format(Number(row.electronic))}</span><b className="ui-num">{money.format(Number(row.total))}</b></summary>
      {detail.length > 0 && (
        <div className="operations-groups__detail">
          <small>Detalle electrónico</small>
          {detail.map(channel => <span key={channel.channel_id}>{channel.channel_name}<b className="ui-num">{money.format(Number(channel.amount))}</b></span>)}
        </div>
      )}
    </details>
  );
}

export function CategoryTotals({ cash, electronic, total }: { cash: number; electronic: number; total: number }) {
  return (
    <footer className="operations-groups__totals">
      <span className="operations-groups__total-item"><small>Efectivo</small><b className="ui-num">{money.format(cash)}</b></span>
      <span className="operations-groups__total-item"><small>Electrónico</small><b className="ui-num">{money.format(electronic)}</b></span>
      <span className="operations-groups__grand-total"><small>Total vendido</small><strong className="ui-num">{money.format(total)}</strong></span>
    </footer>
  );
}
