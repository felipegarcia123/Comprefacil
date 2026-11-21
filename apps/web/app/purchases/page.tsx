"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { History, LoaderCircle, PackageOpen, Plus } from "lucide-react";
import { api } from "@/lib/api";
import { dateTime, errorText, money } from "@/lib/format";
import { publishPurchaseContext } from "@/lib/workContexts";
import type { InventoryDetail, PaymentChannel, Purchase, ReceiveComparisonIn, SchedulePurchaseIn, Supplier } from "@/lib/types";
import type { RoundingPolicy } from "@/lib/pricing";
import { ConfirmDialog, Display, InlineMessage, Sheet, Toldo, TouchButton } from "@/components/ui";
import { useBreakpoint } from "@/lib/useBreakpoint";
import { PurchaseStatusBadge } from "@/components/PurchaseStatusBadge";
import { useOperationalSession } from "@/components/OperationalSessionProvider";
import { EmptyLines, LineTable } from "./_components/LineTable";
import { HistorySheet } from "./_components/HistorySheet";
import { PricingSheet } from "./_components/PricingSheet";
import { ProductPicker } from "./_components/ProductPicker";
import { ReceiveSheet } from "./_components/ReceiveSheet";
import { ScheduleSheet } from "./_components/ScheduleSheet";
import { PurchaseAgendaSheet } from "./_components/PurchaseAgendaSheet";
import { paymentTermsLabel } from "@/lib/smartPurchasing";
import { SupplierPicker } from "./_components/SupplierPicker";
import { seedToLine, type DraftLine, type ProductSeed } from "./_components/types";

type Notice = { tone: "success" | "info" | "warning" | "danger"; text: string };
type SaveState = "idle" | "saving" | "saved" | "error";

// Which purchase is being viewed/worked on right now survives leaving /purchases and coming back.
// `activeDraftId` (only ever a DRAFT) is what the global ContextBar shows; `viewedId` is just "what's
// on screen" and may also point at a RECEIVED/CANCELLED purchase opened from history for reference.
const STORE_KEY = "pos.purchases.ui.v1";
const PENDING_CREATE_KEY = "pos.purchases.pending-create.v1";
type Stored = { viewedId?: string; activeDraftId?: string };
function readStored(): Stored { try { return JSON.parse(sessionStorage.getItem(STORE_KEY) || "{}"); } catch { return {}; } }
function writeStored(value: Stored) { try { sessionStorage.setItem(STORE_KEY, JSON.stringify(value)); } catch { /* storage may be unavailable */ } }

function inventoryDetailToSeed(p: InventoryDetail): ProductSeed {
  return { id: p.id, barcode: p.barcode, name: p.name, sale_price: p.sale_price, unit_cost: p.unit_cost, last_purchase_cost: p.last_purchase_cost, current_stock: p.current_stock };
}
function fallbackSeed(item: Purchase["items"][number]): ProductSeed {
  return { id: item.product_id, barcode: item.barcode, name: item.product_name, sale_price: "0", unit_cost: String(item.unit_cost), last_purchase_cost: null, current_stock: "0" };
}
function toBody(purchase: Purchase) {
  return {
    supplier_id: purchase.supplier_id, invoice_number: purchase.invoice_number || undefined, notes: purchase.notes || undefined,
    items: purchase.items.map(item => ({ product_id: item.product_id, quantity: Number(item.quantity), unit_cost: Number(item.unit_cost) })),
  };
}
function withLineTotals(purchase: Purchase): Purchase {
  const items = purchase.items.map(item => ({ ...item, line_total: String(Number(item.quantity) * Number(item.unit_cost)) }));
  return { ...purchase, items, subtotal: String(items.reduce((sum, item) => sum + Number(item.line_total), 0)), total: items.reduce((sum, item) => sum + Number(item.line_total), 0).toString() };
}

