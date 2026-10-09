"use client";

import { useEffect, useState, type FormEvent } from "react";
import { Sheet, TouchButton } from "@/components/ui";
import { cx } from "@/lib/cx";
import { api } from "@/lib/api";
import { errorText } from "@/lib/format";
import type { Product } from "@/lib/types";

type Body = { product_id?: string; quantity?: number; description: string; estimated_value?: number; notes?: string };
type Props = { open: boolean; busy: boolean; onClose: () => void; onSubmit: (body: Body) => Promise<void> };

/**
 * Casa / consumo interno — nunca se mezcla con ventas (section 7/10). "Producto inventariado"
 * descuenta stock real vía InventoryMovement INTERNAL_CONSUMPTION (create_internal_consumption);
 * "Registro manual" solo deja constancia de un valor estimado, sin tocar inventario.
 */
export function ConsumptionSheet({ open, busy, onClose, onSubmit }: Props) {
  const [formal, setFormal] = useState(true);
  const [query, setQuery] = useState(""); const [results, setResults] = useState<Product[]>([]); const [product, setProduct] = useState<Product | null>(null);
  const [quantity, setQuantity] = useState(""); const [description, setDescription] = useState(""); const [value, setValue] = useState(""); const [notes, setNotes] = useState("");
  const [error, setError] = useState("");

  useEffect(() => { if (open) { setFormal(true); setQuery(""); setResults([]); setProduct(null); setQuantity(""); setDescription(""); setValue(""); setNotes(""); setError(""); } }, [open]);

  async function search(event: FormEvent) { event.preventDefault(); if (query.trim()) setResults(await api.search(query.trim())); }
  const valid = description.trim() !== "" && (formal ? (product !== null && Number(quantity) > 0) : Number(value) > 0);

  async function submit() {
    if (!valid) return;
    setError("");
    try {
      await onSubmit({ product_id: formal ? product!.id : undefined, quantity: formal ? Number(quantity) : undefined,
        description: description.trim(), estimated_value: formal ? undefined : Number(value), notes: notes.trim() || undefined });
    } catch (cause) { setError(errorText(cause)); }
  }

  return (
    <Sheet open={open} title="Casa / consumo interno" persistent module="caja" onClose={onClose}>
      <div className="ui-form">
        <div className="ui-choice" role="group" aria-label="Tipo de registro">
          <button type="button" aria-pressed={formal} className={cx("ui-choice__item", formal && "ui-choice__item--on")} onClick={() => setFormal(true)}>Producto inventariado<small>Descuenta stock real</small></button>
          <button type="button" aria-pressed={!formal} className={cx("ui-choice__item", !formal && "ui-choice__item--on")} onClick={() => setFormal(false)}>Registro manual<small>Valor estimado</small></button>
        </div>

        {formal && (
          <>
            <div className="ui-row">
              <input className="operations-search-input" value={query} onChange={e => setQuery(e.target.value)} placeholder="Buscar producto" />
              <TouchButton type="button" variant="secondary" onClick={event => void search(event as unknown as FormEvent)}>Buscar</TouchButton>
            </div>
            {results.map(row => (
              <button type="button" key={row.id} className="operations-pick-product" onClick={() => { setProduct(row); setDescription(row.name); setResults([]); }}>{row.name} · Stock {Number(row.stock)}</button>
            ))}
            {product && <p className="cash-amount-preview">Seleccionado: <b>{product.name}</b></p>}
            <label className="ui-field"><span>Cantidad</span><input inputMode="decimal" value={quantity} onChange={e => setQuantity(e.target.value)} /></label>
          </>
        )}
        <label className="ui-field"><span>Concepto</span><input value={description} onChange={e => setDescription(e.target.value)} /></label>
        {!formal && <label className="ui-field"><span>Valor estimado</span><input inputMode="numeric" value={value} onChange={e => setValue(e.target.value.replace(/\D/g, ""))} placeholder="$ 0" /></label>}
        <label className="ui-field"><span>Observación</span><textarea rows={2} value={notes} onChange={e => setNotes(e.target.value)} /></label>
        {error && <p className="ui-field-error" role="alert">{error}</p>}
        <div className="ui-form__actions">
          <TouchButton variant="secondary" size="lg" onClick={onClose} disabled={busy}>Cancelar</TouchButton>
          <TouchButton size="lg" module="caja" disabled={!valid || busy} loading={busy} onClick={() => void submit()}>Registrar</TouchButton>
        </div>
      </div>
    </Sheet>
  );
}
