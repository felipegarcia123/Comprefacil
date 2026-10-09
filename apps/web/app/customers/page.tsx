"use client";

import { useCallback, useEffect, useState } from "react";
import { Contact, Link2, LoaderCircle, Pencil, RefreshCw, UserPlus } from "lucide-react";
import { api } from "@/lib/api";
import { dateTime, errorText } from "@/lib/format";
import { useBreakpoint } from "@/lib/useBreakpoint";
import type { Customer, CustomerSyncResult, CustomerSyncStatus } from "@/lib/types";
import { DataTable, InlineMessage, SearchBar, Sheet, StatusBadge, Td, Th, Toldo, TouchButton, TouchTabs, Tr } from "@/components/ui";
import { CustomerCreateForm } from "@/components/customers/CustomerCreateForm";
import { formatIdentification } from "@/components/customers/identification";

type Filter = "active" | "inactive";
type Notice = { tone: "success" | "info" | "warning" | "danger"; text: string };

/**
 * Clientes (menú "Más"). Not a CRM: only what electronic invoicing needs — find a customer, see its
 * Alegra link, bring Alegra's contacts in (sync), create one (Alegra first), and edit contact details.
 * Shares Venta's identity: these are the people sales are invoiced to.
 */
export default function CustomersPage() {
  const breakpoint = useBreakpoint();
  const wide = breakpoint === "pos";
  const compact = breakpoint === "movil";
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("active");
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Customer | null>(null);
  const [status, setStatus] = useState<CustomerSyncStatus | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState<CustomerSyncResult | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState(false);

  const load = useCallback(async () => {
    try { setCustomers(await api.customers(query.trim(), filter === "active", 200)); }
    catch (error) { setNotice({ tone: "danger", text: errorText(error) }); }
    finally { setLoading(false); }
  }, [query, filter]);
  useEffect(() => { const timer = window.setTimeout(() => void load(), 250); return () => window.clearTimeout(timer); }, [load]);

  const loadStatus = useCallback(() => api.customerSyncStatus().then(setStatus).catch(() => setStatus(null)), []);
  useEffect(() => { void loadStatus(); }, [loadStatus]);

  async function sync() {
    setSyncing(true); setNotice(null); setSyncResult(null);
    try { setSyncResult(await api.syncCustomers()); await Promise.all([load(), loadStatus()]); }
    catch (error) { setNotice({ tone: "danger", text: errorText(error) }); }
    finally { setSyncing(false); }
  }

  async function toggleActive(customer: Customer) {
    try {
      const saved = await api.setCustomerActive(customer.id, !customer.active);
      setSelected(saved); await load();
      setNotice({ tone: "success", text: saved.active ? `${saved.name} vuelve a aparecer en Venta` : `${saved.name} ya no aparece en Venta (sigue en Alegra)` });
    } catch (error) { setNotice({ tone: "danger", text: errorText(error) }); }
  }

  const detail = selected && (
    <CustomerDetail customer={selected} onEdit={() => setEditing(true)} onToggle={() => void toggleActive(selected)} />
  );

  return (
    <main className="sale customers" data-module="ventas">
      <Toldo module="ventas" />
      <div className="purchase__head">
        <div className="purchase__head-title">
          <h1>Clientes</h1>
          <p className="cash-open-since">
            {status?.last_synced_at ? `Última sincronización ${dateTime.format(new Date(status.last_synced_at))}` : "Sin sincronizar todavía"}
            {status ? ` · ${status.total_customers} ${status.total_customers === 1 ? "cliente" : "clientes"}` : ""}
          </p>
        </div>
        <div className="ui-row">
          <TouchButton variant="secondary" icon={RefreshCw} loading={syncing} disabled={status ? !status.alegra_configured : false} onClick={() => void sync()}>Sincronizar con Alegra</TouchButton>
          <TouchButton module="ventas" icon={UserPlus} onClick={() => setCreating(true)} disabled={status ? !status.alegra_configured : false}>Nuevo cliente</TouchButton>
        </div>
      </div>

      <div className="purchase__notice customers__notices">
        {status && !status.alegra_configured && <InlineMessage tone="warning">Alegra no está configurado: puedes consultar clientes, pero no crearlos ni sincronizarlos.</InlineMessage>}
        {syncResult && <SyncSummary result={syncResult} onDismiss={() => setSyncResult(null)} />}
        {notice && <InlineMessage tone={notice.tone} onDismiss={() => setNotice(null)}>{notice.text}</InlineMessage>}
      </div>

      <div className="sale__body">
        <section className="sale__main" aria-label="Directorio de clientes">
          <SearchBar label="Buscar cliente" placeholder="Documento, nombre, teléfono o correo" value={query} onChange={setQuery} module="ventas" />
          <TouchTabs ariaLabel="Estado" value={filter} onChange={value => setFilter(value as Filter)}
            items={[{ id: "active", label: "Activos", module: "ventas" }, { id: "inactive", label: "Ocultos", module: "ventas" }]} />
          <div className="sale__cart">
            {loading ? (
              <div className="sale-status" role="status"><LoaderCircle className="ui-btn__spin" size={28} aria-hidden="true" />Cargando…</div>
            ) : customers.length ? (
              <DataTable caption="Clientes" className="customers-cart" module="ventas" stack={compact}>
                <thead><tr><Th>Cliente</Th><Th>Documento</Th>{!compact && <Th>Contacto</Th>}<Th>Alegra</Th></tr></thead>
                <tbody>
                  {customers.map(customer => (
                    <Tr key={customer.id} interactive selected={selected?.id === customer.id} onClick={() => setSelected(customer)}>
                      <Td label="Cliente"><strong>{customer.name}</strong></Td>
                      <Td label="Documento" className="ui-num">{formatIdentification(customer.identification_type, customer.identification_number, customer.dv)}</Td>
                      {!compact && <Td label="Contacto" muted>{customer.email ?? customer.phone ?? "—"}</Td>}
                      <Td label="Alegra"><StatusBadge tone={customer.alegra_contact_id ? "success" : "warning"}>{customer.alegra_contact_id ? `Vinculado · ${customer.alegra_contact_id}` : "Sin vínculo"}</StatusBadge></Td>
                    </Tr>
                  ))}
                </tbody>
              </DataTable>
            ) : (
              <div className="sale-empty" data-module="ventas">
                <span className="sale-empty__icon"><Contact size={44} aria-hidden="true" /></span>
                <h2>{query.trim() ? "Sin resultados" : filter === "active" ? "Sin clientes" : "Sin clientes ocultos"}</h2>
                <p>{query.trim() ? "Revisa el documento o crea el cliente." : "Sincroniza con Alegra para traer los clientes que ya existen, o crea uno."}</p>
              </div>
            )}
          </div>
        </section>
        {wide && (
          <section className="sale__panel" aria-label="Detalle del cliente">
            {detail ?? <div className="sale-status customers__placeholder">Selecciona un cliente para ver su información.</div>}
          </section>
        )}
      </div>

      <Sheet open={!wide && Boolean(selected) && !editing} title="Cliente" module="ventas" onClose={() => setSelected(null)}>{!wide && detail}</Sheet>
      <Sheet open={creating} title="Nuevo cliente" persistent module="ventas" onClose={() => setCreating(false)}>
        {creating && (
          <CustomerCreateForm
            initialNumber={query}
            onCancel={() => setCreating(false)}
            onCreated={(customer, outcome) => {
              setCreating(false); setSelected(customer); void load(); void loadStatus();
              setNotice({ tone: "success", text: outcome === "CREATED" ? `${customer.name} se creó en Alegra` : outcome === "LINKED_REMOTE" ? `${customer.name} ya existía en Alegra y quedó vinculado` : `${customer.name} ya estaba registrado` });
            }}
          />
        )}
      </Sheet>
      {selected && <EditContactSheet open={editing} customer={selected} onClose={() => setEditing(false)} onSaved={saved => { setEditing(false); setSelected(saved); void load(); setNotice({ tone: "success", text: "Datos de contacto actualizados en Alegra" }); }} />}
    </main>
  );
}

