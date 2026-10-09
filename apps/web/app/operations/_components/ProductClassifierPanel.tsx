"use client";

import { useState, type FormEvent } from "react";
import { api } from "@/lib/api";
import { errorText } from "@/lib/format";
import { InlineMessage, TouchButton } from "@/components/ui";
import type { OperationalCategory, Product } from "@/lib/types";

type Props = { categories: OperationalCategory[]; done: () => Promise<void> };

/** Asignar un producto a una de las familias operativas fijas — no crea taxonomía nueva (section 18). */
export function ProductClassifierPanel({ categories, done }: Props) {
  const [query, setQuery] = useState(""); const [results, setResults] = useState<Product[]>([]); const [product, setProduct] = useState<Product | null>(null);
  const [category, setCategory] = useState(""); const [error, setError] = useState("");

  async function search(event: FormEvent) { event.preventDefault(); try { setResults(await api.search(query)); } catch (cause) { setError(errorText(cause)); } }
  async function save() {
    if (!product || !category) return;
    try { await api.setProductCategory(product.id, category); setProduct(null); setQuery(""); setResults([]); await done(); }
    catch (cause) { setError(errorText(cause)); }
  }

  return (
    <section className="operations-panel" aria-label="Clasificar producto">
      <header className="operations-panel__head"><h2>Clasificar producto</h2></header>
      {error && <InlineMessage tone="danger" onDismiss={() => setError("")}>{error}</InlineMessage>}
      <form className="ui-row" onSubmit={search}>
        <input className="operations-search-input" value={query} onChange={event => setQuery(event.target.value)} placeholder="Nombre o código" />
        <TouchButton variant="secondary" type="submit">Buscar</TouchButton>
      </form>
      {results.map(item => <button type="button" className="operations-pick-product" key={item.id} onClick={() => { setProduct(item); setResults([]); }}>{item.name}</button>)}
      {product && (
        <div className="operations-classify-row">
          <strong>{product.name}</strong>
          <select className="inventory-select" value={category} onChange={event => setCategory(event.target.value)}>
            <option value="">Categoría…</option>
            {categories.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}
          </select>
          <TouchButton module="caja" disabled={!category} onClick={() => void save()}>Asignar</TouchButton>
        </div>
      )}
    </section>
  );
}
