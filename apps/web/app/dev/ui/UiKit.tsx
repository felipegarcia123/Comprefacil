"use client";

import { Plus, Printer, Trash2 } from "lucide-react";
import { useState, type ReactNode } from "react";
import {
  ConfirmDialog, ContextBadge, DataTable, Display, InlineMessage, MODULES, MODULE_ORDER, ModuleCard,
  SearchBar, StatusBadge, Td, Th, TouchButton, TouchTabs, Tr, type ModuleId, type TouchTabItem,
} from "@/components/ui";
import { money } from "@/lib/format";
import { useBreakpoint } from "@/lib/useBreakpoint";

const ROWS = [
  { name: "Arroz Diana 500 g", qty: 2, price: 4200 },
  { name: "Aceite Premier 900 ml", qty: 1, price: 9000 },
  { name: "Sal Refisal 500 g", qty: 3, price: 1100 },
];

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="ui-stack" style={{ background: "var(--color-surface)", padding: "var(--sp-4)", borderRadius: "var(--radius-panel)", boxShadow: "var(--shadow-panel)" }}>
      <h2 style={{ margin: 0, fontFamily: "var(--font-display)", fontSize: "var(--text-title)" }}>{title}</h2>
      {children}
    </section>
  );
}

export function UiKit() {
  const breakpoint = useBreakpoint();
  const [tab, setTab] = useState("venta-1");
  const [query, setQuery] = useState("");
  const [confirm, setConfirm] = useState(false);
  const tabs: TouchTabItem[] = [
    { id: "venta-1", label: "Venta 1", sub: money.format(18500), module: "ventas" },
    { id: "venta-2", label: "Venta 2", sub: money.format(4800), module: "ventas" },
    { id: "compra", label: "Compra · Bavaria", sub: "12 ítems", module: "compras" },
  ];

  return (
    <main className="ui-screen ui-stack" style={{ background: "var(--color-paper)", color: "var(--color-ink)", fontFamily: "var(--font-sans)", fontSize: "var(--text-body)", minHeight: "100vh" }}>
      <header className="ui-row" style={{ justifyContent: "space-between" }}>
        <h1 style={{ margin: 0, fontFamily: "var(--font-display)", fontSize: "var(--text-title)" }}>Mostrador · UI kit</h1>
        <StatusBadge tone="info">Breakpoint: {breakpoint}</StatusBadge>
      </header>

      <Section title="Módulos (tokens)">
        <div style={{ display: "grid", gap: "var(--sp-3)", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))" }}>
          {MODULE_ORDER.map(id => (
            <div key={id} data-module={id} style={{ borderRadius: "var(--radius-key)", overflow: "hidden", border: "2px solid var(--mod-deep)" }}>
              <div style={{ background: "var(--mod-strong)", color: "var(--mod-on)", padding: "var(--sp-3)", fontWeight: 700 }}>{MODULES[id].label} · strong</div>
              <div style={{ background: "var(--mod-tint)", color: "var(--mod-deep)", padding: "var(--sp-3)", fontWeight: 700 }}>tint + deep</div>
            </div>
          ))}
        </div>
      </Section>

      <Section title="TouchButton">
        <div className="ui-row">
          <TouchButton module="ventas" size="md" icon={Plus}>Nueva venta</TouchButton>
          <TouchButton module="ventas" size="lg" icon={Printer}>Imprimir</TouchButton>
          <TouchButton module="compras" size="lg">Recibir mercancía</TouchButton>
          <TouchButton module="inventario" variant="secondary" size="lg">Contar stock</TouchButton>
          <TouchButton variant="danger" size="lg" icon={Trash2}>Eliminar</TouchButton>
          <TouchButton module="ventas" variant="ghost" size="lg">Cancelar</TouchButton>
          <TouchButton module="ventas" iconOnly icon={Plus} label="Agregar" size="lg" />
        </div>
        <div className="ui-row">
          <TouchButton module="ventas" size="lg" disabled>Deshabilitado</TouchButton>
          <TouchButton module="ventas" size="lg" loading>Cargando</TouchButton>
        </div>
        <TouchButton module="ventas" size="cta" block>COBRAR {money.format(18500)}</TouchButton>
        <TouchButton module="compras" size="cta" block>RECIBIR</TouchButton>
      </Section>

      <Section title="TouchTabs + ContextBadge">
        <TouchTabs
          ariaLabel="Trabajos abiertos"
          items={tabs}
          value={tab}
          onChange={setTab}
          trailing={<button type="button" className="ui-tab ui-tab--action" aria-label="Nueva venta" style={{ alignItems: "center" }}><span className="ui-tab__body"><Plus size={24} aria-hidden="true" /></span></button>}
        />
        <div className="ui-row">
          {MODULE_ORDER.map(id => <ContextBadge key={id} module={id as ModuleId} />)}
        </div>
      </Section>

      <Section title="ModuleCard">
        <div style={{ display: "grid", gap: "var(--sp-4)", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))" }}>
          {MODULE_ORDER.map((id, index) => <ModuleCard key={id} module={id} count={index === 2 ? 1 : undefined} />)}
        </div>
      </Section>

      <Section title="Display">
        <div style={{ display: "grid", gap: "var(--sp-3)", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))" }}>
          <Display label="Total" value={money.format(18500)} caption="3 productos · 5 unidades" live />
          <Display label="Cambio" value={money.format(1500)} tone="amber" size="figure" />
        </div>
      </Section>

      <Section title="StatusBadge + InlineMessage">
        <div className="ui-row">
          <StatusBadge tone="success">Jornada abierta</StatusBadge>
          <StatusBadge tone="warning">Stock bajo</StatusBadge>
          <StatusBadge tone="danger">Sin stock</StatusBadge>
          <StatusBadge tone="info">Borrador guardado</StatusBadge>
          <StatusBadge>Inactivo</StatusBadge>
        </div>
        <InlineMessage tone="success">Conteo aplicado: 4 movimientos creados.</InlineMessage>
        <InlineMessage tone="warning" title="Jornada cerrada">Abre la caja para confirmar el conteo.</InlineMessage>
        <InlineMessage tone="danger" title="El stock cambió" actions={<TouchButton variant="secondary" module="inventario">Repetir preview</TouchButton>} onDismiss={() => undefined}>
          Genera un nuevo preview antes de confirmar.
        </InlineMessage>
      </Section>

      <Section title="SearchBar (modo escáner)">
        <SearchBar label="Buscar producto" placeholder="Escanea o busca un producto" value={query} onChange={setQuery} scanner module="ventas" />
      </Section>

      <Section title="DataTable">
        <DataTable caption="Productos de la venta" stack module="ventas">
          <thead><tr><Th>Producto</Th><Th numeric>Cantidad</Th><Th numeric>Precio</Th><Th numeric>Total</Th></tr></thead>
          <tbody>
            {ROWS.map(row => (
              <Tr key={row.name} interactive>
                <Td label="Producto">{row.name}</Td>
                <Td numeric label="Cantidad">{row.qty}</Td>
                <Td numeric muted label="Precio">{money.format(row.price)}</Td>
                <Td numeric label="Total"><strong>{money.format(row.qty * row.price)}</strong></Td>
              </Tr>
            ))}
          </tbody>
        </DataTable>
      </Section>

      <Section title="ConfirmDialog">
        <div className="ui-row"><TouchButton variant="danger" size="lg" onClick={() => setConfirm(true)}>Cancelar borrador</TouchButton></div>
        <ConfirmDialog open={confirm} module="compras" tone="danger" title="¿Cancelar este borrador?" confirmLabel="Cancelar borrador" cancelLabel="Volver" onCancel={() => setConfirm(false)} onConfirm={() => setConfirm(false)}>
          La compra se marca como cancelada y el inventario no cambia.
        </ConfirmDialog>
      </Section>
    </main>
  );
}
