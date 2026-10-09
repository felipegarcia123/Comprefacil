"use client";

import { useEffect, useState } from "react";
import { Sheet, TouchButton } from "@/components/ui";
import { errorText } from "@/lib/format";
import type { Supplier } from "@/lib/types";

type Fields = { name: string; document: string; phone: string; email: string; address: string; contact_name: string; notes: string };
const blank: Fields = { name: "", document: "", phone: "", email: "", address: "", contact_name: "", notes: "" };

type Props = {
  /** undefined = closed; null = "new supplier"; a Supplier = editing it. */
  target: Supplier | null | undefined;
  onClose: () => void;
  onSaved: (supplier: Supplier) => void;
  save: (body: Omit<Supplier, "id" | "active" | "created_at">) => Promise<Supplier>;
};

/** Full supplier form (create or edit) — the only place with the complete field set; Compras' in-flow picker only offers a minimal quick-create. */
export function SupplierFormSheet({ target, onClose, onSaved, save }: Props) {
  const [form, setForm] = useState<Fields>(blank);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setError("");
    setForm(target ? { name: target.name, document: target.document ?? "", phone: target.phone ?? "", email: target.email ?? "", address: target.address ?? "", contact_name: target.contact_name ?? "", notes: target.notes ?? "" } : blank);
  }, [target]);

  async function submit() {
    if (form.name.trim().length < 2) return;
    setBusy(true); setError("");
    try {
      const body = Object.fromEntries(Object.entries(form).map(([key, value]) => [key, value.trim() || null])) as unknown as Omit<Supplier, "id" | "active" | "created_at">;
      onSaved(await save(body));
    } catch (cause) { setError(errorText(cause)); } finally { setBusy(false); }
  }

  return (
    <Sheet open={target !== undefined} title={target ? "Editar proveedor" : "Nuevo proveedor"} persistent onClose={onClose} module="proveedores">
      <div className="ui-form">
        <label className="ui-field"><span>Nombre</span><input autoFocus value={form.name} minLength={2} onChange={event => setForm({ ...form, name: event.target.value })} /></label>
        <label className="ui-field"><span>NIT / documento</span><input value={form.document} onChange={event => setForm({ ...form, document: event.target.value })} /></label>
        <label className="ui-field"><span>Contacto</span><input value={form.contact_name} onChange={event => setForm({ ...form, contact_name: event.target.value })} /></label>
        <label className="ui-field"><span>Teléfono</span><input value={form.phone} onChange={event => setForm({ ...form, phone: event.target.value })} /></label>
        <label className="ui-field"><span>Correo</span><input type="email" value={form.email} onChange={event => setForm({ ...form, email: event.target.value })} /></label>
        <label className="ui-field"><span>Dirección</span><input value={form.address} onChange={event => setForm({ ...form, address: event.target.value })} /></label>
        <label className="ui-field"><span>Notas</span><textarea rows={2} value={form.notes} onChange={event => setForm({ ...form, notes: event.target.value })} /></label>
        {error && <p className="ui-field-error" role="alert">{error}</p>}
        <div className="ui-form__actions">
          <TouchButton variant="secondary" size="lg" onClick={onClose} disabled={busy}>Cancelar</TouchButton>
          <TouchButton size="lg" module="proveedores" disabled={form.name.trim().length < 2 || busy} loading={busy} onClick={() => void submit()}>Guardar</TouchButton>
        </div>
      </div>
    </Sheet>
  );
}
