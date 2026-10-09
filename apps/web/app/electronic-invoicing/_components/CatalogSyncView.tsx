"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Download, Link2, LoaderCircle, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { cx } from "@/lib/cx";
import { dateTime, errorText, money } from "@/lib/format";
import { useBreakpoint } from "@/lib/useBreakpoint";
import type { CatalogSyncPage, CatalogSyncState, CatalogSyncStatus, PriorityPosProduct } from "@/lib/types";
import { ConfirmDialog, DataTable, InlineMessage, Pagination, SearchBar, StatusBadge, Td, Th, TouchButton, Tr } from "@/components/ui";

type Tone = "neutral" | "warning" | "info" | "success" | "danger";
const STATUS: Record<CatalogSyncStatus, { label: string; tone: Tone }> = {
  ALREADY_LINKED: { label: "Vinculado", tone: "success" },
  EXACT_MATCH: { label: "Coincidencia exacta", tone: "info" },
  UPC_EAN_MATCH: { label: "Coincidencia UPC/EAN", tone: "info" },
  READY_TO_CREATE: { label: "Se creará al facturar", tone: "neutral" },
  NO_BARCODE: { label: "Sin código (se creará al facturar)", tone: "neutral" },
  REVIEW_REQUIRED: { label: "Revisar", tone: "warning" },
  CONFLICT: { label: "Conflicto", tone: "danger" },
  INVALID_BARCODE: { label: "Código no válido", tone: "warning" },
  ERROR: { label: "Error", tone: "danger" },
};
/** Counter -> the list it opens (a counter may cover two statuses, e.g. exact + UPC/EAN). */
const COUNTERS: { key: keyof NonNullable<CatalogSyncState["summary"]>; label: string; status: CatalogSyncStatus | "" }[] = [
  { key: "active", label: "Activos", status: "" },
  { key: "linked", label: "Vinculados", status: "ALREADY_LINKED" },
  { key: "unlinked", label: "Sin vincular", status: "" },
  { key: "exact_match", label: "Coincidencia exacta", status: "EXACT_MATCH" },
  { key: "ready_to_create", label: "Se crearán al facturar", status: "READY_TO_CREATE" },
  { key: "review", label: "Revisar", status: "REVIEW_REQUIRED" },
  { key: "no_barcode", label: "Sin código", status: "NO_BARCODE" },
  { key: "conflicts", label: "Conflictos", status: "CONFLICT" },
];

/**
 * Catálogo Alegra: DIAGNOSIS only (since 2026-10-02). Invoicing links or creates each missing item on demand
 * (ensure_alegra_item), so nothing here is a prerequisite for the cutover. The analysis only reads Alegra;
 * "Vincular" writes locally after a live re-check, for unambiguous code matches. There is no create button.
 */
