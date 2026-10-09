"use client";

import { memo } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";
import { DataTable, StatusBadge, Td, Th, Tr } from "@/components/ui";
import { formatQuantity, money } from "@/lib/format";
import type { InventoryProduct, InventorySort } from "@/lib/types";

type Props = {
  items: InventoryProduct[];
  selectedId: string;
  onSelect: (product: InventoryProduct) => void;
  sort: InventorySort;
  sortDir: "asc" | "desc";
  onSort: (sort: InventorySort) => void;
  showCategory: boolean;
  /** <640px: stacked rows, Producto/Stock/Precio/Estado only — a squeezed 6-column table doesn't fit a phone. */
  compact: boolean;
};

/** Stock state: never color alone — icon-less StatusBadge with explicit text, driven by the same `minimum_stock` rule the backend already uses. */
export function stockTone(product: Pick<InventoryProduct, "active" | "current_stock" | "minimum_stock">): { tone: "neutral" | "success" | "warning" | "danger"; label: string } {
  if (!product.active) return { tone: "neutral", label: "Inactivo" };
  const stock = Number(product.current_stock);
  if (stock <= 0) return { tone: "danger", label: "Agotado" };
  if (stock <= product.minimum_stock) return { tone: "warning", label: "Stock bajo" };
  return { tone: "success", label: "Normal" };
}

function SortHeader({ label, field, sort, sortDir, onSort, numeric }: { label: string; field: InventorySort; sort: InventorySort; sortDir: "asc" | "desc"; onSort: (field: InventorySort) => void; numeric?: boolean }) {
  const active = field === sort;
  const Icon = sortDir === "asc" ? ArrowUp : ArrowDown;
  return (
    <Th numeric={numeric}>
      <button type="button" className="inventory-sort-btn" aria-pressed={active} onClick={() => onSort(field)}>
        {label}{active && <Icon size={14} aria-hidden="true" />}
      </button>
    </Th>
  );
}

/** Dense product listing: Producto | Stock | Costo | Precio | Margen | Estado (+ Categoría ≥1200, stacked Producto/Stock/Precio/Estado <640). Memoised: selecting a row never re-renders the whole page. */
export const ProductTable = memo(function ProductTable({ items, selectedId, onSelect, sort, sortDir, onSort, showCategory, compact }: Props) {
  return (
    <DataTable caption="Productos del inventario" className="inventory-cart" module="inventario" density="dense" stack={compact}>
      <thead>
        <tr>
          <SortHeader label="Producto" field="name" sort={sort} sortDir={sortDir} onSort={onSort} />
          {showCategory && <Th>Categoría</Th>}
          <SortHeader label="Stock" field="stock" sort={sort} sortDir={sortDir} onSort={onSort} numeric />
          {!compact && <SortHeader label="Costo" field="cost" sort={sort} sortDir={sortDir} onSort={onSort} numeric />}
          <SortHeader label="Precio" field="price" sort={sort} sortDir={sortDir} onSort={onSort} numeric />
          {!compact && <SortHeader label="Margen" field="margin" sort={sort} sortDir={sortDir} onSort={onSort} numeric />}
          <Th>Estado</Th>
        </tr>
      </thead>
      <tbody>
        {items.map(product => {
          const state = stockTone(product);
          const margin = product.estimated_margin_percent === null ? null : Number(product.estimated_margin_percent);
          return (
            <Tr key={product.id} interactive selected={product.id === selectedId} onClick={() => onSelect(product)}>
              <Td label="Producto"><span className="sale-name">{product.name}<small className="ui-td--muted">{product.barcode ?? "Sin código"}</small></span></Td>
              {showCategory && <Td label="Categoría" muted>{product.category ?? "Sin categoría"}</Td>}
              <Td numeric label="Stock" className="ui-num">{formatQuantity(product.current_stock)}</Td>
              {!compact && <Td numeric label="Costo" muted className="ui-num">{money.format(Number(product.unit_cost))}</Td>}
              <Td numeric label="Precio" className="ui-num">{money.format(Number(product.sale_price))}</Td>
              {!compact && <Td numeric label="Margen" className="ui-num">{margin === null ? "—" : `${margin.toFixed(0)}%`}</Td>}
              <Td label="Estado"><StatusBadge tone={state.tone}>{state.label}</StatusBadge></Td>
            </Tr>
          );
        })}
      </tbody>
    </DataTable>
  );
});
