"use client";

import { useEffect, useRef, useState, type RefObject } from "react";
import { api } from "@/lib/api";
import { money } from "@/lib/format";
import type { Product } from "@/lib/types";
import { SearchBar } from "@/components/ui";

const isBarcodeLike = (value: string) => /^\d{8,14}$/.test(value);
type Props = { disabled?: boolean; inputRef: RefObject<HTMLInputElement | null>; onScan: (barcode: string) => Promise<boolean>; onPick: (product: Product) => Promise<boolean>; onNewProduct?: () => void; notify: (text: string) => void };
function relevance(product: Product, query: string) { const q = query.toLocaleLowerCase(); const name = product.name.toLocaleLowerCase(); const barcode = (product.barcode ?? "").toLocaleLowerCase(); if (barcode === q) return 0; if (barcode.startsWith(q)) return 1; if (name.startsWith(q)) return 2; if (barcode.includes(q)) return 3; return 4; }

export function ScanBar({ disabled, inputRef, onScan, onPick, onNewProduct, notify }: Props) {
  const [query, setQuery] = useState(""); const [results, setResults] = useState<Product[]>([]); const [highlighted, setHighlighted] = useState(0); const requestId = useRef(0);
  useEffect(() => { const value = query.trim(); const id = ++requestId.current; if (!value || isBarcodeLike(value) || disabled) { setResults([]); setHighlighted(0); return; } const timer = window.setTimeout(() => { void api.search(value).then(found => { if (id !== requestId.current) return; setResults([...found].sort((a, b) => relevance(a, value) - relevance(b, value) || a.name.localeCompare(b.name))); setHighlighted(0); }).catch(error => { if (id === requestId.current) notify(error instanceof Error ? error.message : "No fue posible buscar el producto."); }); }, 200); return () => window.clearTimeout(timer); }, [query, disabled, notify]);
  function clear() { setQuery(""); setResults([]); setHighlighted(0); }
  async function add(barcode: string) { clear(); await onScan(barcode); }
  async function pick(product: Product) { clear(); await onPick(product); }
  async function submit(raw: string) { const value = raw.trim(); if (!value || disabled) return; if (isBarcodeLike(value)) return add(value); if (results[highlighted]) return pick(results[highlighted]); try { const found = await api.search(value); const sorted = [...found].sort((a, b) => relevance(a, value) - relevance(b, value) || a.name.localeCompare(b.name)); if (sorted.length === 1 || sorted[0]?.barcode === value) return pick(sorted[0]); setResults(sorted); setHighlighted(0); if (!sorted.length) notify(`No hay productos que coincidan con "${value}".`); } catch (error) { notify(error instanceof Error ? error.message : "No fue posible buscar el producto."); } }
  function keyDown(event: React.KeyboardEvent<HTMLInputElement>) { if (event.key === "ArrowDown" && results.length) { event.preventDefault(); setHighlighted(index => (index + 1) % results.length); } else if (event.key === "ArrowUp" && results.length) { event.preventDefault(); setHighlighted(index => (index - 1 + results.length) % results.length); } else if (event.key === "Escape" && results.length) { event.preventDefault(); clear(); } else if (event.key === "Enter" && results[highlighted]) { event.preventDefault(); void pick(results[highlighted]); } }
  return <div className="sale-scan"><SearchBar label="Escanear código o buscar producto" placeholder="Escanea un código o busca por nombre" value={query} onChange={value => setQuery(value)} onSubmit={value => void submit(value)} onKeyDown={keyDown} scanner autoFocus module="ventas" inputRef={inputRef} />{onNewProduct && <button type="button" className="ui-btn ui-btn--secondary sale-new-product" onClick={onNewProduct} disabled={disabled}>Nuevo producto</button>}{results.length > 0 && <ul className="sale-results" role="listbox" aria-label="Resultados de búsqueda">{results.map((product, index) => <li key={product.id} role="option" aria-selected={index === highlighted}><button type="button" className={`sale-result${index === highlighted ? " is-highlighted" : ""}`} onMouseEnter={() => setHighlighted(index)} onClick={() => void pick(product)}><span className="sale-result__name">{product.name}<small>{product.barcode ?? "Sin código"} · Stock {Number(product.stock)}</small></span><strong className="ui-num">{money.format(Number(product.sale_price))}</strong></button></li>)}</ul>}</div>;
}
