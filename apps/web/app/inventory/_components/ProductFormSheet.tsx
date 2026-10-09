"use client";

import { useEffect, useState } from "react";
import { Sheet, TouchButton } from "@/components/ui";
import { errorText, formatQuantity } from "@/lib/format";
import type { InventoryDetail, OperationalCategory, ProductInput, Supplier } from "@/lib/types";

const blank: ProductInput = { barcode: "", name: "", category_id: null, supplier_id: null, purchase_cost: "0", sale_price: "", initial_stock: 0, minimum_stock: 0, unit: "unidad", description: "", active: true };
const UNITS = ["unidad", "paquete", "botella", "lata", "bulto"];

type Props = {
  /** undefined = closed; null = create; a product = edit. */
  target: InventoryDetail | null | undefined;
  categories: OperationalCategory[];
  suppliers: Supplier[];
  onClose: () => void;
  onSaved: () => void;
  create: (body: ProductInput & { initial_stock: number }) => Promise<unknown>;
  update: (id: string, body: ProductInput) => Promise<unknown>;
};

/** Create/edit product — same fields, validation and endpoints as before (`ProductModal`), restyled as a Sheet. */
export function ProductFormSheet({ target, categories, suppliers, onClose, onSaved, create, update }: Props) {
  const [form, setForm] = useState<ProductInput>(blank);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const editing = Boolean(target);

  useEffect(() => {
    setError("");
    setForm(target ? {
      barcode: target.barcode ?? "", name: target.name, category_id: target.category_id, supplier_id: target.supplier_id,
      purchase_cost: String(target.unit_cost), sale_price: String(target.sale_price), minimum_stock: target.minimum_stock,
      unit: target.unit, description: target.description ?? "", active: target.active,
    } : blank);
  }, [target]);

  function field<K extends keyof ProductInput>(key: K, value: ProductInput[K]) { setForm(current => ({ ...current, [key]: value })); }

  async function submit() {
    if (!form.name.trim() || Number(form.sale_price) <= 0) return;
    setBusy(true); setError("");
    // No code = a product sold by name (eggs, loose items). Never a made-up code.
    const body = { ...form, barcode: (form.barcode ?? "").trim() || null };
    try {
      if (editing && target) await update(target.id, body);
      else await create({ ...body, initial_stock: form.initial_stock ?? 0 });
      onSaved();
    } catch (cause) { setError(errorText(cause)); } finally { setBusy(false); }
  }

  const valid = form.name.trim().length > 0 && Number(form.sale_price) > 0;

  return (
    <Sheet open={target !== undefined} title={editing ? "Editar producto" : "Nuevo producto"} persistent module="inventario" onClose={onClose}>
      <div className="ui-form">
        <p className="inventory-form-section">Identificación</p>
        <label className="ui-field"><span>Código de barras (opcional)</span><input autoFocus value={form.barcode ?? ""} placeholder="Déjalo vacío si el producto no tiene código" onChange={event => field("barcode", event.target.value)} /></label>
        <label className="ui-field"><span>Nombre *</span><input value={form.name} onChange={event => field("name", event.target.value)} /></label>
        <label className="ui-field"><span>Categoría</span>
          <select value={form.category_id ?? ""} onChange={event => field("category_id", event.target.value || null)}>
            <option value="">Sin categoría</option>
            {categories.map(category => <option key={category.id} value={category.id}>{category.name}</option>)}
          </select>
        </label>
        <label className="ui-field"><span>Descripción</span><textarea rows={2} value={form.description ?? ""} onChange={event => field("description", event.target.value)} /></label>

        <p className="inventory-form-section">Comercial</p>
        <label className="ui-field"><span>Proveedor</span>
          <select value={form.supplier_id ?? ""} onChange={event => field("supplier_id", event.target.value || null)}>
            <option value="">Sin proveedor</option>
            {suppliers.map(supplier => <option key={supplier.id} value={supplier.id}>{supplier.name}</option>)}
          </select>
        </label>
        <label className="ui-field"><span>Costo de referencia</span><input type="number" min="0" step="any" value={form.purchase_cost} onChange={event => field("purchase_cost", event.target.value)} /></label>
        <label className="ui-field"><span>Precio de venta *</span><input type="number" min="0.01" step="any" value={form.sale_price} onChange={event => field("sale_price", event.target.value)} /></label>

        <p className="inventory-form-section">Inventario</p>
        {editing ? (
          <p className="inventory-readonly-stock">Stock actual (solo lectura): <b className="ui-num">{target ? formatQuantity(target.current_stock) : 0}</b> — usa "Ajustar inventario" para cambiarlo.</p>
        ) : (
          <label className="ui-field"><span>Stock inicial</span><input type="number" min="0" step="1" value={form.initial_stock ?? 0} onChange={event => field("initial_stock", Number(event.target.value))} /></label>
        )}
        <label className="ui-field"><span>Stock mínimo</span><input type="number" min="0" step="1" value={form.minimum_stock} onChange={event => field("minimum_stock", Number(event.target.value))} /></label>
        <label className="ui-field"><span>Unidad</span>
          <select value={form.unit} onChange={event => field("unit", event.target.value)}>{UNITS.map(unit => <option key={unit}>{unit}</option>)}</select>
        </label>
        <label className="ui-choice__item" style={{ display: "flex", flexDirection: "row", alignItems: "center", gap: 10 }}>
          <input type="checkbox" checked={form.active} onChange={event => field("active", event.target.checked)} /> Producto activo
        </label>

        {error && <p className="ui-field-error" role="alert">{error}</p>}
        <div className="ui-form__actions">
          <TouchButton variant="secondary" size="lg" onClick={onClose} disabled={busy}>Cancelar</TouchButton>
          <TouchButton size="lg" module="inventario" disabled={!valid || busy} loading={busy} onClick={() => void submit()}>{editing ? "Guardar cambios" : "Crear producto"}</TouchButton>
        </div>
      </div>
    </Sheet>
  );
}
