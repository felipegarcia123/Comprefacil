"use client";

import { useState } from "react";
import { Sheet, TouchButton } from "@/components/ui";
import { api } from "@/lib/api";
import { errorText, money, preciseMoney } from "@/lib/format";
import { grossMarginPercent, suggestedSalePrice, type RoundingPolicy } from "@/lib/pricing";
import type { InventoryDetail } from "@/lib/types";

const PRESETS = [20, 30] as const;

type Props = {
  product: InventoryDetail | null;
  policy: RoundingPolicy;
  onClose: () => void;
  onApplied: (salePrice: string) => void;
};

/**
 * Standalone pricing detail for a catalog product (not tied to a purchase in progress): costo,
 * precio actual, margen actual, más objetivos rápidos 20%/30% con precio matemático y comercial —
 * mismas fórmulas que Compras (`lib/pricing.ts`), nunca cambia `sale_price` salvo pedido explícito.
 */
export function ProductPricingSheet({ product, policy, onClose, onApplied }: Props) {
  const [margin, setMargin] = useState<number>(30);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (!product) return <Sheet open={false} title="Precio y margen" onClose={onClose} module="inventario" />;

  const cost = Number(product.unit_cost);
  const salePrice = Number(product.sale_price);
  const currentMargin = grossMarginPercent(salePrice, cost);
  const pricing = suggestedSalePrice(cost, margin, policy);

  async function apply() {
    setBusy(true); setError("");
    try {
      const applied = await api.applySuggestedPrice(product!.id, cost, margin);
      onApplied(applied.sale_price); setConfirming(false); onClose();
    } catch (cause) { setError(errorText(cause)); } finally { setBusy(false); }
  }

  return (
    <Sheet open title="Precio y margen" module="inventario" onClose={onClose}>
      <div className="ui-stack">
        <p className="purchase-detail-code">{product.name}</p>
        <div className="purchase-metric"><span>Costo actual</span><b className="ui-num">{money.format(cost)}</b></div>
        <div className="purchase-metric"><span>Precio de venta actual</span><b className="ui-num">{money.format(salePrice)}</b></div>
        <div className="purchase-metric"><span>Margen actual</span><b className="ui-num">{currentMargin === null ? "No calculable" : `${currentMargin.toFixed(1)}%`}</b></div>

        <p className="inventory-form-section">Objetivo de margen</p>
        <div className="ui-row">
          {PRESETS.map(value => (
            <TouchButton key={value} variant={margin === value ? "primary" : "secondary"} module="inventario" onClick={() => setMargin(value)}>{value}%</TouchButton>
          ))}
          <label className="ui-field" style={{ flex: 1, minWidth: 120 }}>
            <span>Otro (%)</span>
            <input type="number" inputMode="decimal" min="1" max="99" value={margin} onChange={event => setMargin(Number(event.target.value))} />
          </label>
        </div>

        <div className="purchase-metric"><span>Precio matemático</span><b className="ui-num">{pricing ? preciseMoney.format(pricing.calculated) : "—"}</b></div>
        <div className="purchase-metric purchase-metric--accent"><span>{`Precio comercial (${policy})`}</span><b className="ui-num">{pricing ? money.format(pricing.suggested) : "—"}</b></div>

        {error && <p className="ui-field-error" role="alert">{error}</p>}
        <TouchButton module="inventario" size="lg" disabled={!pricing} onClick={() => setConfirming(true)}>Aplicar precio comercial</TouchButton>
      </div>

      {confirming && pricing && (
        <div className="ui-inline-confirm" role="alertdialog" aria-label="Confirmar cambio de precio">
          <p>¿Actualizar el precio de venta de <b>{product.name}</b> de {money.format(salePrice)} a <b>{money.format(pricing.suggested)}</b>? Es inmediato.</p>
          <div className="ui-form__actions">
            <TouchButton variant="secondary" onClick={() => setConfirming(false)} disabled={busy}>Cancelar</TouchButton>
            <TouchButton module="inventario" onClick={() => void apply()} loading={busy}>Actualizar precio</TouchButton>
          </div>
        </div>
      )}
    </Sheet>
  );
}
