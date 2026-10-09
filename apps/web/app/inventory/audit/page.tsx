"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, CloudDownload, Flag, History, PlayCircle, RotateCcw, ScanLine, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { dateTimeShort, errorText, formatNumber, formatQuantity, signed } from "@/lib/format";
import { useBreakpoint } from "@/lib/useBreakpoint";
import { ConfirmDialog, DataTable, InlineMessage, MetricCard, QtyStepper, SearchBar, StatusBadge, Td, Th, Toldo, TouchButton, Tr } from "@/components/ui";
import type { AuditItem, AuditListItem, AuditReport, AuditSection, InventoryProduct, PhysicalAudit } from "@/lib/types";

/*
 * Interventoría física: walk the store, scan or search each product -> found (1 by default)
 * -> optional real quantity -> finish -> report. Local only: nothing here writes inventory,
 * prices or Alegra. Counted quantities reach the ledger only by downloading them as a
 * count template and confirming it in Inventario > Conteo físico.
 */
const isBarcodeLike = (value: string) => /^\d{8,14}$/.test(value);
const SECTIONS: [AuditSection, string][] = [
  ["present", "Presentes"], ["not_found", "Revisar / no encontrado"], ["counted", "Con stock contado"],
  ["without_alegra", "Sin Alegra"], ["to_create", "Productos por crear"],
];
type Notice = { tone: "success" | "warning" | "danger" | "info"; text: string };

