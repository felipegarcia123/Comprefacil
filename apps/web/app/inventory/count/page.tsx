"use client";

import Link from "next/link";
import { ChangeEvent, useMemo, useState } from "react";
import { ArrowLeft, CircleCheck, CloudDownload, FileUp, ListChecks, Sparkles, Upload } from "lucide-react";
import { api } from "@/lib/api";
import { formatQuantity } from "@/lib/format";
import { useOperationalSession } from "@/components/OperationalSessionProvider";
import { useBreakpoint } from "@/lib/useBreakpoint";
import { ConfirmDialog, DataTable, InlineMessage, MetricCard, StatusBadge, Td, Th, Toldo, TouchButton, Tr } from "@/components/ui";
import type { CountConfirmResult, CountPreview, CountRowStatus } from "@/lib/types";

/*
 * VISUAL restyle only (Phase 4, section 10-14 of CLAUDE.md): every rule from the physical-count
 * flow (batch_id, expected_current_stock, 409 on stock drift, idempotent confirm, jornada-open
 * guard, unseen products untouched, an error blocks the whole batch, integer physical_count >= 0)
 * is untouched — same endpoints, same payloads, same state machine. Only the markup/CSS changed
 * (design tokens, ConfirmDialog instead of a bespoke confirm box, dense DataTable primitives).
 */
type Filter = "CHANGE" | "UNCHANGED" | "SKIPPED" | "ERROR" | "ALL";
const PAGE = 100;
const FILTERS: [Filter, string][] = [["CHANGE", "Con diferencia"], ["UNCHANGED", "Sin cambio"], ["SKIPPED", "Sin contar"], ["ERROR", "Errores"], ["ALL", "Todos"]];
const LABELS: Record<CountRowStatus, string> = { CHANGE: "Ajustar", UNCHANGED: "Sin cambio", SKIPPED: "Sin contar", ERROR: "Error" };
const TONE: Record<CountRowStatus, "warning" | "success" | "neutral" | "danger"> = { CHANGE: "warning", UNCHANGED: "success", SKIPPED: "neutral", ERROR: "danger" };
const STEPS = ["Plantilla y archivo", "Validación", "Preview", "Resultado"];

