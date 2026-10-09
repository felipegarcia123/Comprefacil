"use client";

import { useEffect, useRef, useState } from "react";
import { Check, LoaderCircle, UserPlus } from "lucide-react";
import { InlineMessage, SearchBar, Sheet, TouchButton } from "@/components/ui";
import { api } from "@/lib/api";
import { cx } from "@/lib/cx";
import { errorText } from "@/lib/format";
import type { Customer } from "@/lib/types";
import { CustomerCreateForm } from "./CustomerCreateForm";
import { formatIdentification } from "./identification";

type Props = {
  open: boolean;
  selectedId?: string | null;
  /** `note` explains a created/linked customer (shown by the screen after the sheet closes). */
  onSelect: (customer: Customer, note?: string) => void;
  onClose: () => void;
  /** Leaves the sale billed to Consumidor final (shown next to creation errors). */
  onUseFinalConsumer: () => void;
};

const OUTCOME_NOTE = { EXISTING_LOCAL: "ya estaba registrado y quedó seleccionado", LINKED_REMOTE: "ya existía en Alegra: se vinculó y quedó seleccionado", CREATED: "se creó en Alegra y quedó seleccionado" } as const;

/**
 * Pick the customer an invoice goes to. Search is LOCAL (GET /customers, debounced 250 ms) — never
 * one Alegra call per key. "Crear cliente" switches the same sheet to the creation form; a created
 * (or linked) customer is selected right away.
 */
export function CustomerPickerSheet({ open, selectedId, onSelect, onClose, onUseFinalConsumer }: Props) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Customer[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { if (open) { setCreating(false); setError(""); } }, [open]);
  // showModal() focuses the dialog's first control (the close key); the cashier came here to type.
  useEffect(() => {
    if (!open || creating) return;
    const timer = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [open, creating]);

  useEffect(() => {
    if (!open || creating) return;
    let alive = true;
    setLoading(true);
    const timer = window.setTimeout(() => {
      api.customers(query.trim(), true, 12)
        .then(list => { if (alive) { setResults(list); setError(""); } })
        .catch(cause => { if (alive) setError(errorText(cause)); })
        .finally(() => { if (alive) setLoading(false); });
    }, 250);
    return () => { alive = false; window.clearTimeout(timer); };
  }, [open, creating, query]);

  return (
    <Sheet open={open} title={creating ? "Crear cliente" : "Cliente de la factura"} persistent={creating} module="ventas" onClose={onClose}>
      {creating ? (
        <CustomerCreateForm
          initialNumber={query}
          onCancel={() => setCreating(false)}
          onCreated={(customer, outcome) => onSelect(customer, `${customer.name} ${OUTCOME_NOTE[outcome]}`)}
          errorAction={{ label: "Usar Consumidor final", onClick: onUseFinalConsumer }}
        />
      ) : (
        <div className="customer-picker">
          <SearchBar label="Buscar cliente" placeholder="Documento, nombre, teléfono o correo" value={query} onChange={setQuery} module="ventas" inputRef={inputRef} autoFocus />
          {error && <InlineMessage tone="danger">{error}</InlineMessage>}
          <ul className="customer-picker__list" aria-label="Clientes encontrados" aria-busy={loading}>
            {results.map(customer => {
              const selected = customer.id === selectedId;
              return (
                <li key={customer.id}>
                  <button type="button" className={cx("customer-row", selected && "customer-row--selected")} aria-pressed={selected} onClick={() => onSelect(customer)}>
                    <span className="customer-row__main">
                      <strong>{customer.name}</strong>
                      <span className="ui-num">{formatIdentification(customer.identification_type, customer.identification_number, customer.dv)}</span>
                      {(customer.email || customer.phone) && <small>{customer.email ?? customer.phone}</small>}
                    </span>
                    {selected && <Check size={22} aria-hidden="true" />}
                  </button>
                </li>
              );
            })}
          </ul>
          {loading && !results.length && <p className="customer-picker__hint" role="status"><LoaderCircle className="ui-btn__spin" size={20} aria-hidden="true" />Buscando…</p>}
          {!loading && !results.length && !error && (
            <p className="customer-picker__hint">{query.trim() ? "No hay clientes con ese dato." : "Todavía no hay clientes. Sincroniza desde Clientes o crea uno."}</p>
          )}
          <div className="customer-picker__actions">
            <TouchButton variant="secondary" size="lg" icon={UserPlus} module="ventas" onClick={() => setCreating(true)}>Crear cliente</TouchButton>
          </div>
        </div>
      )}
    </Sheet>
  );
}
