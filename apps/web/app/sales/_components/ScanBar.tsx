"use client";

import { useState, type RefObject } from "react";
import { api } from "@/lib/api";
import { money } from "@/lib/format";
import type { Product } from "@/lib/types";
import { SearchBar } from "@/components/ui";

/** EAN-8/UPC-A/EAN-13/GTIN-14 style input: a scan, not a typed name. Goes straight to the API (one round trip). */
const isBarcodeLike = (value: string) => /^\d{8,14}$/.test(value);

type Props = {
  disabled?: boolean;
  inputRef: RefObject<HTMLInputElement | null>;
  /** Scanner path: adds a scanned/typed code to the active sale. Resolves true when the line was added. */
  onScan: (barcode: string) => Promise<boolean>;
  /** Manual pick from the name search: always by product_id (works with or without barcode). */
  onPick: (productId: string, label: string) => Promise<boolean>;
  notify: (text: string) => void;
};

/**
 * Scanner-first field. Query and results live here so typing/scanning never re-renders the cart or the payment panel.
 * Enter or a scanner burst on a barcode-like value adds it directly; anything else is a name/partial-code search.
 */
export function ScanBar({ disabled, inputRef, onScan, onPick, notify }: Props) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Product[]>([]);

  async function add(barcode: string) {
    setQuery(""); setResults([]);
    await onScan(barcode);
  }

  async function pick(product: Product) {
    setQuery(""); setResults([]);
    await onPick(product.id, product.name);
  }

  async function submit(raw: string) {
    const value = raw.trim();
    if (!value || disabled) return;
    if (isBarcodeLike(value)) return add(value); // clear at once so the next scan never appends to a stale code
    try {
      const found = await api.search(value);
      if (found.length === 1 || (found[0]?.barcode && found[0].barcode === value)) return pick(found[0]);
      setResults(found);
      if (!found.length) notify(`No hay productos que coincidan con "${value}".`);
    } catch (error) {
      notify(error instanceof Error ? error.message : "No fue posible buscar el producto.");
    }
  }

  return (
    <div className="sale-scan">
      <SearchBar
        label="Escanear código o buscar producto"
        placeholder="Escanea un código o busca por nombre"
        value={query}
        onChange={value => { setQuery(value); if (!value) setResults([]); }}
        onSubmit={value => void submit(value)}
        scanner
        autoFocus
        module="ventas"
        inputRef={inputRef}
      />
      {results.length > 0 && (
        <ul className="sale-results" aria-label="Resultados de búsqueda">
          {results.map(product => (
            <li key={product.id}>
              <button type="button" className="sale-result" onClick={() => void pick(product)}>
                <span className="sale-result__name">{product.name}<small>{product.barcode ?? "Sin código"} · Stock {Number(product.stock)}</small></span>
                <strong className="ui-num">{money.format(Number(product.sale_price))}</strong>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
