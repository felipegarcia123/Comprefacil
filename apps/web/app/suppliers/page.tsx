"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Handshake, LoaderCircle, Pencil, Plus } from "lucide-react";
import { api } from "@/lib/api";
import { dateMedium, errorText, money } from "@/lib/format";
import type { Supplier, SupplierDetail } from "@/lib/types";
import { DataTable, MetricCard, SearchBar, StatusBadge, Td, Th, Toldo, TouchButton, Tr } from "@/components/ui";
import { PurchaseStatusBadge } from "@/components/PurchaseStatusBadge";
import { SupplierFormSheet } from "./_components/SupplierFormSheet";

export default function SuppliersPage() {
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [detail, setDetail] = useState<SupplierDetail | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Supplier | null | undefined>(undefined);
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    try { setSuppliers(await api.suppliers(query)); } catch (error) { setMessage(errorText(error)); } finally { setLoading(false); }
  }, [query]);
  useEffect(() => { const timer = setTimeout(() => void load(), 250); return () => clearTimeout(timer); }, [load]);

  async function open(supplier: Supplier) {
    try { setDetail(await api.supplier(supplier.id)); } catch (error) { setMessage(errorText(error)); }
  }
  async function toggleActive() {
    if (!detail) return;
    try { const saved = await api.setSupplierActive(detail.id, !detail.active); await load(); await open(saved); }
    catch (error) { setMessage(errorText(error)); }
  }
  async function onSaved(saved: Supplier) {
    setEditing(undefined); setMessage(`Proveedor ${saved.name} guardado`); await load(); await open(saved);
  }

  return (
    <main className="sale" data-module="proveedores">
      <Toldo module="proveedores" />
      <div className="purchase__head">
        <div className="purchase__head-title"><h1>Proveedores</h1></div>
        <TouchButton module="proveedores" icon={Plus} onClick={() => setEditing(null)}>Nuevo proveedor</TouchButton>
      </div>
      {message && <div className="purchase__notice"><StatusBadge tone="info">{message}</StatusBadge></div>}

      <div className="sale__body">
        <section className="sale__main" aria-label="Directorio de proveedores">
          <SearchBar label="Buscar proveedor" placeholder="Nombre, NIT o contacto" value={query} onChange={setQuery} module="proveedores" />
          <div className="sale__cart">
            {loading ? (
              <div className="sale-status" role="status"><LoaderCircle className="ui-btn__spin" size={28} aria-hidden="true" />Cargando…</div>
            ) : suppliers.length ? (
              <DataTable caption="Proveedores" className="sale-cart" module="proveedores">
                <thead><tr><Th>Proveedor</Th><Th>NIT / documento</Th><Th>Contacto</Th><Th>Estado</Th></tr></thead>
                <tbody>
                  {suppliers.map(supplier => (
                    <Tr key={supplier.id} interactive selected={detail?.id === supplier.id} onClick={() => void open(supplier)}>
                      <Td label="Proveedor"><strong>{supplier.name}</strong></Td>
                      <Td label="Documento" muted>{supplier.document ?? "—"}</Td>
                      <Td label="Contacto" muted>{supplier.contact_name ?? supplier.phone ?? "—"}</Td>
                      <Td label="Estado"><StatusBadge tone={supplier.active ? "success" : "neutral"}>{supplier.active ? "Activo" : "Inactivo"}</StatusBadge></Td>
                    </Tr>
                  ))}
                </tbody>
              </DataTable>
            ) : (
              <div className="sale-empty" data-module="proveedores"><span className="sale-empty__icon"><Handshake size={44} aria-hidden="true" /></span><h2>Sin proveedores</h2><p>Crea el primero para empezar a registrar compras.</p></div>
            )}
          </div>
        </section>

        <section className="sale__panel" aria-label="Detalle del proveedor">
          {detail ? <SupplierDetailPanel detail={detail} onEdit={() => setEditing(detail)} onToggle={() => void toggleActive()} /> : (
            <div className="sale-status" style={{ minHeight: "auto", padding: "var(--sp-6)" }}>Selecciona un proveedor para ver su información.</div>
          )}
        </section>
      </div>

      <SupplierFormSheet
        target={editing}
        onClose={() => setEditing(undefined)}
        onSaved={onSaved}
        save={body => (editing ? api.updateSupplier(editing.id, body) : api.createSupplier(body))}
      />
    </main>
  );
}

function SupplierDetailPanel({ detail, onEdit, onToggle }: { detail: SupplierDetail; onEdit: () => void; onToggle: () => void }) {
  return (
    <div className="ui-stack" style={{ padding: "var(--sp-3)" }}>
      <div className="ui-row" style={{ justifyContent: "space-between" }}>
        <div><h2 style={{ margin: 0, fontFamily: "var(--font-display)", fontSize: "var(--text-title)", color: "var(--color-proveedores-deep)" }}>{detail.name}</h2><p style={{ margin: "2px 0 0", color: "var(--color-ink-muted)" }}>{detail.document ?? "Sin documento"}</p></div>
        <StatusBadge tone={detail.active ? "success" : "neutral"}>{detail.active ? "Activo" : "Inactivo"}</StatusBadge>
      </div>
      <dl className="purchase__receipt-meta">
        <Pair label="Contacto" value={detail.contact_name} />
        <Pair label="Teléfono" value={detail.phone} />
        <Pair label="Correo" value={detail.email} />
        <Pair label="Dirección" value={detail.address} />
      </dl>
      <div className="ui-row">
        <MetricCard module="proveedores" label="Total recibido" value={money.format(Number(detail.total_purchased))} />
        <MetricCard module="proveedores" label="Última compra" value={detail.last_purchase_at ? dateMedium.format(new Date(detail.last_purchase_at)) : "—"} />
      </div>
      <div>
        <h3 style={{ margin: "0 0 var(--sp-2)", fontSize: "var(--text-micro)", color: "var(--color-ink-muted)" }}>Compras</h3>
        <ul className="ui-menu-list">
          {detail.purchases.map(purchase => (
            <li key={purchase.id}>
              <Link href={`/purchases?id=${purchase.id}`} className="ui-menu-link purchase-history-row">
                <span>{purchase.invoice_number ?? purchase.id.slice(0, 8)}<small>{dateMedium.format(new Date(purchase.purchased_at))}</small></span>
                <span className="purchase-history-row__end"><PurchaseStatusBadge status={purchase.status} /><b className="ui-num">{money.format(Number(purchase.total))}</b></span>
              </Link>
            </li>
          ))}
          {!detail.purchases.length && <p className="ui-empty-hint">Sin compras registradas.</p>}
        </ul>
      </div>
      <div className="ui-form__actions" style={{ justifyContent: "flex-start" }}>
        <TouchButton variant="secondary" icon={Pencil} onClick={onEdit}>Editar</TouchButton>
        <TouchButton variant={detail.active ? "danger" : "primary"} module={detail.active ? undefined : "proveedores"} onClick={onToggle}>{detail.active ? "Desactivar" : "Activar"}</TouchButton>
      </div>
    </div>
  );
}

function Pair({ label, value }: { label: string; value: string | null }) {
  return <div><dt>{label}</dt><dd>{value ?? "—"}</dd></div>;
}
