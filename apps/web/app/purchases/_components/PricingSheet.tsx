"use client";

import { useState } from "react";
import { Sheet, TouchButton } from "@/components/ui";
import { api } from "@/lib/api";
import { errorText, formatQuantity, money, preciseMoney } from "@/lib/format";
import { grossMarginPercent, suggestedSalePrice, validTargetMargin, type RoundingPolicy } from "@/lib/pricing";
import { cx } from "@/lib/cx";
import { lineTotal, type DraftLine } from "./types";

type Props = {
  line: DraftLine | null;
  policy: RoundingPolicy;
  onClose: () => void;
  /** Commits quantity/cost/target_margin back onto the line (unit_cost/quantity feed the purchase; target_margin is UI-only). */
  onChange: (id: string, patch: Partial<Pick<DraftLine, "quantity" | "purchase_cost" | "target_margin">>) => void;
  notify: (message: string) => void;
};

/**
 * Cost/margin decision panel for one line — unchanged from the pre-redesign ProductModal
 * (previous cost, cost variation, current margin, target margin, mathematical/suggested sale
 * price, "apply suggested price"), only restyled into a Sheet. Receiving a purchase never
 * changes sale_price automatically; this panel is the one place that can, and only on request.
 */
export function PricingSheet({ line, policy, onClose, onChange, notify }: Props) {
  const [marginInput, setMarginInput] = useState(() => String(line?.target_margin ?? 30));
  const [confirmApply, setConfirmApply] = useState(false);
  const [applying, setApplying] = useState(false);

  if (!line) return <Sheet open={false} title="Detalle de precio" onClose={onClose} module="compras" />;

  const previousCost = line.last_purchase_cost === null ? null : Number(line.last_purchase_cost);
  const salePrice = Number(line.sale_price);
  const currentMargin = grossMarginPercent(salePrice, line.purchase_cost);
  const variation = previousCost !== null && previousCost > 0 ? ((line.purchase_cost - previousCost) / previousCost) * 100 : null;
  const marginValue = marginInput.trim() === "" ? Number.NaN : Number(marginInput);
  const marginValid = validTargetMargin(marginValue);
  const pricing = marginValid ? suggestedSalePrice(line.purchase_cost, marginValue, policy) : null;
  const projectedStock = Number(line.current_stock) + (Number.isFinite(line.quantity) ? line.quantity : 0);

  function commitMargin() { if (marginValid) onChange(line!.id, { target_margin: marginValue }); }

  async function applySuggested() {
    if (!pricing) return;
    setApplying(true);
    try {
      const applied = await api.applySuggestedPrice(line!.id, line!.purchase_cost, marginValue);
      notify(`Precio de venta de ${line!.name} actualizado a ${money.format(Number(applied.sale_price))}`);
      setConfirmApply(false); onClose();
    } catch (error) { notify(errorText(error)); } finally { setApplying(false); }
  }

  return (
    <Sheet open={Boolean(line)} title={line.name} onClose={onClose} module="compras">
      <div className="ui-stack">
        <p className="purchase-detail-code">{line.barcode ? `Código ${line.barcode}` : "Sin código"}</p>
        <div className="purchase-decision-grid">
          <section>
            <h3>Existencias</h3>
            <Metric label="Stock actual" value={formatQuantity(Number(line.current_stock))} />
            <label className="ui-field"><span>Cantidad a recibir</span>
              <input type="number" min="0.001" step="0.001" value={line.quantity} onChange={event => onChange(line.id, { quantity: Number(event.target.value) })} />
            </label>
            <Metric label="Stock después de recibir" value={formatQuantity(projectedStock)} accent />
          </section>
          <section>
            <h3>Compra</h3>
            <Metric label="Último costo de compra" value={previousCost === null ? "Sin costo anterior" : money.format(previousCost)} />
            <label className="ui-field"><span>Costo unitario nuevo</span>
              <input type="number" min="0" step="0.01" value={line.purchase_cost} onChange={event => onChange(line.id, { purchase_cost: Number(event.target.value) })} />
            </label>
            <Metric label="Variación del costo" value={variation === null ? "Sin costo anterior" : `${variation >= 0 ? "+" : ""}${variation.toFixed(1)}%`} tone={variation !== null && variation > 0 ? "warning" : undefined} />
          </section>
          <section className="purchase-pricing">
            <h3>Venta y margen</h3>
            <Metric label="Precio de venta actual" value={money.format(salePrice)} />
            <Metric label="Margen actual (nuevo costo)" value={currentMargin === null ? "No calculable" : `${currentMargin.toFixed(1)}%`} tone={currentMargin !== null && currentMargin <= 0 ? "danger" : currentMargin !== null && marginValid && currentMargin < marginValue ? "warning" : undefined} />
            <label className="ui-field">
              <span>Margen objetivo (%)</span>
              <input className={!marginValid ? "ui-field--invalid" : ""} type="number" inputMode="decimal" step="any" value={marginInput} aria-invalid={!marginValid} onChange={event => setMarginInput(event.target.value)} onBlur={commitMargin} />
              {!marginValid && <small className="ui-field-error">El margen debe ser mayor que 0% y menor que 100%.</small>}
            </label>
            <Metric label="Precio matemático" value={pricing ? preciseMoney.format(pricing.calculated) : "—"} />
            <Metric label={`Precio sugerido (${policy})`} value={pricing ? money.format(pricing.suggested) : "—"} accent />
            <TouchButton variant="secondary" size="lg" block disabled={!pricing} onClick={() => setConfirmApply(true)}>Aplicar precio sugerido</TouchButton>
          </section>
        </div>
        <div className="purchase-warning-row">
          {variation !== null && variation > 0 && <span>El costo aumentó {variation.toFixed(1)}%</span>}
          {currentMargin !== null && marginValid && currentMargin < marginValue && <span>Margen actual por debajo del objetivo</span>}
          {line.purchase_cost >= salePrice && <strong>El precio de venta actual no cubre adecuadamente el costo</strong>}
        </div>
        <div className="purchase-subtotal">
          <span>Subtotal · {formatQuantity(line.quantity)} × {money.format(line.purchase_cost)}</span>
          <strong className="ui-num">{money.format(lineTotal(line))}</strong>
        </div>
      </div>

      {confirmApply && pricing && (
        <div className="ui-inline-confirm" role="alertdialog" aria-label="Confirmar cambio de precio">
          <p>¿Actualizar el precio de venta de <b>{line.name}</b> a <b>{money.format(pricing.suggested)}</b>? Este cambio es inmediato y afecta la venta al público, no la compra.</p>
          <div className="ui-form__actions">
            <TouchButton variant="secondary" onClick={() => setConfirmApply(false)} disabled={applying}>Cancelar</TouchButton>
            <TouchButton module="compras" onClick={() => void applySuggested()} loading={applying}>Actualizar precio</TouchButton>
          </div>
        </div>
      )}
    </Sheet>
  );
}

function Metric({ label, value, accent, tone }: { label: string; value: string; accent?: boolean; tone?: "warning" | "danger" }) {
  return <div className={cx("purchase-metric", accent && "purchase-metric--accent", tone && `purchase-metric--${tone}`)}><span>{label}</span><b className="ui-num">{value}</b></div>;
}
