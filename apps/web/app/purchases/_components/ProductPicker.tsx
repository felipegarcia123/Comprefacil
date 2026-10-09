"use client";

import { useState, type RefObject } from "react";
import { api } from "@/lib/api";
import { errorText, money } from "@/lib/format";
import type { InventoryProduct, ProductCatalog } from "@/lib/types";
import { SearchBar } from "@/components/ui";
import type { ProductSeed } from "./types";

const isBarcodeLike = (value: string) => /^\d{8,14}$/.test(value);

const fromCatalog = (p: ProductCatalog): ProductSeed => ({
  id: p.id, barcode: p.barcode, name: p.name, sale_price: p.sale_price,
  unit_cost: p.purchase_cost, last_purchase_cost: p.last_purchase_cost, current_stock: p.current_stock,
});
const fromInventory = (p: InventoryProduct): ProductSeed => ({
  id: p.id, barcode: p.barcode, name: p.name, sale_price: p.sale_price,
  unit_cost: p.unit_cost, last_purchase_cost: p.last_purchase_cost, current_stock: p.current_stock,
});

type Props = {
  disabled?: boolean;
  inputRef: RefObject<HTMLInputElement | null>;
  onAdd: (seed: ProductSeed) => void;
  notify: (text: string) => void;
};

/**
 * Scanner-first product lookup for a purchase line: a barcode-like scan resolves straight to
 * GET /products/by-barcode (UPC-A ⇄ EAN-13 tolerant, same as Ventas); anything else searches
 * the catalog by name. Never creates a product — an unknown code is reported, not guessed.
 * Search results are capped server-side (one page, page_size 20): never loads the full 7.500+ catalog for a picker.
 */
export function ProductPicker({ disabled, inputRef, onAdd, notify }: Props) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<InventoryProduct[]>([]);

  function pick(seed: ProductSeed) {
    setQuery(""); setResults([]);
    onAdd(seed);
  }

  async function submit(raw: string) {
    const value = raw.trim();
    if (!value || disabled) return;
    if (isBarcodeLike(value)) {
      try { return pick(fromCatalog(await api.productByBarcode(value))); }
      catch (error) { return notify(`${errorText(error)} (código ${value})`); }
    }
    try {
      const found = (await api.inventory({ q: value, page_size: 20 })).items;
      const exact = found.find(product => product.barcode === value);
      if (exact || found.length === 1) return pick(fromInventory(exact ?? found[0]));
      setResults(found);
      if (!found.length) notify(`No hay productos que coincidan con "${value}".`);
    } catch (error) { notify(errorText(error)); }
  }

  return (
    <div className="purchase-scan">
      <SearchBar
        label="Escanear código o buscar producto"
        placeholder="Escanea un código o busca por nombre"
        value={query}
        onChange={value => { setQuery(value); if (!value) setResults([]); }}
        onSubmit={value => void submit(value)}
        scanner
        module="compras"
        inputRef={inputRef}
      />
      {results.length > 0 && (
        <ul className="sale-results" aria-label="Resultados de búsqueda">
          {results.map(product => (
            <li key={product.id}>
              <button type="button" className="sale-result" onClick={() => pick(fromInventory(product))}>
                <span className="sale-result__name">{product.name}<small>{product.barcode ?? "Sin código"} · Stock {Number(product.current_stock)}</small></span>
                <strong className="ui-num">Costo {money.format(Number(product.unit_cost))}</strong>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