function SyncSummary({ result, onDismiss }: { result: CustomerSyncResult; onDismiss: () => void }) {
  return (
    <InlineMessage tone={result.errors ? "warning" : "success"} title="Sincronización con Alegra terminada" onDismiss={onDismiss}>
      <span className="customers__sync ui-num">
        <span>Nuevos <b>{result.created}</b></span><span>Actualizados <b>{result.updated}</b></span>
        <span>Sin cambios <b>{result.unchanged}</b></span><span>Errores <b>{result.errors}</b></span>
      </span>
      {result.error_messages.length > 0 && <ul className="einvoice-reasons">{result.error_messages.slice(0, 5).map(message => <li key={message}>{message}</li>)}</ul>}
    </InlineMessage>
  );
}

function CustomerDetail({ customer, onEdit, onToggle }: { customer: Customer; onEdit: () => void; onToggle: () => void }) {
  return (
    <div className="ui-stack customers__detail">
      <div>
        <h2 className="customers__name">{customer.name}</h2>
        <p className="customers__doc ui-num">{formatIdentification(customer.identification_type, customer.identification_number, customer.dv)} · {customer.kind_of_person === "LEGAL_ENTITY" ? "Empresa" : "Persona natural"}</p>
      </div>
      <div className="ui-row">
        <StatusBadge tone={customer.alegra_contact_id ? "success" : "warning"}>{customer.alegra_contact_id ? "Vinculado a Alegra" : "Sin vínculo con Alegra"}</StatusBadge>
        <StatusBadge tone={customer.active ? "success" : "neutral"}>{customer.active ? "Visible en Venta" : "Oculto"}</StatusBadge>
      </div>
      <dl className="purchase__receipt-meta">
        <div><dt>Correo</dt><dd>{customer.email ?? "—"}</dd></div>
        <div><dt>Teléfono</dt><dd>{customer.phone ?? "—"}</dd></div>
        <div><dt>Dirección</dt><dd>{customer.address ?? "—"}</dd></div>
        <div><dt><Link2 size={16} aria-hidden="true" /> Contacto Alegra</dt><dd className="ui-num">{customer.alegra_contact_id ?? "—"}</dd></div>
        <div><dt>Última sincronización</dt><dd>{customer.last_synced_at ? dateTime.format(new Date(customer.last_synced_at)) : "—"}</dd></div>
      </dl>
      <p className="customers__hint">El documento y el nombre definen la identidad fiscal: se corrigen en Alegra y llegan aquí al sincronizar.</p>
      <div className="ui-form__actions">
        <TouchButton variant="secondary" icon={Pencil} onClick={onEdit}>Editar contacto</TouchButton>
        <TouchButton variant={customer.active ? "danger" : "primary"} module={customer.active ? undefined : "ventas"} onClick={onToggle}>{customer.active ? "Ocultar" : "Mostrar"}</TouchButton>
      </div>
    </div>
  );
}