export function CatalogSyncView() {
  const [state, setState] = useState<CatalogSyncState | null>(null);
  const [page, setPage] = useState<CatalogSyncPage | null>(null);
  const compact = useBreakpoint() === "movil";
  // Start on the actionable list (exact matches) when there is one; otherwise show everything.
  const [status, setStatus] = useState<CatalogSyncStatus | "">("");
  const defaulted = useRef(false);
  const [query, setQuery] = useState("");
  const [pageNumber, setPageNumber] = useState(1);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ tone: "success" | "warning" | "danger" | "info"; text: string } | null>(null);
  const [priority, setPriority] = useState<PriorityPosProduct[]>([]);
  const poll = useRef<number | null>(null);

  const loadState = useCallback(async () => {
    const next = await api.catalogSyncState();
    setState(next);
    if (!defaulted.current && next.summary) {
      defaulted.current = true;
      if (next.summary.exact_match > 0) setStatus("EXACT_MATCH");
    }
    return next;
  }, []);
  const loadRows = useCallback(async () => {
    try { setPage(await api.catalogAnalysis({ status, q: query.trim(), page: pageNumber })); }
    catch (cause) { setNotice({ tone: "danger", text: errorText(cause) }); }
  }, [status, query, pageNumber]);

  useEffect(() => { void loadState().catch(cause => setNotice({ tone: "danger", text: errorText(cause) })); }, [loadState]);
  useEffect(() => { api.priorityPosProducts().then(setPriority).catch(() => setPriority([])); }, []);
  useEffect(() => { const timer = window.setTimeout(() => void loadRows(), 250); return () => window.clearTimeout(timer); }, [loadRows]);
  useEffect(() => () => { if (poll.current) window.clearInterval(poll.current); }, []);

  const running = state?.latest_run?.status === "RUNNING";
  useEffect(() => {
    if (!running || poll.current) return;
    // The full remote scan takes a few minutes: check back every 10 s until it finishes.
    poll.current = window.setInterval(async () => {
      const next = await loadState().catch(() => null);
      if (next && next.latest_run?.status !== "RUNNING") {
        if (poll.current) window.clearInterval(poll.current);
        poll.current = null;
        setNotice(next.latest_run?.status === "FAILED"
          ? { tone: "danger", text: `El análisis falló: ${next.latest_run.error ?? "error desconocido"}` }
          : { tone: "success", text: "Análisis actualizado" });
        void loadRows();
      }
    }, 10000);
  }, [running, loadState, loadRows]);

  async function startAnalysis() {
    setNotice(null);
    try { await api.startCatalogAnalysis(); await loadState(); }
    catch (cause) { setNotice({ tone: "danger", text: errorText(cause) }); }
  }

  const limit = state?.batch_limit ?? 50;
  const linkable = (page?.rows ?? []).filter(row => row.proposed_action === "LINK");
  function toggle(id: string) {
    setSelected(current => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else if (next.size < limit) next.add(id);
      return next;
    });
  }

  async function link() {
    setBusy(true); setNotice(null);
    try {
      const result = await api.linkCatalogProducts([...selected], false);
      const linked = result.summary.LINKED ?? 0;
      const other = result.results.length - linked;
      setNotice({ tone: other ? "warning" : "success",
        text: `${linked} vinculado(s)${other ? ` · ${other} sin vincular: ${result.results.filter(r => r.status !== "LINKED").slice(0, 3).map(r => r.reason).join(" · ")}` : ""}` });
      setSelected(new Set());
      await Promise.all([loadState(), loadRows()]);
    } catch (cause) { setNotice({ tone: "danger", text: errorText(cause) }); }
    finally { setBusy(false); setConfirming(false); }
  }

  const summary = state?.summary;
  return (
    <section className="einv-card" aria-labelledby="einv-catalog-sync-title">
      <div className="einv-card__head">
        <h2 id="einv-catalog-sync-title">Catálogo Alegra</h2>
        <div className="ui-row">
          <TouchButton variant="secondary" icon={RefreshCw} loading={running} disabled={running || !state?.alegra_configured} onClick={() => void startAnalysis()}>
            {running ? "Analizando…" : "Actualizar análisis"}
          </TouchButton>
          {state?.summary_run && <a className="ui-btn ui-btn--ghost ui-btn--md" href={api.catalogAnalysisCsvUrl} download><Download size={24} aria-hidden="true" />CSV</a>}
        </div>
      </div>
      <p className="einv-card__hint">
        {state?.summary_run?.finished_at ? `Último análisis ${dateTime.format(new Date(state.summary_run.finished_at))} · ${state.summary_run.remote_items ?? "—"} ítems en Alegra. ` : "Todavía no hay análisis. "}
        Solo se vincula por código idéntico o equivalente UPC/EAN; un nombre parecido nunca vincula.
      </p>
      <InlineMessage tone="info">
        Los productos pendientes se crearán automáticamente cuando sean necesarios para facturación.
        {state && !state.create_enabled && " La creación automática todavía está desactivada."}
      </InlineMessage>
      {running && <p className="customer-picker__hint" role="status"><LoaderCircle className="ui-btn__spin" size={20} aria-hidden="true" />Leyendo el catálogo completo de Alegra (unos minutos)…</p>}
      {notice && <InlineMessage tone={notice.tone} onDismiss={() => setNotice(null)}>{notice.text}</InlineMessage>}

      {summary && (
        <div className="einv__summary einv-catalog__counters" role="group" aria-label="Resumen del catálogo">
          {COUNTERS.map(counter => (
            <button key={counter.key} type="button" className="einv__figure" aria-pressed={counter.status !== "" && status === counter.status}
              onClick={() => { setStatus(counter.status); setPageNumber(1); setSelected(new Set()); }}>
              <span className="einv__figure-value ui-num">{Number(summary[counter.key] ?? 0).toLocaleString("es-CO")}</span>
              <span className="einv__figure-label">{counter.label}</span>
            </button>
          ))}
        </div>
      )}

      {page?.run && (
        <>
          <div className="einv__filters einv-catalog__filters">
            <label className="ui-field"><span>Estado</span>
              <select className="inventory-select" value={status} onChange={event => { setStatus(event.target.value as CatalogSyncStatus | ""); setPageNumber(1); setSelected(new Set()); }}>
                <option value="">Todos</option>
                {(Object.keys(STATUS) as CatalogSyncStatus[]).map(value => <option key={value} value={value}>{STATUS[value].label}</option>)}
              </select>
            </label>
            <SearchBar label="Buscar producto en el análisis" placeholder="Nombre o código" value={query} onChange={value => { setQuery(value); setPageNumber(1); }} module="informes" />
          </div>
          {linkable.length > 0 && (
            <div className="ui-row einv-catalog__actions">
              <TouchButton variant="secondary" onClick={() => setSelected(new Set(linkable.slice(0, limit).map(row => row.product_id)))}>Seleccionar coincidencias de esta página</TouchButton>
              <TouchButton module="informes" icon={Link2} disabled={!selected.size || busy} loading={busy} onClick={() => setConfirming(true)}>
                Vincular seleccionados{selected.size ? ` (${selected.size})` : ""}
              </TouchButton>
              <span className="einv-card__hint">Máximo {limit} por lote.</span>
            </div>
          )}
          <DataTable caption="Análisis del catálogo" className="einv-cart" module="informes" density="dense" maxHeight="420px" stack={compact}>
            <thead><tr><Th><span className="ui-sr-only">Seleccionar</span></Th><Th>Producto</Th><Th>Código</Th><Th>Ítem de Alegra</Th><Th>Estado</Th></tr></thead>
            <tbody>
              {page.rows.map(row => {
                const canLink = row.proposed_action === "LINK";
                return (
                  <Tr key={row.product_id}>
                    <Td label="Seleccionar">
                      {canLink && (
                        <label className={cx("einv-catalog__check", selected.has(row.product_id) && "einv-catalog__check--on")}>
                          <input type="checkbox" checked={selected.has(row.product_id)} onChange={() => toggle(row.product_id)} aria-label={`Seleccionar ${row.product_name}`} />
                        </label>
                      )}
                    </Td>
                    <Td label="Producto"><span className="sale-name">{row.product_name}<small className="einv-detail__why ui-num">{money.format(Number(row.sale_price))}</small></span></Td>
                    <Td label="Código" muted className="ui-num">{row.barcode ?? "Sin código"}</Td>
                    <Td label="Ítem de Alegra" muted>
                      {row.matched_alegra_item_id ? `${row.matched_alegra_item_id} · ${row.matched_alegra_name ?? ""}` : "—"}
                      {row.review_hint && <small className="einv-detail__why">{row.review_hint}</small>}
                    </Td>
                    <Td label="Estado">
                      <StatusBadge tone={STATUS[row.sync_status].tone}>{STATUS[row.sync_status].label}</StatusBadge>
                      {row.reason && <small className="einv-detail__why">{row.reason}</small>}
                    </Td>
                  </Tr>
                );
              })}
            </tbody>
          </DataTable>
          {page.total_pages > 1 && <Pagination page={page.page} totalPages={page.total_pages} total={page.total} itemLabel="productos" module="informes" onChange={setPageNumber} />}
        </>
      )}

      <h3 className="einv-detail__h3">Productos históricos pendientes de crear en el POS</h3>
      <p className="einv-card__hint">Existen en Alegra y se vendieron, pero el POS no los tiene. No se crean hasta conocer su costo real.</p>
      <ul className="einv-groups">
        {priority.map(item => (
          <li key={item.alegra_item_id}>
            <span>{item.name}<small className="einv-detail__why">Ítem {item.alegra_item_id} · {item.category} · sin código de barras · costo pendiente</small></span>
            <b className="ui-num">{money.format(item.sale_price)}</b>
          </li>
        ))}
      </ul>

      <ConfirmDialog open={confirming} module="informes" title="Vincular productos con Alegra" confirmLabel="Vincular" cancelLabel="Volver"
        busy={busy} onCancel={() => setConfirming(false)} onConfirm={() => void link()}>
        Se vincularán {selected.size} producto(s) con su ítem de Alegra. Antes de guardar se verifica de nuevo en Alegra que el ítem exista y que el código coincida. No se crea ni modifica nada en Alegra.
      </ConfirmDialog>
    </section>
  );
}
