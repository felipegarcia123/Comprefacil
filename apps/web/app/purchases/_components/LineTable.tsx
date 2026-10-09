"use client";

import { memo, useEffect, useRef } from "react";
import { PackageOpen, Trash2 } from "lucide-react";
import { DataTable, QtyStepper, Td, Th, TouchButton, Tr } from "@/components/ui";
import { money } from "@/lib/format";
import { useBreakpoint } from "@/lib/useBreakpoint";
import { lineTotal, type DraftLine } from "./types";

type Props = {
  lines: DraftLine[];
  selectedId: string;
  onSelect: (id: string) => void;
  onQuantity: (id: string, quantity: number) => void;
  /** Opens the pricing detail sheet for that line (cost, margin, suggested price). */
  onDetail: (id: string) => void;
  onRemove: (id: string) => void;
  /** The very last remaining line can't be removed here (an empty draft can't be saved); cancel the purchase instead. */
  removeDisabled: boolean;
};

export function EmptyLines() {
  return (
    <div className="sale-empty" data-module="compras">
      <span className="sale-empty__icon"><PackageOpen size={44} aria-hidden="true" /></span>
      <h2>Compra sin productos</h2>
      <p>Escanea o busca el primer producto para empezar a registrar la mercancía.</p>
    </div>
  );
}

/**
 * Dense purchase-line table: Producto | Cantidad | Costo unitario | Subtotal | Quitar. Memoised
 * like Ventas' cart table.
 *
 * Section 11 (Fase 6): entre 900-1199 el panel lateral deja ~636-700px para esta tabla, y Cantidad
 * (dos teclas de 48px + input) no puede bajar de ~190px sin violar el touch target — de ahí el
 * scroll horizontal residual. Se resolvió con dos cambios reales, no con texto minúsculo: (a) el
 * botón de detalle de precio se quitó de Acciones (ya se abre tocando el costo, ver más abajo),
 * liberando una columna entera; (b) en ese mismo rango, Costo unitario y Subtotal se muestran
 * apilados en una sola celda ("mostrar costo/subtotal en dos niveles", la opción que el propio
 * encargo sugería) en vez de dos columnas separadas.
 */
export const LineTable = memo(function LineTable({ lines, selectedId, onSelect, onQuantity, onDetail, onRemove, removeDisabled }: Props) {
  const selectedRow = useRef<HTMLTableRowElement>(null);
  const stacked = useBreakpoint() === "laptop"; // 900-1199: costo + subtotal en una celda
  useEffect(() => { selectedRow.current?.scrollIntoView?.({ block: "nearest" }); }, [selectedId, lines.length]);

  return (
    <DataTable caption="Productos de la compra" className="sale-cart purchase-cart" module="compras">
      <thead>
        <tr>
          <Th>Producto</Th>
          <Th className="sale-col-qty">Cantidad</Th>
          {stacked ? (
            <Th numeric className="purchase-col-cost-stacked">Costo / Subtotal</Th>
          ) : (
            <>
              <Th numeric className="purchase-col-cost">Costo unitario</Th>
              <Th numeric className="sale-col-total">Subtotal</Th>
            </>
          )}
          <Th className="purchase-col-actions"><span className="ui-sr-only">Acciones</span></Th>
        </tr>
      </thead>
      <tbody>
        {lines.map(line => (
          <Tr key={line.id} interactive selected={line.id === selectedId} onClick={() => onSelect(line.id)} ref={line.id === selectedId ? selectedRow : undefined}>
            <Td label="Producto" className="sale-col-name"><span className="sale-name">{line.name}<small className="ui-td--muted">{line.barcode ?? "Sin código"}</small></span></Td>
            <Td className="sale-col-qty"><QtyStepper value={line.quantity} label={line.name} onChange={next => onQuantity(line.id, next)} /></Td>
            {stacked ? (
              <Td numeric label="Costo / Subtotal" className="purchase-col-cost-stacked">
                <button type="button" className="purchase-cost-btn purchase-cost-btn--stacked ui-num" onClick={event => { event.stopPropagation(); onDetail(line.id); }}>
                  <small>{money.format(line.purchase_cost)} c/u</small>
                  <strong>{money.format(lineTotal(line))}</strong>
                </button>
              </Td>
            ) : (
              <>
                <Td numeric label="Costo unitario" className="purchase-col-cost">
                  <button type="button" className="purchase-cost-btn ui-num" onClick={event => { event.stopPropagation(); onDetail(line.id); }}>{money.format(line.purchase_cost)}</button>
                </Td>
                <Td numeric label="Subtotal" className="sale-col-total"><strong className="ui-num">{money.format(lineTotal(line))}</strong></Td>
              </>
            )}
            <Td className="purchase-col-actions">
              {/* El detalle de precio ya se abre tocando el costo (celda anterior) — un solo botón
                 "Quitar" aquí evita duplicar el control y libera ancho a Producto. */}
              <TouchButton
                variant="ghost" iconOnly icon={Trash2} label={`Quitar ${line.name}`} className="sale-del"
                disabled={removeDisabled && lines.length === 1}
                onClick={event => { event.stopPropagation(); onRemove(line.id); }}
              />
            </Td>
          </Tr>
        ))}
      </tbody>
    </DataTable>
  );
});
