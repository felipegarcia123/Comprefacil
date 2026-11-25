import type { Purchase, PurchasePaymentTerms, PurchaseStatus, ReceivedItemIn, SchedulePurchaseIn } from "./types";

export const purchaseStatusLabel: Record<PurchaseStatus, string> = {
  DRAFT: "Borrador", SCHEDULED: "Programado", RECEIVED: "Recibida", CANCELLED: "Cancelada",
};

export const paymentTermsLabel: Record<PurchasePaymentTerms, string> = {
  CASH_ON_DELIVERY: "Efectivo contra entrega",
  DIGITAL_ON_DELIVERY: "Pago digital contra entrega",
  CREDIT: "Crédito del proveedor",
};

export function businessDate(offsetDays = 0): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Bogota", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
  const date = new Date(Date.UTC(Number(values.year), Number(values.month) - 1, Number(values.day) + offsetDays));
  return date.toISOString().slice(0, 10);
}

export function receiptRequestIdentity(previous: { signature: string; id: string } | null,
                                       signature: string, nextId: () => string) {
  return previous?.signature === signature ? previous : { signature, id: nextId() };
}

/** Exact decimal parser for the receipt preview. FastAPI remains the authority on the final total. */
export function scaledDecimal(value: string, places: number): bigint | null {
  if (!/^\d+(?:\.\d+)?$/.test(value)) return null;
  const [whole, fraction = ""] = value.split(".");
  if (fraction.length > places) return null;
  return BigInt(whole) * 10n ** BigInt(places) + BigInt(fraction.padEnd(places, "0") || "0");
}

export function scheduleErrors(purchase: Purchase, body: SchedulePurchaseIn): string[] {
  const errors: string[] = [];
  if (!purchase.id || !purchase.items.length) errors.push("Guarda al menos un producto antes de programar.");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(body.expected_delivery_date)) errors.push("Indica la fecha prevista de entrega.");
  if (body.payment_terms === "CREDIT") {
    if (!body.payment_due_date || body.payment_due_date <= body.expected_delivery_date) errors.push("El vencimiento debe ser posterior a la entrega prevista.");
  } else if (body.payment_due_date) errors.push("Solo un pedido a crédito puede tener vencimiento.");
  for (const offer of body.offers) {
    const item = purchase.items.find(line => line.product_id === offer.product_id);
    if (!item) { errors.push("La oferta no corresponde a un producto del pedido."); continue; }
    const hasList = offer.list_unit_cost !== null && offer.list_unit_cost !== undefined && offer.list_unit_cost !== "";
    const hasDiscount = offer.discount_per_unit !== null && offer.discount_per_unit !== undefined && offer.discount_per_unit !== "";
    const list = hasList ? scaledDecimal(offer.list_unit_cost!, 2) : null;
    const discount = hasDiscount ? scaledDecimal(offer.discount_per_unit!, 2) : null;
    if (hasList !== hasDiscount || (hasList && (list === null || discount === null ||
        discount > list || list - discount !== scaledDecimal(item.unit_cost, 2)))) {
      errors.push(`Revisa el precio de lista y el descuento de ${item.product_name}.`);
    }
  }
  return errors;
}

/** Line rounding: quantity has 3 decimals, net unit cost has 2, half up to cents. */
export function receiptPreview(purchase: Purchase, rows: ReceivedItemIn[]) {
  const errors: string[] = [];
  const byProduct = new Map(rows.map(row => [row.product_id, row]));
  if (byProduct.size !== rows.length || byProduct.size !== purchase.items.length ||
      purchase.items.some(item => !byProduct.has(item.product_id))) {
    errors.push("Incluye exactamente una vez cada producto del pedido.");
  }
  let totalCents = 0n;
  let totalQuantityMilli = 0n;
  let differences = 0;
  for (const item of purchase.items) {
    const row = byProduct.get(item.product_id);
    if (!row) continue;
    const qty = scaledDecimal(row.received_quantity, 3);
    const cost = row.actual_unit_cost === null ? null : scaledDecimal(row.actual_unit_cost, 2);
    const plannedQty = scaledDecimal(item.ordered_quantity ?? item.quantity, 3);
    const plannedCost = scaledDecimal(item.ordered_unit_cost ?? item.unit_cost, 2);
    if (qty === null || plannedQty === null || plannedCost === null ||
        (qty > 0n && cost === null) || (qty === 0n && row.actual_unit_cost !== null)) {
      errors.push(`Revisa la cantidad y el costo real de ${item.product_name}.`);
      continue;
    }
    const changed = qty !== plannedQty || (qty > 0n && cost !== plannedCost);
    if (changed) differences++;
    if (changed && !row.difference_reason?.trim()) errors.push(`Explica la diferencia de ${item.product_name}.`);
    if (!changed && row.difference_reason?.trim()) errors.push(`Quita el motivo de ${item.product_name}: no hay diferencia.`);
    totalQuantityMilli += qty;
    if (qty > 0n && cost !== null) totalCents += (qty * cost + 500n) / 1000n;
  }
  if (totalQuantityMilli === 0n) errors.push("No se puede recibir un pedido sin mercancía.");
  return { errors, totalCents, totalQuantityMilli, differences };
}