function EditContactSheet({ open, customer, onClose, onSaved }: { open: boolean; customer: Customer; onClose: () => void; onSaved: (customer: Customer) => void }) {
  const [email, setEmail] = useState(customer.email ?? "");
  const [phone, setPhone] = useState(customer.phone ?? "");
  const [address, setAddress] = useState(customer.address ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { if (open) { setEmail(customer.email ?? ""); setPhone(customer.phone ?? ""); setAddress(customer.address ?? ""); setError(""); } }, [open, customer]);

  async function submit() {
    setBusy(true); setError("");
    try { onSaved(await api.updateCustomer(customer.id, { email: email.trim() || null, phone: phone.trim() || null, address: address.trim() || null })); }
    catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  }

  return (
    <Sheet open={open} title={`Editar · ${customer.name}`} persistent module="ventas" onClose={onClose}>
      <form className="ui-form" onSubmit={event => { event.preventDefault(); void submit(); }}>
        <label className="ui-field"><span>Correo</span><input type="email" inputMode="email" value={email} onChange={event => setEmail(event.target.value)} /></label>
        <label className="ui-field"><span>Teléfono</span><input inputMode="tel" value={phone} onChange={event => setPhone(event.target.value)} /></label>
        <label className="ui-field"><span>Dirección</span><input value={address} onChange={event => setAddress(event.target.value)} /></label>
        <p className="customers__hint">Se guarda primero en Alegra; si Alegra lo rechaza, no cambia nada aquí.</p>
        {error && <InlineMessage tone="danger">{error}</InlineMessage>}
        <div className="ui-form__actions">
          <TouchButton variant="secondary" size="lg" onClick={onClose} disabled={busy}>Cancelar</TouchButton>
          <TouchButton type="submit" size="lg" module="ventas" loading={busy}>Guardar</TouchButton>
        </div>
      </form>
    </Sheet>
  );
}
