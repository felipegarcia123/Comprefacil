import { Pencil, SlidersHorizontal, Sparkles } from "lucide-react";
import { StatusBadge, TouchButton } from "@/components/ui";
import { formatQuantity, money, signed, time as timeFormat } from "@/lib/format";
import type { InventoryDetail } from "@/lib/types";
import { stockTone } from "./ProductTable";

/** A RETURN created by a product exchange is an "Entrada por cambio" for the user, never a money return. */
function movementLabel(movement: { movement_type: string; reason: string | null }): string {
  if (movement.movement_type === "RETURN" && movement.reason?.startsWith("Cambio ")) return "Entrada por cambio";
  return MOVEMENT_LABEL[movement.movement_type] ?? movement.movement_type;
}

const MOVEMENT_LABEL: Record<string, string> = {
  INITIAL_STOCK: "Stock inicial", PURCHASE: "Compra", SALE: "Venta", RETURN: "Devolución",
  DAMAGE: "Merma", ADJUSTMENT: "Ajuste", INTERNAL_CONSUMPTION: "Consumo interno", EXCHANGE_OUT: "Salida por cambio",
};

type Props = {
  product: InventoryDetail;
  onEdit: () => void;
  onAdjust: () => void;
  onPricing: () => void;
  onToggleActive: () => void;
  busy?: boolean;
};

/** Detail content shared by the inline panel (≥1200) and the Sheet (<1200) — one definition, two containers. */
export function ProductDetail({ product, onEdit, onAdjust, onPricing, onToggleActive, busy }: Props) {
  const state = stockTone(product);
  const margin = product.estimated_margin_percent === null ? null : Number(product.estimated_margin_percent);

  return (
    <div className="inventory-detail">
      <div className="inventory-detail__head">
        <div><h2>{product.name}</h2><p className="purchase-detail-code">{product.barcode ?? "Sin código"}</p></div>
        <StatusBadge tone={state.tone}>{state.label}</StatusBadge>
      </div>

      <div className="purchase-metric"><span>Categoría</span><b>{product.category ?? "Sin categoría"}</b></div>
      <div className="purchase-metric"><span>Proveedor</span><b>{product.supplier ?? "Sin proveedor"}</b></div>
      <div className="purchase-metric"><span>Stock actual</span><b className="ui-num">{formatQuantity(product.current_stock)} {product.unit}</b></div>
      <div className="purchase-metric"><span>Stock mínimo</span><b className="ui-num">{product.minimum_stock}</b></div>
      <div className="purchase-metric"><span>Costo de referencia</span><b className="ui-num">{money.format(Number(product.unit_cost))}</b></div>
      <div className="purchase-metric"><span>Último costo real</span><b className="ui-num">{product.last_purchase_cost === null ? "Sin compras" : money.format(Number(product.last_purchase_cost))}</b></div>
      <div className="purchase-metric"><span>Precio de venta</span><b className="ui-num">{money.format(Number(product.sale_price))}</b></div>
      <div className="purchase-metric purchase-metric--accent"><span>Margen actual</span><b className="ui-num">{margin === null ? "No calculable" : `${margin.toFixed(1)}%`}</b></div>

      <div className="inventory-detail__actions">
        <TouchButton variant="secondary" icon={Pencil} onClick={onEdit}>Editar</TouchButton>
        <TouchButton variant="secondary" icon={SlidersHorizontal} onClick={onPricing}>Precio y margen</TouchButton>
        <TouchButton module="inventario" icon={Sparkles} onClick={onAdjust}>Ajustar inventario</TouchButton>
        <TouchButton variant={product.active ? "danger" : "primary"} onClick={onToggleActive} disabled={busy} loading={busy}>{product.active ? "Desactivar" : "Activar"}</TouchButton>
      </div>

      <p className="inventory-form-section">Movimientos recientes</p>
      <ul className="inventory-movement-list">
        {product.recent_movements.map(movement => (
          <li key={movement.id}>
            <span>{movementLabel(movement)}<small>{timeFormat.format(new Date(movement.created_at))} · {new Date(movement.created_at).toLocaleDateString("es-CO")}</small></span>
            <b className={Number(movement.quantity) >= 0 ? "pos ui-num" : "neg ui-num"}>{signed(Number(movement.quantity))}</b>
          </li>
        ))}
        {!product.recent_movements.length && <p className="ui-empty-hint">Sin movimientos todavía.</p>}
      </ul>
    </div>
  );
}