export default function InventoryCountPage() {
  const { summary: cashSummary, loading: cashLoading, error: cashError } = useOperationalSession();
  const compact = useBreakpoint() === "movil"; // <640: stacked preview rows (section 14 — mobile: filas apiladas).
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<CountPreview | null>(null), [result, setResult] = useState<CountConfirmResult | null>(null);
  const [filter, setFilter] = useState<Filter>("CHANGE"), [visible, setVisible] = useState(PAGE);
  const [busy, setBusy] = useState(false), [confirming, setConfirming] = useState(false), [error, setError] = useState("");
  const rows = useMemo(() => (preview?.rows ?? []).filter(row => filter === "ALL" || row.status === filter), [preview, filter]);
  // Same convention as /purchases: while the jornada state hasn't loaded (or failed to), never claim it's closed.
  const sessionOpen = preview ? preview.session_open : cashLoading || cashError ? true : cashSummary !== null;
  const step = result ? 3 : preview ? 2 : file ? 1 : 0;

  async function runPreview(selected: File) {
    setBusy(true); setError(""); setResult(null); setConfirming(false);
    try {
      const data = await api.previewCount(selected);
      setPreview(data); setVisible(PAGE);
      setFilter(data.summary.errors > 0 ? "ERROR" : "CHANGE");
    } catch (e) { setPreview(null); setError(e instanceof Error ? e.message : "No fue posible procesar el archivo"); }
    finally { setBusy(false); }
  }
  async function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files?.[0]; event.target.value = "";
    setError(""); setPreview(null); setResult(null); setFile(null);
    if (!selected) return;
    if (!selected.name.toLowerCase().endsWith(".xlsx")) return setError("Selecciona un archivo .xlsx (usa la plantilla descargada)");
    if (selected.size > 10 * 1024 * 1024) return setError("El archivo supera el límite de 10 MB");
    setFile(selected); await runPreview(selected);
  }
  async function confirm() {
    if (!preview || busy || result) return;
    setBusy(true); setError("");
    try {
      const changes = preview.rows.filter(row => row.status === "CHANGE" && row.product_id && row.current_stock !== null && row.physical_count !== null);
      setResult(await api.confirmCount({ batch_id: preview.batch_id, rows: changes.map(row => ({ product_id: row.product_id!, expected_current_stock: row.current_stock!, physical_count: row.physical_count! })) }));
      setConfirming(false);
    } catch (e) { setConfirming(false); setError(e instanceof Error ? e.message : "No fue posible aplicar el conteo"); }
    finally { setBusy(false); }
  }
  function reset() { setFile(null); setPreview(null); setResult(null); setError(""); setConfirming(false); }

  const s = preview?.summary;
  const canConfirm = !!preview && preview.can_confirm && !busy && !result;

  return (
    <main className="sale purchase" data-module="inventario">
      <Toldo module="inventario" />
      <div className="purchase__head">
        <div className="purchase__head-title"><Link href="/inventory" className="count-back-link"><ArrowLeft size={20} aria-hidden="true" />Inventario</Link><h1>Conteo físico</h1></div>
        <a href={api.countTemplateUrl} className="ui-btn ui-btn--secondary ui-btn--md"><CloudDownload size={22} aria-hidden="true" />Descargar plantilla</a>
      </div>

      <nav className="count-steps" aria-label="Progreso del conteo">
        {STEPS.map((label, index) => (
          <span key={label} className={index === step ? "count-steps__item count-steps__item--active" : index < step ? "count-steps__item count-steps__item--done" : "count-steps__item"}>
            {index < step ? <CircleCheck size={16} aria-hidden="true" /> : <span className="count-steps__num">{index + 1}</span>}
            {label}
          </span>
        ))}
      </nav>

      <div className="purchase__notice ui-stack">
        {!sessionOpen && !result && <InlineMessage tone="warning">La jornada está cerrada. Puedes revisar el preview, pero para confirmar el conteo debes abrir la caja.</InlineMessage>}
        {error && <InlineMessage tone="danger" actions={file && <TouchButton variant="secondary" module="inventario" disabled={busy} onClick={() => void runPreview(file)}>Repetir preview</TouchButton>}>{error}</InlineMessage>}
      </div>

      <div className="sale__main count-body">
        {!preview && !result && (
          <section className="sale-empty count-upload" data-module="inventario">
            <span className="sale-empty__icon"><Upload size={40} aria-hidden="true" /></span>
            <h2>{busy ? "Validando archivo…" : "Sube el conteo (.xlsx)"}</h2>
            <p>La plantilla identifica cada producto con <code>product_id</code> (también los que no tienen código de barras); no lo modifiques. Escribe <code>physical_count</code> (entero ≥ 0). Vacío = no contado. Los archivos anteriores con solo <code>barcode</code> siguen sirviendo. Este proceso no crea productos.</p>
            <label className={busy ? "ui-btn ui-btn--primary ui-btn--lg" : "ui-btn ui-btn--primary ui-btn--lg"} data-module="inventario" style={{ opacity: busy ? 0.6 : 1, pointerEvents: busy ? "none" : "auto" }}>
              <FileUp size={22} aria-hidden="true" /> Seleccionar archivo
              <input type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" disabled={busy} onChange={event => void chooseFile(event)} style={{ display: "none" }} />
            </label>
          </section>
        )}

        {preview && s && !result && (
          <div className="ui-stack">
            <div className="ui-row" style={{ justifyContent: "space-between" }}>
              <div><b>{file?.name}</b><p className="purchase-detail-code">Lote {preview.batch_id.replaceAll("-", "").slice(0, 8).toUpperCase()} · {s.file_rows} filas · {s.active_products_not_in_file} productos activos no incluidos (no se modifican)</p></div>
              <TouchButton variant="secondary" onClick={reset}>Cambiar archivo</TouchButton>
            </div>

            <div className="count-metrics">
              <MetricCard module="inventario" className="count-metric count-metric--warning" label="Con diferencia" value={formatQuantity(s.with_difference)} />
              <MetricCard module="inventario" className="count-metric count-metric--success" label="Sin cambio" value={formatQuantity(s.unchanged)} />
              <MetricCard module="inventario" className="count-metric count-metric--neutral" label="Sin contar" value={formatQuantity(s.skipped_blank)} />
              <MetricCard module="inventario" className="count-metric count-metric--danger" label="Errores" value={formatQuantity(s.errors)} />
            </div>
            <p className="count-net">Entran <b className="pos ui-num">+{formatQuantity(s.positive_units)}</b> · salen <b className="neg ui-num">{formatQuantity(s.negative_units)}</b> · neto <b className="ui-num">{Number(s.net_units) > 0 ? "+" : ""}{formatQuantity(s.net_units)}</b> unidades</p>
            {s.errors > 0 && <InlineMessage tone="danger">Hay {s.errors} fila(s) con error. Corrige el archivo y súbelo de nuevo: ningún conteo se aplica mientras haya errores.</InlineMessage>}

            <div className="purchase-filters" role="group" aria-label="Filtrar filas">
              {FILTERS.map(([value, label]) => <TouchButton key={value} variant={filter === value ? "primary" : "secondary"} module="inventario" size="md" onClick={() => { setFilter(value); setVisible(PAGE); }}>{label}</TouchButton>)}
            </div>

            <div className="sale__cart">
              {rows.length ? (
                <DataTable caption="Filas del conteo" className="count-cart" module="inventario" density="dense" stack={compact}>
                  <thead><tr><Th>Fila</Th><Th>Producto</Th><Th>Barcode</Th><Th numeric>Stock actual</Th><Th numeric>Conteo</Th><Th numeric>Delta</Th><Th>Estado</Th></tr></thead>
                  <tbody>
                    {rows.slice(0, visible).map(row => (
                      <Tr key={row.row_number}>
                        <Td label="Fila" muted>{row.row_number}</Td>
                        <Td label="Producto">{row.name ?? "—"}{row.errors.map(x => <small key={x} className="count-row-error">{x}</small>)}</Td>
                        <Td label="Código" muted className="ui-num">{row.barcode ?? (row.source_barcode || (row.product_id || row.source_product_id ? "Sin código" : "—"))}</Td>
                        <Td numeric label="Stock actual" className="ui-num">{row.current_stock === null ? "—" : formatQuantity(row.current_stock)}</Td>
                        <Td numeric label="Conteo" className="ui-num">{row.physical_count ?? "—"}</Td>
                        <Td numeric label="Delta" className={row.delta === null ? "ui-num" : Number(row.delta) > 0 ? "pos ui-num" : Number(row.delta) < 0 ? "neg ui-num" : "ui-num"}>{row.delta === null ? "—" : `${Number(row.delta) > 0 ? "+" : ""}${formatQuantity(row.delta)}`}</Td>
                        <Td label="Estado"><StatusBadge tone={TONE[row.status]}>{LABELS[row.status]}</StatusBadge></Td>
                      </Tr>
                    ))}
                  </tbody>
                </DataTable>
              ) : <p className="ui-empty-hint">No hay filas en esta vista.</p>}
            </div>
            {rows.length > visible && <p className="ui-empty-hint">Mostrando {visible} de {rows.length}. <button className="link-button" onClick={() => setVisible(visible + PAGE)}>Mostrar más</button></p>}

            <div className="ui-form__actions">
              <TouchButton variant="secondary" size="lg" onClick={reset}>Cancelar</TouchButton>
              <TouchButton size="lg" module="inventario" icon={ListChecks} disabled={!canConfirm} onClick={() => setConfirming(true)}>Aplicar {s.with_difference} ajuste(s)</TouchButton>
            </div>
            {!preview.can_confirm && s.errors === 0 && s.with_difference === 0 && <p className="ui-empty-hint">No hay diferencias que aplicar.</p>}
          </div>
        )}

        {result && (
          <section className="count-result">
            <span className="count-result__icon"><Sparkles size={32} aria-hidden="true" /></span>
            <h2>Conteo aplicado</h2>
            <p>{result.reason}</p>
            <div className="count-metrics">
              <MetricCard module="inventario" className="count-metric count-metric--success" label="Movimientos creados" value={formatQuantity(result.movements_created)} />
              <MetricCard module="inventario" className="count-metric" label="Unidades que entran" value={formatQuantity(Number(result.positive_units))} />
              <MetricCard module="inventario" className="count-metric count-metric--warning" label="Unidades que salen" value={formatQuantity(Math.abs(Number(result.negative_units)))} />
              <MetricCard module="inventario" className="count-metric" label="Neto" value={formatQuantity(Number(result.net_units))} />
            </div>
            <div className="ui-form__actions" style={{ justifyContent: "center" }}>
              <TouchButton variant="secondary" size="lg" onClick={reset}>Nuevo conteo</TouchButton>
              <Link className="ui-btn ui-btn--primary ui-btn--lg" data-module="inventario" href="/inventory">Ver inventario</Link>
            </div>
          </section>
        )}
      </div>

      <ConfirmDialog
        open={confirming} module="inventario" title="Confirmar conteo físico" confirmLabel="Confirmar y aplicar" cancelLabel="Revisar"
        busy={busy} onCancel={() => setConfirming(false)} onConfirm={() => void confirm()}
      >
        Se crearán {s?.with_difference ?? 0} movimientos ADJUSTMENT (neto {s && Number(s.net_units) > 0 ? "+" : ""}{s ? formatQuantity(s.net_units) : 0} unidades). Las filas sin diferencia no generan movimiento. Esta acción no se puede aplicar dos veces.
      </ConfirmDialog>
    </main>
  );
}