export default function PhysicalAuditPage() {
  const compact = useBreakpoint() === "movil";
  const [audit, setAudit] = useState<PhysicalAudit | null>(null);
  const [history, setHistory] = useState<AuditListItem[]>([]);
  const [report, setReport] = useState<AuditReport | null>(null);
  const [section, setSection] = useState<AuditSection>("not_found");
  const [query, setQuery] = useState(""), [results, setResults] = useState<InventoryProduct[]>([]);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [finishing, setFinishing] = useState(false);
  const [lastId, setLastId] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const queue = useRef<Promise<unknown>>(Promise.resolve());   // scans are serialized: a fast scanner never races itself
  const started = useRef(false);

  const refocus = useCallback(() => window.setTimeout(() => inputRef.current?.focus(), 0), []);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    (async () => {
      try {
        const [current, list] = await Promise.all([api.currentAudit(), api.audits()]);
        setAudit(current); setHistory(list);
      } catch (error) { setNotice({ tone: "danger", text: errorText(error) }); }
      finally { setLoading(false); }
    })();
  }, []);

  function run<T>(task: () => Promise<T>): Promise<T | undefined> {
    const next = queue.current.then(task).catch(error => { setNotice({ tone: "danger", text: errorText(error) }); return undefined; });
    queue.current = next;
    return next;
  }

  async function start() {
    setBusy(true); setNotice(null); setReport(null);
    try { setAudit(await api.startAudit()); refocus(); }
    catch (error) { setNotice({ tone: "danger", text: errorText(error) }); }
    finally { setBusy(false); }
  }

  async function mark(ref: { barcode: string } | { product_id: string }) {
    if (!audit) return;
    const updated = await run(() => api.auditScan(audit.id, ref));
    if (!updated) return refocus();
    setAudit(updated); setLastId(updated.item_id);
    if (updated.unknown_code) {
      const { barcode, scan_count } = updated.unknown_code;
      setNotice({ tone: "warning", text: `El código ${barcode} no está en el catálogo: quedó en "Productos por crear"${scan_count > 1 ? ` (${scan_count} escaneos)` : ""}. No se crea ningún producto.` });
      return refocus();
    }
    const item = updated.items.find(i => i.id === updated.item_id);
    setNotice(item ? {
      tone: item.active ? "success" : "warning",
      text: `${item.name}: ${item.counted ? `${formatQuantity(item.quantity)} contadas` : "encontrado"}${item.active ? "" : " (producto inactivo)"}`,
    } : null);
    refocus();
  }

  async function submit(raw: string) {
    const value = raw.trim();
    if (!value || !audit) return;
    setQuery(""); setResults([]);
    if (isBarcodeLike(value)) return mark({ barcode: value });
    try {
      const found = (await api.inventory({ q: value, page_size: 20 })).items;
      const exact = found.find(product => product.barcode === value);
      if (exact || found.length === 1) return mark({ product_id: (exact ?? found[0]).id });
      if (!found.length) return setNotice({ tone: "warning", text: `No hay productos que coincidan con "${value}".` });
      setQuery(value); setResults(found);
    } catch (error) { setNotice({ tone: "danger", text: errorText(error) }); }
  }

  async function changeQuantity(item: AuditItem, quantity: number) {
    if (!audit) return;
    const updated = await run(() => quantity <= 0 ? api.auditRemove(audit.id, item.id) : api.auditQuantity(audit.id, item.id, Math.round(quantity)));
    if (updated) setAudit(updated);
    if (updated && quantity <= 0) setNotice({ tone: "info", text: `${item.name} vuelve a "no encontrado".` });
  }

  async function finish() {
    if (!audit) return;
    setBusy(true);
    try {
      await api.finishAudit(audit.id);
      await openReport(audit.id);
      setAudit(null); setFinishing(false);
      setHistory(await api.audits());
    } catch (error) { setNotice({ tone: "danger", text: errorText(error) }); setFinishing(false); }
    finally { setBusy(false); }
  }

  async function openReport(id: string) {
    setBusy(true); setNotice(null);
    try { const data = await api.auditReport(id); setReport(data); setSection(data.summary.not_found ? "not_found" : "present"); }
    catch (error) { setNotice({ tone: "danger", text: errorText(error) }); }
    finally { setBusy(false); }
  }

  async function reopen(id: string) {
    setBusy(true); setNotice(null);
    try { setAudit(await api.reopenAudit(id)); setReport(null); refocus(); }
    catch (error) { setNotice({ tone: "danger", text: errorText(error) }); }
    finally { setBusy(false); }
  }

  const progress = audit && audit.active_products ? formatNumber(Math.round((audit.found * 1000) / audit.active_products) / 10) : "0";

  return (
    <main className="sale purchase audit" data-module="inventario">
      <Toldo module="inventario" />
      <div className="purchase__head">
        <div className="purchase__head-title">
          <Link href="/inventory" className="count-back-link"><ArrowLeft size={20} aria-hidden="true" />Inventario</Link>
          <h1>Interventoría física</h1>
        </div>
        {audit && <TouchButton module="inventario" icon={Flag} disabled={busy} onClick={() => setFinishing(true)}>Terminar recorrido</TouchButton>}
      </div>

      {notice && <div className="purchase__notice"><InlineMessage tone={notice.tone} onDismiss={() => setNotice(null)}>{notice.text}</InlineMessage></div>}

      <div className="sale__main count-body">
        {loading && <p className="ui-empty-hint">Cargando…</p>}

        {!loading && !audit && !report && (
          <section className="sale-empty count-upload" data-module="inventario">
            <span className="sale-empty__icon"><ScanLine size={40} aria-hidden="true" /></span>
            <h2>Recorre la tienda y marca lo que encuentras</h2>
            <p>Escanea o busca cada producto: queda como encontrado con cantidad 1. Si quieres, pon la cantidad real. Al terminar verás qué está presente, qué no se encontró, qué falta vincular a Alegra y qué podría desactivarse. No modifica el inventario.</p>
            <TouchButton size="lg" module="inventario" icon={PlayCircle} loading={busy} onClick={() => void start()}>Iniciar recorrido</TouchButton>
            {history.length > 0 && (
              <div className="audit-history">
                <h3><History size={20} aria-hidden="true" />Recorridos anteriores</h3>
                <ul>
                  {history.map(h => (
                    <li key={h.id}>
                      <button type="button" className="sale-result" onClick={() => void openReport(h.id)}>
                        <span className="sale-result__name">{dateTimeShort.format(new Date(h.started_at))}<small>{h.status === "OPEN" ? "En curso" : `Terminado ${h.finished_at ? dateTimeShort.format(new Date(h.finished_at)) : ""}`}</small></span>
                        <strong className="ui-num">{formatQuantity(h.found)} encontrados</strong>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        )}

        {audit && (
          <div className="ui-stack audit-walk">
            <div className="purchase-scan">
              <SearchBar label="Escanear código o buscar producto" placeholder="Escanea un código o busca por nombre" value={query}
                onChange={value => { setQuery(value); if (!value) setResults([]); }} onSubmit={value => void submit(value)}
                scanner module="inventario" inputRef={inputRef} autoFocus />
              {results.length > 0 && (
                <ul className="sale-results" aria-label="Resultados de búsqueda">
                  {results.map(product => (
                    <li key={product.id}>
                      <button type="button" className="sale-result" onClick={() => { setQuery(""); setResults([]); void mark({ product_id: product.id }); }}>
                        <span className="sale-result__name">{product.name}<small>{product.barcode ?? "Sin código"} · Stock {formatQuantity(product.current_stock)}</small></span>
                        <strong>Marcar</strong>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <p className="audit-progress">
              <b className="ui-num">{formatQuantity(audit.found)}</b> de <span className="ui-num">{formatQuantity(audit.active_products)}</span> productos activos encontrados ({progress} %) · <b className="ui-num">{formatQuantity(audit.counted)}</b> con cantidad real
              {audit.unknown > 0 && <> · <b className="ui-num">{formatQuantity(audit.unknown)}</b> {audit.unknown === 1 ? "código" : "códigos"} por crear</>}
            </p>

            <div className="sale__cart">
              {audit.items.length ? (
                <DataTable caption="Productos encontrados" className="count-cart" module="inventario" density="dense" stack={compact}>
                  <thead><tr><Th>Producto</Th><Th numeric>Stock sistema</Th><Th>Cantidad</Th><Th>Alegra</Th><Th><span className="ui-sr-only">Quitar</span></Th></tr></thead>
                  <tbody>
                    {audit.items.map(item => (
                      <Tr key={item.id} className={item.id === lastId ? "audit-row--last" : undefined}>
                        <Td label="Producto">{item.name}<small className="ui-td--muted">{item.barcode ?? "Sin código"}{item.active ? "" : " · Inactivo"}</small></Td>
                        <Td numeric label="Stock sistema" className="ui-num">{formatQuantity(item.system_stock)}</Td>
                        <Td label="Cantidad">
                          <div className="audit-qty">
                            <QtyStepper value={Number(item.quantity)} label={item.name} disabled={busy} onChange={value => void changeQuantity(item, value)} />
                            {item.counted
                              ? item.difference !== null && Number(item.difference) !== 0 && <small className={Number(item.difference) > 0 ? "pos ui-num" : "neg ui-num"}>{signed(Number(item.difference))}</small>
                              : <small className="ui-td--muted">Encontrado</small>}
                          </div>
                        </Td>
                        <Td label="Alegra">{item.alegra_item_id ? <StatusBadge tone="success">Vinculado</StatusBadge> : <StatusBadge tone="warning">Sin Alegra</StatusBadge>}</Td>
                        <Td label="Quitar"><TouchButton variant="ghost" iconOnly icon={Trash2} label={`Quitar ${item.name}`} disabled={busy} onClick={() => void changeQuantity(item, 0)} /></Td>
                      </Tr>
                    ))}
                  </tbody>
                </DataTable>
              ) : <p className="ui-empty-hint">Todavía no has marcado productos. Escanea el primero.</p>}
            </div>
          </div>
        )}

        {!audit && report && <ReportView report={report} section={section} onSection={setSection} busy={busy}
          onReopen={() => void reopen(report.audit.id)} onNew={() => void start()} onBack={() => setReport(null)} />}
      </div>

      <ConfirmDialog open={finishing} module="inventario" title="Terminar recorrido" confirmLabel="Terminar recorrido" cancelLabel="Seguir recorriendo"
        busy={busy} onCancel={() => setFinishing(false)} onConfirm={() => void finish()}>
        {audit && `Encontraste ${formatQuantity(audit.found)} de ${formatQuantity(audit.active_products)} productos activos. Los demás quedarán como "no encontrados" en el reporte. No se modifica el inventario; puedes reabrir el recorrido después.`}
      </ConfirmDialog>
    </main>
  );
}

function ReportView({ report, section, onSection, busy, onReopen, onNew, onBack }: {
  report: AuditReport; section: AuditSection; onSection: (s: AuditSection) => void; busy: boolean;
  onReopen: () => void; onNew: () => void; onBack: () => void;
}) {
  const compact = useBreakpoint() === "movil";
  const s = report.summary;
  const rows = section === "to_create" ? [] : report.sections[section];
  const total = s[section];
  const showCount = section === "present" || section === "counted" || section === "without_alegra";
  const hint = useMemo(() => ({
    present: "Productos que viste en la tienda.",
    not_found: "Productos activos del catálogo que no marcaste en el recorrido. Revísalos antes de decidir: en esta primera interventoría el stock del sistema no es confiable, así que no se recomienda desactivar nada. Desactivar es manual, desde Inventario.",
    counted: "Productos con cantidad real. Descárgalos como plantilla de conteo y confírmalos en Conteo físico para ajustar el inventario.",
    without_alegra: "Están en la tienda pero todavía no se pueden facturar: son la prioridad para vincular o crear en Alegra.",
    to_create: "Códigos escaneados en la tienda que no existen en el catálogo del POS. No se creó ningún producto.",
  })[section], [section]);

  return (
    <div className="ui-stack">
      <div className="ui-row" style={{ justifyContent: "space-between" }}>
        <div>
          <b>Recorrido del {dateTimeShort.format(new Date(report.audit.started_at))}</b>
          <p className="purchase-detail-code">{report.audit.status === "OPEN" ? "En curso" : `Terminado ${report.audit.finished_at ? dateTimeShort.format(new Date(report.audit.finished_at)) : ""}`} · {formatQuantity(s.active_products)} productos activos</p>
        </div>
        <div className="ui-row">
          <TouchButton variant="secondary" onClick={onBack}>Volver</TouchButton>
          <TouchButton variant="secondary" icon={RotateCcw} disabled={busy} onClick={onReopen}>Reabrir recorrido</TouchButton>
          <TouchButton module="inventario" icon={PlayCircle} disabled={busy} onClick={onNew}>Nuevo recorrido</TouchButton>
        </div>
      </div>

      <div className="count-metrics audit-metrics">
        <MetricCard module="inventario" className="count-metric count-metric--success" label="Presentes" value={formatQuantity(s.present)} />
        <MetricCard module="inventario" className="count-metric count-metric--neutral" label="Revisar / no encontrado" value={formatQuantity(s.not_found)} />
        <MetricCard module="inventario" className="count-metric" label="Con stock contado" value={formatQuantity(s.counted)} caption={s.counted ? `${formatQuantity(s.counted_with_difference)} con diferencia` : undefined} />
        <MetricCard module="inventario" className="count-metric count-metric--warning" label="Sin Alegra" value={formatQuantity(s.without_alegra)} />
        <MetricCard module="inventario" className="count-metric count-metric--warning" label="Productos por crear" value={formatQuantity(s.to_create)} />
      </div>
      {s.present_inactive > 0 && <InlineMessage tone="info">{s.present_inactive} producto(s) inactivo(s) aparecieron en la tienda: revisa si deben reactivarse.</InlineMessage>}

      <div className="purchase-filters" role="group" aria-label="Sección del reporte">
        {SECTIONS.map(([value, label]) => <TouchButton key={value} variant={section === value ? "primary" : "secondary"} module="inventario" size="md" onClick={() => onSection(value)}>{label} ({formatQuantity(s[value])})</TouchButton>)}
      </div>
      <div className="ui-row" style={{ justifyContent: "space-between" }}>
        <p className="ui-empty-hint">{hint}</p>
        <div className="ui-row">
          {section === "counted" && s.counted > 0 && <a className="ui-btn ui-btn--secondary ui-btn--md" href={api.auditCountTemplateUrl(report.audit.id)}><CloudDownload size={22} aria-hidden="true" />Plantilla de conteo</a>}
          <a className="ui-btn ui-btn--secondary ui-btn--md" href={api.auditCsvUrl(report.audit.id, section)}><CloudDownload size={22} aria-hidden="true" />CSV completo</a>
        </div>
      </div>

      <div className="sale__cart">
        {section === "to_create" ? (report.sections.to_create.length ? (
          <DataTable caption="Productos por crear" className="count-cart" module="inventario" density="dense" stack={compact}>
            <thead><tr><Th>Código</Th><Th numeric>Escaneos</Th><Th>Primer escaneo</Th><Th>Último escaneo</Th></tr></thead>
            <tbody>
              {report.sections.to_create.map(code => (
                <Tr key={code.barcode}>
                  <Td label="Código" className="ui-num">{code.barcode}</Td>
                  <Td numeric label="Escaneos" className="ui-num">{formatQuantity(code.scan_count)}</Td>
                  <Td label="Primer escaneo" muted>{dateTimeShort.format(new Date(code.first_scanned_at))}</Td>
                  <Td label="Último escaneo" muted>{dateTimeShort.format(new Date(code.last_scanned_at))}</Td>
                </Tr>
              ))}
            </tbody>
          </DataTable>
        ) : <p className="ui-empty-hint">No se escanearon códigos desconocidos.</p>) : rows.length ? (
          <DataTable caption="Productos de la sección" className="count-cart" module="inventario" density="dense" stack={compact}>
            <thead><tr><Th>Producto</Th><Th>Categoría</Th><Th numeric>Stock sistema</Th>{showCount && <Th numeric>Cantidad</Th>}{showCount && <Th numeric>Diferencia</Th>}<Th>Alegra</Th></tr></thead>
            <tbody>
              {rows.map(row => (
                <Tr key={row.product_id}>
                  <Td label="Producto">{row.name}<small className="ui-td--muted">{row.barcode ?? "Sin código"}{row.active === false ? " · Inactivo" : ""}{row.last_sale_at !== undefined ? ` · ${row.last_sale_at ? `última venta ${dateTimeShort.format(new Date(row.last_sale_at))}` : "sin ventas"}` : ""}</small></Td>
                  <Td label="Categoría" muted>{row.category ?? "—"}</Td>
                  <Td numeric label="Stock sistema" className="ui-num">{formatQuantity(row.system_stock)}</Td>
                  {showCount && <Td numeric label="Cantidad" className="ui-num">{row.counted ? formatQuantity(row.quantity) : "Encontrado"}</Td>}
                  {showCount && <Td numeric label="Diferencia" className={row.difference ? (Number(row.difference) > 0 ? "pos ui-num" : "neg ui-num") : "ui-num"}>{row.difference === null || row.difference === undefined ? "—" : signed(Number(row.difference))}</Td>}
                  <Td label="Alegra">{row.alegra_item_id ? <StatusBadge tone="success">Vinculado</StatusBadge> : <StatusBadge tone="warning">Sin Alegra</StatusBadge>}</Td>
                </Tr>
              ))}
            </tbody>
          </DataTable>
        ) : <p className="ui-empty-hint">No hay productos en esta sección.</p>}
      </div>
      {section !== "to_create" && total > rows.length && <p className="ui-empty-hint">Mostrando {formatQuantity(rows.length)} de {formatQuantity(total)}. Descarga el CSV para ver la lista completa.</p>}
    </div>
  );
}
