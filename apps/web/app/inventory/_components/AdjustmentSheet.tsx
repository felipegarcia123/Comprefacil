"use client";

import { useEffect, useState } from "react";
import { Display, Sheet, TouchButton } from "@/components/ui";
import { errorText, formatQuantity } from "@/lib/format";
import type { InventoryDetail } from "@/lib/types";

type Props = {
  product: InventoryDetail | null;
  onClose: () => void;
  onSaved: (delta: number) => void;
  adjust: (productId: string, delta: number, reason: string) => Promise<unknown>;
};

/**
 * Manual stock adjustment — same rule as before: it records the FINAL counted quantity, never a
 * delta typed directly, and the backend turns it into `delta = counted - current` as an
 * InventoryMovement ADJUSTMENT (never writes product.stock). Requires an open jornada (enforced
 * by the backend guard already in place; this sheet does not duplicate that rule).
 */
export function AdjustmentSheet({ product, onClose, onSaved, adjust }: Props) {
  const [counted, setCounted] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (product) { setCounted(String(Math.trunc(Number(product.current_stock)))); setReason(""); setError(""); }
  }, [product?.id]);

  const current = product ? Number(product.current_stock) : 0;
  const target = Number(counted);
  const delta = counted.trim() !== "" && Number.isFinite(target) ? target - current : null;
  const valid = product !== null && counted.trim() !== "" && Number.isInteger(target) && target >= 0 && reason.trim().length >= 3 && delta !== 0;

  async function submit() {
    if (!product || !valid || delta === null) return;
    setBusy(true); setError("");
    try { await adjust(product.id, delta, reason.trim()); onSaved(delta); }
    catch (cause) { setError(errorText(cause)); } finally { setBusy(false); }
  }

  return (
    <Sheet open={product !== null} title="Ajustar inventario" persistent module="inventario" onClose={onClose}>
      {product && (
        <div className="ui-form">
          <p className="purchase-detail-code">{product.name} · {product.barcode ?? "Sin código"}</p>
          <Display size="figure" label="Stock actual" value={formatQuantity(product.current_stock)} caption={product.unit} />
          <label className="ui-field"><span>Cantidad real contada</span>
            <input autoFocus type="number" inputMode="numeric" min="0" step="1" value={counted}
              onChange={event => { setCounted(event.target.value); setError(""); }} />
            <small className="inventory-form-hint">Escribe el total que debe quedar, no las unidades a sumar.</small>
          </label>
          <label className="ui-field"><span>Motivo</span><textarea rows={2} value={reason} onChange={event => { setReason(event.target.value); setError(""); }} /></label>
          {error && <p className="ui-field-error" role="alert">{error}</p>}
          {delta !== null && (
            <p className="inventory-delta-preview">Movimiento a registrar: <b className={delta > 0 ? "pos" : delta < 0 ? "neg" : ""}>{delta > 0 ? "+" : ""}{delta}</b></p>
          )}
          <div className="ui-form__actions">
            <TouchButton variant="secondary" size="lg" onClick={onClose} disabled={busy}>Cancelar</TouchButton>
            <TouchButton size="lg" module="inventario" disabled={!valid || busy} loading={busy} onClick={() => void submit()}>Registrar ajuste</TouchButton>
          </div>
        </div>
      )}
    </Sheet>
  );
}
