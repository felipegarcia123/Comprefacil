"use client";

import { useEffect, useState } from "react";
import { Plus } from "lucide-react";
import { Sheet, TouchButton } from "@/components/ui";
import { api } from "@/lib/api";
import { errorText } from "@/lib/format";
import type { Supplier } from "@/lib/types";

type Props = {
  open: boolean;
  onClose: () => void;
  onSelect: (supplier: Supplier) => void;
};

/**
 * Fast supplier search/select for starting or re-supplying a purchase, without leaving Compras.
 * "+ Nuevo proveedor" is a minimal quick form (name is the only real requirement); full editing
 * — address, notes, activation — stays in /suppliers, this is not a second CRUD.
 */
export function SupplierPicker({ open, onClose, onSelect }: Props) {
  const [query, setQuery] = useState("");
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (!open) { setQuery(""); setCreating(false); setError(""); }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const timer = setTimeout(() => {
      api.suppliers(query, true).then(setSuppliers).catch(error => setError(errorText(error)));
    }, 200);
    return () => clearTimeout(timer);
  }, [open, query]);

  return (
    <Sheet open={open} title="Elegir proveedor" onClose={onClose} module="compras">
      {error && <p className="ui-field-error" role="alert">{error}</p>}
      {!creating ? (
        <div className="ui-stack">
          <label className="ui-field"><span>Buscar</span>
            <input autoFocus value={query} onChange={event => setQuery(event.target.value)} placeholder="Nombre, NIT o contacto" />
          </label>
          <ul className="ui-menu-list">
            {suppliers.map(supplier => (
              <li key={supplier.id}>
                <button type="button" className="ui-menu-link" onClick={() => onSelect(supplier)}>
                  <span>{supplier.name}<small>{supplier.document ?? supplier.contact_name ?? "Sin datos adicionales"}</small></span>
                </button>
              </li>
            ))}
            {!suppliers.length && <p className="ui-empty-hint">Ningún proveedor activo coincide con la búsqueda.</p>}
          </ul>
          <TouchButton variant="secondary" icon={Plus} block onClick={() => setCreating(true)}>Nuevo proveedor</TouchButton>
        </div>
      ) : (
        <QuickCreateSupplier
          initialName={query}
          onCancel={() => setCreating(false)}
          onCreated={supplier => onSelect(supplier)}
          notify={setError}
        />
      )}
    </Sheet>
  );
}

function QuickCreateSupplier({ initialName, onCancel, onCreated, notify }: { initialName: string; onCancel: () => void; onCreated: (supplier: Supplier) => void; notify: (message: string) => void }) {
  const [name, setName] = useState(initialName);
  const [document, setDocument] = useState("");
  const [phone, setPhone] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (name.trim().length < 2) return;
    setBusy(true); notify("");
    try {
      const supplier = await api.createSupplier({
        name: name.trim(), document: document.trim() || null, phone: phone.trim() || null,
        email: null, address: null, contact_name: null, notes: null,
      });
      onCreated(supplier);
    } catch (error) { notify(errorText(error)); } finally { setBusy(false); }
  }

  return (
    <div className="ui-form">
      <label className="ui-field"><span>Nombre</span><input autoFocus value={name} onChange={event => setName(event.target.value)} minLength={2} /></label>
      <label className="ui-field"><span>NIT / documento (opcional)</span><input value={document} onChange={event => setDocument(event.target.value)} /></label>
      <label className="ui-field"><span>Teléfono (opcional)</span><input value={phone} onChange={event => setPhone(event.target.value)} /></label>
      <div className="ui-form__actions">
        <TouchButton variant="secondary" size="lg" onClick={onCancel} disabled={busy}>Volver</TouchButton>
        <TouchButton size="lg" module="compras" onClick={() => void submit()} disabled={busy || name.trim().length < 2} loading={busy}>Crear y usar</TouchButton>
      </div>
    </div>
  );
}
