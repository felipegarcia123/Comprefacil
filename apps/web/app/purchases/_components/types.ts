/** One purchase line being edited. Mirrors PurchaseItemIn (product_id/quantity/unit_cost) plus
 * the read-only context (name, barcode, current sale price, previous cost) the pricing detail needs. */
export type DraftLine = {
  id: string;
  barcode: string | null;
  name: string;
  sale_price: string;
  current_stock: string;
  last_purchase_cost: string | null;
  quantity: number;
  purchase_cost: number;
  /** UI-only target margin used to compute a suggested sale price; never sent to the backend. */
  target_margin: number;
};

/** Minimal shape a product lookup (barcode scan or name search) needs to provide to seed a new line. */
export type ProductSeed = { id: string; barcode: string | null; name: string; sale_price: string; unit_cost: string; last_purchase_cost: string | null; current_stock: string };

export function seedToLine(seed: ProductSeed, existing?: DraftLine): DraftLine {
  return {
    id: seed.id, barcode: seed.barcode, name: seed.name, sale_price: seed.sale_price,
    current_stock: seed.current_stock, last_purchase_cost: seed.last_purchase_cost,
    quantity: (existing?.quantity ?? 0) + 1,
    purchase_cost: existing?.purchase_cost ?? Number(seed.unit_cost),
    target_margin: existing?.target_margin ?? 30,
  };
}

export function lineTotal(line: DraftLine): number {
  return line.quantity * line.purchase_cost;
}