export default function PurchasesPage() {
  const { summary: cash, loading: cashLoading, error: cashError } = useOperationalSession();
  const cajaOpen = Boolean(cash);
  const cajaClosed = !cashLoading && !cashError && !cash;
  const breakpoint = useBreakpoint();
  const wide = breakpoint === "pos" || breakpoint === "laptop";
  const [summaryOpen, setSummaryOpen] = useState(false);

  const [phase, setPhase] = useState<"loading" | "ready">("loading");
  const [channels, setChannels] = useState<PaymentChannel[]>([]);
  const [policy, setPolicy] = useState<RoundingPolicy>("round_up_500");

  const [purchase, setPurchase] = useState<Purchase | null>(null);
  const [productCache, setProductCache] = useState<Record<string, ProductSeed>>({});
  const [selectedId, setSelectedId] = useState("");
  const [detailId, setDetailId] = useState("");

  const [supplierPickerOpen, setSupplierPickerOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [receiveOpen, setReceiveOpen] = useState(false);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [agendaOpen, setAgendaOpen] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);

  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [savedAt, setSavedAt] = useState<Date | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const pendingCreate = useRef<{ body: ReturnType<typeof toBody>; key: string } | null>(null);

  const purchaseRef = useRef<Purchase | null>(null);
  useEffect(() => { purchaseRef.current = purchase; }, [purchase]);

  const isDraft = purchase?.status === "DRAFT";

  // ---- load -----------------------------------------------------------------------------------
  const load = useCallback(async () => {
    setPhase("loading");
    const [channelResult, policyResult] = await Promise.allSettled([api.paymentChannels(true), api.pricingConfig()]);
    if (channelResult.status === "fulfilled") setChannels(channelResult.value);
    if (policyResult.status === "fulfilled") setPolicy(policyResult.value.commercial_rounding_policy);

    const requested = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("id") : null;
    const stored = readStored();
    const wantedId = requested || stored.viewedId;
    if (wantedId) {
      try {
        const loaded = await api.purchase(wantedId);
        setPurchase(loaded);
        if (loaded.status === "DRAFT") {
          writeStored({ viewedId: loaded.id, activeDraftId: loaded.id });
          const seeds = await Promise.all(loaded.items.map(item =>
            api.inventoryDetail(item.product_id).then(inventoryDetailToSeed).catch(() => fallbackSeed(item))));
          setProductCache(Object.fromEntries(loaded.items.map((item, index) => [item.product_id, seeds[index]])));
        } else {
          writeStored({ viewedId: loaded.id, activeDraftId: stored.activeDraftId });
        }
      } catch { writeStored({}); }
    }
    setPhase("ready");
  }, []);
  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    publishPurchaseContext(isDraft && purchase ? { id: purchase.id, label: `Compra · ${purchase.supplier_name}`, module: "compras" } : null);
  }, [isDraft, purchase]);

  useEffect(() => {
    if (!notice || notice.tone === "success") return;
    const timer = window.setTimeout(() => setNotice(null), 7000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const refocus = useCallback(() => {
    window.setTimeout(() => {
      const active = document.activeElement;
      if (active instanceof HTMLInputElement && active !== inputRef.current) return;
      if (document.querySelector("dialog[open]")) return;
      inputRef.current?.focus();
    }, 0);
  }, []);

  // ---- autosave (debounced PUT; add/remove flush immediately) ---------------------------------
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveQueue = useRef<Promise<unknown>>(Promise.resolve());
  const pendingSaves = useRef(0);
  const scheduleSave = useCallback((next: Purchase, immediate = false) => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    const run = () => {
      pendingSaves.current++; setSaveState("saving");
      saveQueue.current = saveQueue.current.catch(() => undefined)
        .then(() => api.updatePurchase(next.id, toBody(next)))
        .then(saved => {
          pendingSaves.current--;
          if (pendingSaves.current === 0) { setPurchase(saved); setSaveState("saved"); setSavedAt(new Date()); }
        })
        .catch(error => {
          pendingSaves.current--;
          setSaveState("error"); setNotice({ tone: "danger", text: errorText(error) });
        });
    };
    if (immediate) run(); else saveTimer.current = setTimeout(run, 550);
  }, []);

  // ---- supplier selection / new draft -----------------------------------------------------------
  function onSupplierChosen(supplier: Supplier) {
    setSupplierPickerOpen(false);
    try {
      const saved = JSON.parse(sessionStorage.getItem(PENDING_CREATE_KEY) || "null") as typeof pendingCreate.current;
      pendingCreate.current = saved?.body?.supplier_id === supplier.id && typeof saved.key === "string" ? saved : null;
    } catch { pendingCreate.current = null; }
    if (purchase && purchase.status === "DRAFT") {
      setNotice({ tone: "info", text: `Tenías una compra en curso con ${purchase.supplier_name}; sigue guardada, retómala desde el historial.` });
    }
    setPurchase({ id: "", supplier_id: supplier.id, supplier_name: supplier.name, status: "DRAFT", invoice_number: null,
      purchased_at: new Date().toISOString(), received_at: null, notes: null, subtotal: "0", total: "0",
      created_at: new Date().toISOString(), created_by: "", payment: null, items: [],
      scheduled_at: null, expected_delivery_date: null, payment_terms: null, payment_due_date: null,
      estimated_total: null, offer_notes: null, paid_amount: "0", outstanding_amount: null });
    writeStored({});
    refocus();
  }

  async function addProduct(seed: ProductSeed) {
    const current = purchaseRef.current;
    setProductCache(cache => ({ ...cache, [seed.id]: seed }));
    if (!current) { setNotice({ tone: "warning", text: "Elige un proveedor antes de agregar productos." }); return; }

    const existing = current.items.find(item => item.product_id === seed.id);
    const line = seedToLine(seed, existing ? { id: seed.id, barcode: seed.barcode, name: seed.name, sale_price: seed.sale_price, current_stock: seed.current_stock, last_purchase_cost: seed.last_purchase_cost, quantity: Number(existing.quantity), purchase_cost: Number(existing.unit_cost), target_margin: 30 } : undefined);

    if (!current.id) {
      // First real content: this is the moment the DRAFT is actually created in the backend.
      setBusy(true);
      try {
        pendingCreate.current ??= { key: crypto.randomUUID(), body: {
          supplier_id: current.supplier_id, invoice_number: current.invoice_number || undefined, notes: current.notes || undefined,
          items: [{ product_id: seed.id, quantity: line.quantity, unit_cost: line.purchase_cost }],
        } };
        try { sessionStorage.setItem(PENDING_CREATE_KEY, JSON.stringify(pendingCreate.current)); } catch { /* retry still works while mounted */ }
        const created = await api.createPurchase(pendingCreate.current.body, pendingCreate.current.key);
        pendingCreate.current = null;
        try { sessionStorage.removeItem(PENDING_CREATE_KEY); } catch { /* storage may be unavailable */ }
        setPurchase(created); setSelectedId(seed.id);
        writeStored({ viewedId: created.id, activeDraftId: created.id });
        setSaveState("saved"); setSavedAt(new Date());
      } catch (error) { setNotice({ tone: "danger", text: errorText(error) }); }
      finally { setBusy(false); refocus(); }
      return;
    }

    const items = existing
      ? current.items.map(item => item.product_id === seed.id ? { ...item, quantity: String(line.quantity), unit_cost: String(line.purchase_cost), line_total: String(line.quantity * line.purchase_cost) } : item)
      : [...current.items, { id: seed.id, product_id: seed.id, product_name: seed.name, barcode: seed.barcode, quantity: String(line.quantity), unit_cost: String(line.purchase_cost), line_total: String(line.quantity * line.purchase_cost), ordered_quantity: null, ordered_unit_cost: null, offer_list_unit_cost: null, offer_discount_per_unit: null, offer_note: null, difference_reason: null, actual_unit_cost: null, quantity_difference: null, unit_cost_difference: null, amount_difference: null }];
    const next = withLineTotals({ ...current, items });
    setPurchase(next); setSelectedId(seed.id);
    scheduleSave(next, true); // adding a line is a discrete action: save right away, not debounced
    refocus();
  }

  function changeLine(productId: string, patch: { quantity?: number; unit_cost?: number }) {
    const current = purchaseRef.current; if (!current) return;
    const items = current.items.map(item => item.product_id === productId
      ? { ...item, quantity: String(patch.quantity ?? item.quantity), unit_cost: String(patch.unit_cost ?? item.unit_cost),
          line_total: String((patch.quantity ?? Number(item.quantity)) * (patch.unit_cost ?? Number(item.unit_cost))) }
      : item);
    const next = withLineTotals({ ...current, items });
    setPurchase(next);
    scheduleSave(next);
  }

  function removeLine(productId: string) {
    const current = purchaseRef.current; if (!current) return;
    if (current.items.length <= 1) return; // an empty DRAFT can't be saved — cancel the purchase instead
    const items = current.items.filter(item => item.product_id !== productId);
    const next = withLineTotals({ ...current, items });
    setPurchase(next); setSelectedId("");
    scheduleSave(next, true);
  }

  function updateField(patch: { invoice_number?: string; notes?: string }) {
    const current = purchaseRef.current; if (!current) return;
    const next = { ...current, ...patch };
    setPurchase(next);
    if (current.items.length) scheduleSave(next);
  }

  // ---- receive / cancel -------------------------------------------------------------------------
  async function receive(channelId: string, reference: string, notesValue: string) {
    if (!purchase) return;
    const received = await api.receivePurchaseWithPayment(purchase.id, channelId, reference, notesValue);
    setPurchase(received); setReceiveOpen(false);
    writeStored({ viewedId: received.id }); // no longer the active draft: it's done
    setNotice({ tone: "success", text: "Mercancía recibida y pago registrado." });
  }

  async function payExisting(channelId: string, reference: string, notesValue: string) {
    if (!purchase || purchase.status !== "RECEIVED") return;
    const paid = await api.payPurchase(purchase.id, channelId, reference, notesValue);
    setPurchase(paid); setReceiveOpen(false);
    setNotice({ tone: "success", text: "Pago al proveedor registrado." });
  }

  async function scheduleOrder(body: SchedulePurchaseIn) {
    if (!purchase?.id || purchase.status !== "DRAFT") return;
    setBusy(true);
    try {
      if (saveTimer.current) { clearTimeout(saveTimer.current); saveTimer.current = null; }
      await saveQueue.current;
      // The server must hold the final draft before its ordered_* snapshot is frozen.
      const saved = await api.updatePurchase(purchase.id, toBody(purchase));
      const scheduled = await api.schedulePurchase(saved.id, body);
      setPurchase(scheduled); setScheduleOpen(false);
      writeStored({ viewedId: scheduled.id });
      setNotice({ tone: "success", text: "Pedido programado. No se modificaron inventario ni caja." });
    } catch (cause) {
      // A lost response after a successful schedule must not strand the UI in DRAFT.
      const latest = await api.purchase(purchase.id).catch(() => null);
      if (latest?.status === "SCHEDULED") {
        const confirmed = await api.schedulePurchase(latest.id, body).catch(() => null);
        if (confirmed) {
          setPurchase(confirmed); setScheduleOpen(false); writeStored({ viewedId: confirmed.id });
          setNotice({ tone: "info", text: "El pedido ya quedó programado; se recuperó su estado desde el servidor." });
          return;
        }
      }
      throw cause;
    } finally { setBusy(false); }
  }

  async function receiveScheduled(comparison: ReceiveComparisonIn, payment?: { channelId: string; reference: string; notes: string }) {
    if (!purchase?.id || purchase.status !== "SCHEDULED") return;
    setBusy(true);
    try {
      const received = payment
        ? await api.receivePurchaseWithPayment(purchase.id, payment.channelId, payment.reference, payment.notes, comparison)
        : await api.receivePurchase(purchase.id, comparison);
      setPurchase(received); setReceiveOpen(false); writeStored({ viewedId: received.id });
      setNotice({ tone: "success", text: payment ? "Mercancía recibida y pago real registrado." : "Mercancía recibida; obligación real registrada para el proveedor." });
    } finally { setBusy(false); }
  }

  async function cancelDraft() {
    if (!purchase) return;
    setBusy(true);
    try {
      await api.cancelPurchase(purchase.id);
      setPurchase(null); setProductCache({}); setSelectedId("");
      writeStored({});
      setNotice({ tone: "info", text: "Compra cancelada. El pedido queda en el historial sin afectar inventario ni caja." });
    } catch (error) { setNotice({ tone: "danger", text: errorText(error) }); }
    finally { setBusy(false); setCancelOpen(false); }
  }

  async function openPurchaseById(id: string) {
    setHistoryOpen(false);
    try {
      const loaded = await api.purchase(id);
      setPurchase(loaded);
      if (loaded.status === "DRAFT") {
        writeStored({ viewedId: loaded.id, activeDraftId: loaded.id });
        const seeds = await Promise.all(loaded.items.map(item => api.inventoryDetail(item.product_id).then(inventoryDetailToSeed).catch(() => fallbackSeed(item))));
        setProductCache(Object.fromEntries(loaded.items.map((item, index) => [item.product_id, seeds[index]])));
      } else {
        const stored = readStored();
        writeStored({ viewedId: loaded.id, activeDraftId: stored.activeDraftId });
      }
      setSelectedId("");
    } catch (error) { setNotice({ tone: "danger", text: errorText(error) }); }
  }

  async function openFromAgenda(id: string) {
    setAgendaOpen(false);
    await openPurchaseById(id);
  }

  // ---- render -------------------------------------------------------------------------------------
  if (phase === "loading") {
    return <main className="sale purchase" data-module="compras"><Toldo module="compras" /><div className="sale-status" role="status"><LoaderCircle className="ui-btn__spin" size={32} aria-hidden="true" />Cargando compras…</div></main>;
  }

  const detailLine: DraftLine | null = purchase && detailId
    ? (() => { const item = purchase.items.find(i => i.product_id === detailId); const seed = productCache[detailId];
        return item && seed ? { id: detailId, barcode: item.barcode, name: item.product_name, sale_price: seed.sale_price, current_stock: seed.current_stock, last_purchase_cost: seed.last_purchase_cost, quantity: Number(item.quantity), purchase_cost: Number(item.unit_cost), target_margin: 30 } : null; })()
    : null;
  const units = purchase?.items.reduce((sum, item) => sum + Number(item.quantity), 0) ?? 0;

  return (
    <main className="sale purchase" data-module="compras">
      <Toldo module="compras" />
      <div className="purchase__head">
        <div className="purchase__head-title">
          <h1>{purchase ? `Compra · ${purchase.supplier_name}` : "Compras"}</h1>
          {purchase && <PurchaseStatusBadge status={purchase.status} />}
          {isDraft && <SaveIndicator state={saveState} savedAt={savedAt} />}
        </div>
        <div className="ui-row">
          <TouchButton variant="secondary" onClick={() => setAgendaOpen(true)}>Agenda y previsión</TouchButton>
          <TouchButton variant="secondary" icon={History} onClick={() => setHistoryOpen(true)}>Historial</TouchButton>
          <TouchButton module="compras" icon={Plus} disabled={busy} onClick={() => setSupplierPickerOpen(true)}>Nueva compra</TouchButton>
        </div>
      </div>

      {notice && <div className="purchase__notice"><InlineMessage tone={notice.tone} onDismiss={() => setNotice(null)}>{notice.text}</InlineMessage></div>}

      {!purchase ? (
        <div className="sale-status">
          <div className="sale-closed" data-module="compras">
            <PackageOpen size={48} aria-hidden="true" />
            <h1>Empieza una compra</h1>
            <p>Elige un proveedor para crear un borrador, programar una entrega o registrar una compra.</p>
            <TouchButton module="compras" size="xl" onClick={() => setSupplierPickerOpen(true)}>Elegir proveedor</TouchButton>
          </div>
        </div>
      ) : isDraft ? (
        <>
          <div className="purchase__fields">
            <span className="purchase__field-ro"><small>Proveedor</small><b>{purchase.supplier_name}</b></span>
            <label className="ui-field purchase__field"><span>Factura / referencia</span><input value={purchase.invoice_number ?? ""} onChange={event => updateField({ invoice_number: event.target.value })} placeholder="Opcional" /></label>
            <span className="purchase__field-ro"><small>Fecha</small><b>{new Date(purchase.purchased_at).toLocaleDateString("es-CO")}</b></span>
          </div>
          <div className="sale__body">
            <section className="sale__main" aria-label="Compra activa">
              <ProductPicker disabled={busy} inputRef={inputRef} onAdd={seed => void addProduct(seed)} notify={text => setNotice({ tone: "warning", text })} />
              {cajaClosed && <InlineMessage tone="warning" title="La caja está cerrada">Puedes seguir armando esta compra; para recibirla necesitarás abrir la jornada.</InlineMessage>}
              <div className="sale__cart">
                {purchase.items.length ? (
                  <LineTable lines={purchase.items.map(item => ({ id: item.product_id, barcode: item.barcode, name: item.product_name, sale_price: productCache[item.product_id]?.sale_price ?? "0", current_stock: productCache[item.product_id]?.current_stock ?? "0", last_purchase_cost: productCache[item.product_id]?.last_purchase_cost ?? null, quantity: Number(item.quantity), purchase_cost: Number(item.unit_cost), target_margin: 30 }))}
                    selectedId={selectedId} onSelect={setSelectedId}
                    onQuantity={(id, qty) => changeLine(id, { quantity: qty })}
                    onDetail={setDetailId} onRemove={removeLine} removeDisabled={purchase.items.length <= 1} />
                ) : <EmptyLines />}
              </div>
              <label className="ui-field purchase__notes"><span>Notas (opcional)</span><textarea rows={2} value={purchase.notes ?? ""} onChange={event => updateField({ notes: event.target.value })} /></label>
            </section>
            {wide && (
              <section className="sale__panel purchase__panel" aria-label="Resumen de compra">
                <PurchaseSummary purchase={purchase} units={units} onHistory={() => setHistoryOpen(true)} onReceive={() => setReceiveOpen(true)} onSchedule={() => setScheduleOpen(true)} onCancel={() => setCancelOpen(true)} />
              </section>
            )}
          </div>
          {!wide && (
            <div className="sale__dock">
              <Display size="figure" label="Total compra" value={money.format(Number(purchase.total))} caption={`${purchase.items.length} ${purchase.items.length === 1 ? "referencia" : "referencias"}`} tickKey={purchase.total} live />
              <TouchButton size="cta" module="compras" onClick={() => setSummaryOpen(true)}>RECIBIR</TouchButton>
              <TouchButton variant="secondary" module="compras" onClick={() => setScheduleOpen(true)}>Programar pedido</TouchButton>
            </div>
          )}
          <Sheet open={!wide && summaryOpen} title="Resumen de compra" module="compras" onClose={() => setSummaryOpen(false)}>
            {!wide && summaryOpen && <PurchaseSummary purchase={purchase} units={units} onHistory={() => { setSummaryOpen(false); setHistoryOpen(true); }} onReceive={() => { setSummaryOpen(false); setReceiveOpen(true); }} onSchedule={() => { setSummaryOpen(false); setScheduleOpen(true); }} onCancel={() => { setSummaryOpen(false); setCancelOpen(true); }} />}
          </Sheet>
        </>
      ) : (
        <ReceiptView purchase={purchase} onReceive={() => setReceiveOpen(true)} onCancel={() => setCancelOpen(true)} cajaOpen={cajaOpen} />
      )}

      <SupplierPicker open={supplierPickerOpen} onClose={() => setSupplierPickerOpen(false)} onSelect={onSupplierChosen} />
      <HistorySheet open={historyOpen} onClose={() => setHistoryOpen(false)} onOpenPurchase={row => void openPurchaseById(row.id)} />
      <PurchaseAgendaSheet open={agendaOpen} close={() => setAgendaOpen(false)} openPurchase={id => void openFromAgenda(id)} />
      {scheduleOpen && purchase?.status === "DRAFT" && <ScheduleSheet purchase={purchase} busy={busy} close={() => setScheduleOpen(false)} confirm={scheduleOrder} />}
      {detailLine && <PricingSheet line={detailLine} policy={policy} onClose={() => setDetailId("")} notify={text => setNotice({ tone: "info", text })}
        onChange={(id, patch) => changeLine(id, { quantity: patch.quantity, unit_cost: patch.purchase_cost })} />}
      {receiveOpen && purchase && (
        <ReceiveSheet key={purchase.id} purchase={purchase} channels={channels} cajaOpen={cajaOpen} busy={busy} close={() => setReceiveOpen(false)}
          confirm={async (channelId, reference, notesValue) => { setBusy(true); try { await receive(channelId, reference, notesValue); } finally { setBusy(false); } }}
          confirmExistingPayment={async (channelId, reference, notesValue) => { setBusy(true); try { await payExisting(channelId, reference, notesValue); } finally { setBusy(false); } }}
          confirmScheduled={receiveScheduled} />
      )}
      <ConfirmDialog open={cancelOpen} module="compras" tone="danger" title="Cancelar esta compra" confirmLabel="Cancelar compra" cancelLabel="Volver" busy={busy} onCancel={() => setCancelOpen(false)} onConfirm={() => void cancelDraft()}>
        La compra pendiente con {purchase?.supplier_name} quedará cancelada. No se afecta inventario, costos ni caja; queda en el historial.
      </ConfirmDialog>
    </main>
  );
}

function PurchaseSummary({ purchase, units, onHistory, onReceive, onSchedule, onCancel }: { purchase: Purchase; units: number; onHistory: () => void; onReceive: () => void; onSchedule: () => void; onCancel: () => void }) {
  return (
    <>
      <div className="pay__top"><Display label="Total compra" value={money.format(Number(purchase.total))} caption={`${purchase.items.length} ${purchase.items.length === 1 ? "referencia" : "referencias"} · ${units} unidades`} tickKey={purchase.total} live /></div>
      <div className="pay__scroll">
        <TouchButton variant="secondary" icon={History} block onClick={onHistory}>Historial de compras</TouchButton>
      </div>
      <div className="pay__cta">
        <TouchButton size="cta" module="compras" block disabled={!purchase.items.length} onClick={onReceive}>RECIBIR COMPRA</TouchButton>
        <TouchButton variant="secondary" module="compras" block disabled={!purchase.id || !purchase.items.length} onClick={onSchedule}>Programar entrega</TouchButton>
        <TouchButton variant="ghost" className="sale-del" block onClick={onCancel}>Cancelar compra</TouchButton>
      </div>
    </>
  );
}

function SaveIndicator({ state, savedAt }: { state: SaveState; savedAt: Date | null }) {
  if (state === "saving") return <span className="purchase__save purchase__save--saving">Guardando…</span>;
  if (state === "error") return <span className="purchase__save purchase__save--error">Error al guardar</span>;
  if (state === "saved" && savedAt) return <span className="purchase__save">Guardado {savedAt.toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit" })}</span>;
  return null;
}

function ReceiptView({ purchase, onReceive, onCancel, cajaOpen }: { purchase: Purchase; onReceive: () => void; onCancel: () => void; cajaOpen: boolean }) {
  const scheduled = purchase.status === "SCHEDULED";
  const compared = purchase.status === "RECEIVED" && purchase.items.some(item => item.ordered_quantity !== null);
  return (
    <div className="purchase__receipt">
      <dl className="purchase__receipt-meta">
        {purchase.invoice_number && <div><dt>Factura / referencia</dt><dd>{purchase.invoice_number}</dd></div>}
        <div><dt>Fecha de compra</dt><dd>{dateTime.format(new Date(purchase.purchased_at))}</dd></div>
        <div><dt>Fecha de recepción</dt><dd>{purchase.received_at ? dateTime.format(new Date(purchase.received_at)) : "Pendiente"}</dd></div>
        <div><dt>Pago</dt><dd>{purchase.payment ? `Pagada · ${purchase.payment.channel_name}` : purchase.status === "RECEIVED" ? "Pendiente de pago" : purchase.status === "CANCELLED" ? "Compra cancelada" : "Pendiente de recepción"}</dd></div>
        {purchase.expected_delivery_date && <div><dt>Entrega prevista</dt><dd>{purchase.expected_delivery_date}</dd></div>}
        {purchase.payment_terms && <div><dt>Condición de pago</dt><dd>{paymentTermsLabel[purchase.payment_terms]}</dd></div>}
        {purchase.payment_due_date && <div><dt>Vencimiento pactado</dt><dd>{purchase.payment_due_date}</dd></div>}
        {purchase.status === "RECEIVED" && purchase.outstanding_amount !== null && <div><dt>Obligación real pendiente</dt><dd>{money.format(Number(purchase.outstanding_amount))}</dd></div>}
      </dl>
      <div className="sale__cart">
        <div className="ui-table-wrap" data-module="compras">
          <table className="ui-table purchase-receipt-table">
            <caption className="ui-sr-only">{scheduled ? "Productos pedidos" : "Productos recibidos"}</caption>
            <thead><tr><th className="ui-th">Producto</th>{compared && <><th className="ui-th ui-th--num">Pedido</th><th className="ui-th ui-th--num">Costo pedido</th></>}<th className="ui-th ui-th--num">{purchase.status === "RECEIVED" ? "Cantidad recibida" : "Cantidad registrada"}</th><th className="ui-th ui-th--num">{purchase.status === "RECEIVED" ? "Costo real" : "Costo estimado"}</th><th className="ui-th ui-th--num">Subtotal</th></tr></thead>
            <tbody>{purchase.items.map(item => <tr key={item.id} className="ui-tr"><td className="ui-td">{item.product_name}<small className="ui-td--muted">{item.barcode ?? "Sin código"}{item.offer_list_unit_cost !== null ? ` · Lista ${money.format(Number(item.offer_list_unit_cost))}, descuento ${money.format(Number(item.offer_discount_per_unit))}` : ""}{item.offer_note ? ` · Oferta: ${item.offer_note}` : ""}{item.difference_reason ? ` · Diferencia: ${item.difference_reason}` : ""}</small></td>{compared && <><td className="ui-td ui-td--num ui-num">{item.ordered_quantity ?? "—"}</td><td className="ui-td ui-td--num ui-num">{item.ordered_unit_cost === null ? "—" : money.format(Number(item.ordered_unit_cost))}</td></>}<td className="ui-td ui-td--num ui-num">{Number(item.quantity)}{item.quantity_difference !== null && !scheduled ? <small className="ui-td--muted">Δ {item.quantity_difference}</small> : null}</td><td className="ui-td ui-td--num ui-num">{!scheduled && compared && item.actual_unit_cost === null ? "No recibido" : money.format(Number(item.unit_cost))}</td><td className="ui-td ui-td--num ui-num"><strong>{money.format(Number(item.line_total))}</strong>{compared && item.amount_difference !== null && <small className="ui-td--muted">Δ {money.format(Number(item.amount_difference))}</small>}</td></tr>)}</tbody>
          </table>
        </div>
      </div>
      {purchase.estimated_total !== null && <p>Pedido estimado: <strong>{money.format(Number(purchase.estimated_total))}</strong></p>}
      <div className="purchase-subtotal"><span>{scheduled ? "COMPROMISO ESTIMADO" : purchase.status === "RECEIVED" ? "TOTAL REAL DE COMPRA" : "TOTAL REGISTRADO"}</span><strong className="ui-num">{money.format(Number(purchase.total))}</strong></div>
      {purchase.notes && <p className="purchase-detail-code">{purchase.notes}</p>}
      {purchase.offer_notes && <p className="purchase-detail-code">Oferta: {purchase.offer_notes}</p>}
      {scheduled && <div className="ui-form__actions"><TouchButton module="compras" onClick={onReceive}>Recibir pedido</TouchButton><TouchButton variant="secondary" onClick={onCancel}>Cancelar pedido</TouchButton></div>}
      {scheduled && !cajaOpen && <InlineMessage tone="warning" actions={<Link href="/cash" data-module="caja" className="ui-btn ui-btn--secondary ui-btn--md">Abrir caja</Link>}>Para confirmar la recepción debes abrir la jornada.</InlineMessage>}
      {purchase.status === "RECEIVED" && !purchase.payment && (
        <div className="ui-form__actions"><TouchButton module="compras" disabled={!cajaOpen} onClick={onReceive}>Registrar pago</TouchButton></div>
      )}
      {!cajaOpen && purchase.status === "RECEIVED" && !purchase.payment && (
        <InlineMessage tone="warning" actions={<Link href="/cash" data-module="caja" className="ui-btn ui-btn--secondary ui-btn--md">Abrir caja</Link>}>La caja está cerrada.</InlineMessage>
      )}
    </div>
  );
}
