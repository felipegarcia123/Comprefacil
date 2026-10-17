"use client";

import { memo, useEffect, useRef } from "react";
import { ScanBarcode, Trash2 } from "lucide-react";
import { DataTable, QtyStepper, Td, Th, TouchButton, Tr } from "@/components/ui";
import { money } from "@/lib/format";
import type { Item, ManualItemKind } from "@/lib/types";

type Props = {
  items: Item[];
  selectedId: string;
  onSelect: (id: string) => void;
  priceLabel?: string;
  /** quantity <= 0 removes the line. */
  onChange: (itemId: string, quantity: number) => void;
};

/** Empty sale: an invitation to act (no illustration on the sales screen, by design). */
export function EmptyCart() {
  return (
    <div className="sale-empty">
      <span className="sale-empty__icon"><ScanBarcode size={44} aria-hidden="true" /></span>
      <h2>Venta vacía</h2>
      <p>Escanea un producto o búscalo por nombre. Aquí aparecerán las líneas.</p>
    </div>
  );
}

/**
 * Cart lines as a real table: fixed header, only the body scrolls, tabular figures, 48px controls.
 * Memoised so changing a quantity elsewhere (search box, payment panel) does not re-render the lines.
 */
/** "Producto manual" lines: which kind they are, under the description. */
const MANUAL_LABEL: Record<ManualItemKind, string> = { SUPER: "Manual · Súper", GRANEL: "Manual · Comida a granel", BULTO: "Manual · Bulto", PET_OTHER: "Manual · Mascotas otro" };

export const CartTable = memo(function CartTable({ items, selectedId, onSelect, onChange, priceLabel = "Precio" }: Props) {
  const selectedRow = useRef<HTMLTableRowElement>(null);
  useEffect(() => { selectedRow.current?.scrollIntoView?.({ block: "nearest" }); }, [selectedId, items.length]);

  return (
    <DataTable caption="Productos de la venta" className="sale-cart" module="ventas">
      <thead>
        <tr>
          <Th>Producto</Th>
          <Th className="sale-col-qty">Cantidad</Th>
          <Th numeric className="sale-col-price">{priceLabel}</Th>
          <Th numeric className="sale-col-total">Total</Th>
          <Th className="sale-col-del"><span className="ui-sr-only">Quitar</span></Th>
        </tr>
      </thead>
      <tbody>
        {items.map(item => {
          const quantity = Number(item.quantity);
          return (
            <Tr key={item.id} interactive selected={item.id === selectedId} onClick={() => onSelect(item.id)} ref={item.id === selectedId ? selectedRow : undefined}>
              <Td label="Producto" className="sale-col-name"><span className="sale-name">{item.product_name}</span>{item.manual_kind && <span className="sale-manual">{MANUAL_LABEL[item.manual_kind]}</span>}</Td>
              <Td className="sale-col-qty"><QtyStepper value={quantity} label={item.product_name} onChange={next => onChange(item.id, next)} /></Td>
              <Td numeric muted label={priceLabel} className="sale-col-price">{money.format(Number(item.unit_price))}</Td>
              <Td numeric label="Total" className="sale-col-total"><strong key={item.line_total} className="sale-tick">{money.format(Number(item.line_total))}</strong></Td>
              <Td className="sale-col-del">
                <TouchButton variant="ghost" iconOnly icon={Trash2} label={`Quitar ${item.product_name}`} className="sale-del" onClick={event => { event.stopPropagation(); onChange(item.id, 0); }} />
              </Td>
            </Tr>
          );
        })}
      </tbody>
    </DataTable>
  );
});
