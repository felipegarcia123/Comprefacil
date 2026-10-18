"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Ban, FileText, Home, Landmark, LoaderCircle, PenLine, Plus, User } from "lucide-react";
import { api } from "@/lib/api";
import { MockHardwareService } from "@/lib/hardware";
import { errorText, money } from "@/lib/format";
import { onActivateSale, publishSalesContexts, saleLabel } from "@/lib/workContexts";
import { useBreakpoint } from "@/lib/useBreakpoint";
import type { BusinessAccount, CardType, Customer, ElectronicInvoiceIntent, Item, OperationalCategory, PaymentChannel, Product, ProductCatalog, Sale, Supplier } from "@/lib/types";
import { useOperationalSession } from "@/components/OperationalSessionProvider";
import { ConfirmDialog, Display, InlineMessage, Sheet, Toldo, TouchButton, TouchTabs, type TouchTabItem } from "@/components/ui";
import { CustomerPickerSheet } from "@/components/customers/CustomerPickerSheet";
import { CartTable, EmptyCart } from "./_components/CartTable";
import { ElectronicInvoicePanel } from "./_components/ElectronicInvoicePanel";
import { SaleInvoiceStatus } from "./_components/SaleInvoiceStatus";
import { ManualItemSheet } from "./_components/ManualItemSheet";
import { PaymentPanel, type PayMode } from "./_components/PaymentPanel";
import { ScanBar } from "./_components/ScanBar";
import { SplitPaymentSheet } from "./_components/SplitPaymentSheet";
import { ProductFormSheet } from "../inventory/_components/ProductFormSheet";

const hardware = new MockHardwareService();

/** Per-sale UI choices. The sale's customer lives on the backend Sale (survives navigation like the cart).
 * `electronicInvoice` = the "Factura electrónica" checkbox (OFF by default); `recipient` = who it goes to
 * while ON — the sale's customer or Consumidor final. Choosing a customer never triggers an invoice. */
type Draft = { channelId?: string; received?: string; mode?: PayMode; electronicInvoice?: boolean; recipient?: "FINAL_CONSUMER" | "CUSTOMER"; cardType?: CardType; accountId?: string };
type Notice = { tone: "success" | "info" | "warning" | "danger"; text: string; change?: number; invoiceSaleId?: string };
type ConsumptionItem = Item & { unit_cost: string; reference_sale_price: string };
type ConsumptionCart = { id: string; idempotencyKey: string; items: ConsumptionItem[] };

// Per-sale UI state (active tab, chosen channel, cash received) survives leaving /sales and coming back.
const STORE_KEY = "pos.sales.ui.v1";
type Stored = { activeId?: string; drafts?: Record<string, Draft>; lastSaleId?: string; consumption?: ConsumptionCart };
function readStored(): Stored { try { return JSON.parse(sessionStorage.getItem(STORE_KEY) || "{}"); } catch { return {}; } }
function writeStored(value: Stored) { try { sessionStorage.setItem(STORE_KEY, JSON.stringify(value)); } catch { /* storage may be unavailable */ } }

/** Same arithmetic as the backend (line = qty × unit price, total = sum), applied locally so taps feel instant. */
function optimistic(sale: Sale, itemId: string, quantity: number): Sale {
  const items = quantity <= 0
    ? sale.items.filter(item => item.id !== itemId)
    : sale.items.map(item => item.id === itemId ? { ...item, quantity: String(quantity), line_total: String(quantity * Number(item.unit_price)) } : item);
  return { ...sale, items, total: String(items.reduce((sum, item) => sum + Number(item.line_total), 0)) };
}

export default function SalesPage() {
  const { summary: cash, loading: cashLoading, error: cashError, refresh } = useOperationalSession();
  const breakpoint = useBreakpoint();
  const wide = breakpoint === "pos" || breakpoint === "laptop";

  const [phase, setPhase] = useState<"loading" | "ready" | "error">("loading");
  const [loadError, setLoadError] = useState("");
  const [sales, setSales] = useState<Sale[]>([]);
  const [consumption, setConsumption] = useState<ConsumptionCart | null>(null);
  const [channels, setChannels] = useState<PaymentChannel[]>([]);
  const [accounts, setAccounts] = useState<BusinessAccount[]>([]);
  const [activeId, setActiveId] = useState("");
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [selectedId, setSelectedId] = useState("");
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);
  const [invoiceConfigured, setInvoiceConfigured] = useState(false);
  const [splitOpen, setSplitOpen] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);
  const [productOpen, setProductOpen] = useState(false);
  const [productCreated, setProductCreated] = useState<ProductCatalog | null>(null);
  const [productCategories, setProductCategories] = useState<OperationalCategory[]>([]);
  const [productSuppliers, setProductSuppliers] = useState<Supplier[]>([]);
  const [payOpen, setPayOpen] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [consumptionConfirmOpen, setConsumptionConfirmOpen] = useState(false);
  const [invoiceOpen, setInvoiceOpen] = useState(false);
  const [lastSaleId, setLastSaleId] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [customerBusy, setCustomerBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!productOpen) return;
    void Promise.all([api.operationalCategories(), api.suppliers("", true)]).then(([categories, suppliers]) => {
      setProductCategories(categories); setProductSuppliers(suppliers);
    }).catch(error => setNotice({ tone: "danger", text: errorText(error) }));
  }, [productOpen]);

  const active = sales.find(sale => sale.id === activeId);
  const consumptionActive = consumption?.id === activeId;
  const cartItems = consumptionActive ? consumption!.items : active?.items ?? [];
  const activeRef = useRef<Sale | undefined>(undefined);
  const chargingRef = useRef(false);     // a second tap before React re-renders the disabled COBRAR
  const salesRef = useRef<Sale[]>([]);
  useEffect(() => { activeRef.current = active; salesRef.current = sales; });

  const total = consumptionActive
    ? cartItems.reduce((sum, item) => sum + Number(item.line_total), 0)
    : Number(active?.total ?? 0);
  const itemCount = cartItems.length;
  const unitCount = cartItems.reduce((sum, item) => sum + Number(item.quantity), 0);
  const draft = drafts[activeId] ?? {};
  // First decision: Efectivo vs the "Transferencia" umbrella = every active non-cash channel from the backend.
  const cashChannel = channels.find(item => item.affects_cash);
  const electronicChannels = useMemo(() => channels.filter(item => !item.affects_cash), [channels]);
  const storedChannel = channels.find(item => item.id === draft.channelId);
  const mode: PayMode = draft.mode ?? (storedChannel && !storedChannel.affects_cash ? "electronic" : cashChannel ? "cash" : "electronic");
  const channel = mode === "cash" ? cashChannel
    : electronicChannels.find(item => item.id === draft.channelId) ?? (electronicChannels.length === 1 ? electronicChannels[0] : undefined);
  const channelId = channel?.id ?? "";
  const received = draft.received ?? "";
  const cajaOpen = Boolean(cash);
  const cajaClosed = !cashLoading && !cashError && !cash;
  const customer = active?.customer ?? null;
  const electronicInvoice = invoiceConfigured && Boolean(draft.electronicInvoice);
  // ON: the sale's customer unless the cashier chose Consumidor final; no customer = Consumidor final.
  const recipient = electronicInvoice && draft.recipient !== "FINAL_CONSUMER" ? customer : null;
  // Datáfono: Débito/Crédito is required (it decides the invoice's DIAN payment method).
  const cardType = channel?.payment_type === "CARD" ? draft.cardType : undefined;
  // 0028: WHERE the electronic money landed. The channel's default is only a preselection; never for cash.
  const accountId = channel && !channel.affects_cash ? (draft.accountId ?? channel.default_account_id ?? "") : "";
  const accountReady = !channel?.requires_destination_account || Boolean(accountId);
  // Comida a granel: "Báscula / efectivo" (default) = part of the customer's bill only — the POS collects and
  // records `posTotal`, never the scale part; "Pago electrónico" = an ordinary POS line, never in POS cash.
  const granelTotal = (active?.items ?? []).reduce((sum, item) => sum + (item.manual_kind === "GRANEL" ? Number(item.line_total) : 0), 0);
  const granelSettlement = active?.granel_settlement ?? "EXTERNAL_SCALE";
  const scaleTotal = granelSettlement === "EXTERNAL_SCALE" ? granelTotal : 0;
  const posTotal = total - scaleTotal;
  const scaleOnly = scaleTotal > 0 && posTotal <= 0;
  const granelInCash = granelSettlement === "POS" && Boolean(channel?.affects_cash) && granelTotal > 0;
  const canCharge = Boolean(itemCount && channel && (!channel.affects_cash || Number(received) >= total))
    && (channel?.payment_type !== "CARD" || Boolean(cardType)) && accountReady
    && !granelInCash && !scaleOnly && !busy && !customerBusy;

  // ---- load ---------------------------------------------------------------------------------------------
  const load = useCallback(async () => {
    setPhase("loading"); setLoadError("");
    const [channelResult, openResult, invoiceResult, accountResult] = await Promise.allSettled([api.paymentChannels(true), api.openSales(), api.electronicInvoiceConfigStatus(), api.businessAccounts(true)]);
    if (channelResult.status === "fulfilled") setChannels(channelResult.value);
    if (accountResult.status === "fulfilled") setAccounts(accountResult.value);
    setInvoiceConfigured(invoiceResult.status === "fulfilled" && Boolean(invoiceResult.value.configured));
    if (openResult.status === "rejected") { setLoadError(errorText(openResult.reason)); setPhase("error"); return; }
    let list = openResult.value;
    if (!list.length) { try { list = [await api.createSale()]; } catch { /* closed jornada: the closed state explains it */ } }
    const stored = readStored();
    setSales(list);
    // Only keep UI drafts of sales that are still open (completed/cancelled ones would just accumulate).
    setDrafts(Object.fromEntries(Object.entries(stored.drafts ?? {}).filter(([id]) => list.some(sale => sale.id === id))));
    setLastSaleId(stored.lastSaleId ?? ""); setConsumption(stored.consumption ?? null);
    setActiveId(stored.consumption?.id === stored.activeId || list.some(sale => sale.id === stored.activeId) ? stored.activeId! : list[0]?.id ?? "");
    setPhase("ready");
  }, []);
  useEffect(() => { void load(); }, [load]);

  // The jornada opened while there were no sales: start the first one.
  const creating = useRef(false);
  useEffect(() => {
    if (phase !== "ready" || !cash || sales.length || creating.current) return;
    creating.current = true;
    api.createSale().then(sale => { setSales([sale]); setActiveId(sale.id); }).catch(error => setNotice({ tone: "danger", text: errorText(error) })).finally(() => { creating.current = false; });
  }, [phase, cash, sales.length]);

  useEffect(() => {
    if (phase === "ready") publishSalesContexts(sales.map((sale, index) => ({ id: sale.id, label: saleLabel(index), sub: money.format(Number(sale.total)), module: "ventas" })), activeId);
  }, [phase, sales, activeId]);
  // A ContextBar chip for a sale can request activating it while this screen is already mounted (no navigation happens in that case).
  useEffect(() => onActivateSale(id => { if (sales.some(sale => sale.id === id)) { setActiveId(id); setSelectedId(""); } }), [sales]);
  useEffect(() => { if (phase === "ready") writeStored({ activeId, drafts, lastSaleId, consumption: consumption ?? undefined }); }, [phase, activeId, drafts, lastSaleId, consumption]);
  useEffect(() => {
    if (!notice || notice.tone === "success") return;
    const timer = window.setTimeout(() => setNotice(null), 7000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  // Keep the scanner ready: whenever no dialog is open and nothing else is being edited.
  const refocus = useCallback(() => {
    window.setTimeout(() => { // timer, not rAF: rAF is paused in background tabs and kiosk webviews
      const current = document.activeElement;
      if (current instanceof HTMLInputElement && current !== inputRef.current) return;
      if (document.querySelector("dialog[open]")) return;
      inputRef.current?.focus();
    }, 0);
  }, []);
  useEffect(() => { if (phase === "ready" && !busy && !splitOpen && !manualOpen && !payOpen && !cancelOpen && !invoiceOpen && !pickerOpen) refocus(); }, [phase, activeId, busy, splitOpen, manualOpen, payOpen, cancelOpen, invoiceOpen, pickerOpen, refocus]);

  // ---- sale operations: serialised so rapid taps and scans are applied in order --------------------------
  const pending = useRef(0);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  // The API returns lines in its own order; keep the order the cashier already sees and append new lines at the end.
  const updateSale = useCallback((sale: Sale) => setSales(current => current.map(item => {
    if (item.id !== sale.id) return item;
    const rank = new Map(item.items.map((line, index) => [line.id, index]));
    return { ...sale, items: [...sale.items].sort((a, b) => (rank.get(a.id) ?? 1e9) - (rank.get(b.id) ?? 1e9)) };
  })), []);
  const run = useCallback(async <T,>(job: () => Promise<T>, apply: (result: T) => void): Promise<T> => {
    pending.current++;
    const task = queue.current.catch(() => undefined).then(job);
    queue.current = task;
    try { const result = await task; pending.current--; if (pending.current === 0) apply(result); return result; }
    catch (error) { pending.current--; throw error; }
  }, []);
  const resync = useCallback(async () => { try { const list = await api.openSales(); if (list.length) setSales(list); } catch { /* keep local state */ } }, []);

  // Scanner -> barcode; manual pick from the search -> product_id. Same backend logic, same UI handling.
  const addLine = useCallback(async (ref: { barcode: string } | { productId: string; label: string }): Promise<boolean> => {
    const sale = activeRef.current; if (!sale) return false;
    setNotice(null);
    const before = new Map(sale.items.map(item => [item.id, Number(item.quantity)]));
    try {
      await run(() => ("barcode" in ref ? api.addItem(sale.id, ref.barcode) : api.addProduct(sale.id, ref.productId)), updated => {
        updateSale(updated);
        const touched = updated.items.find(item => before.get(item.id) !== Number(item.quantity));
        if (touched) setSelectedId(touched.id);
      });
      return true;
    } catch (error) {
      setNotice({ tone: "danger", text: `${errorText(error)} (${"barcode" in ref ? `código ${ref.barcode}` : ref.label})` });
      return false;
    } finally { refocus(); }
  }, [run, updateSale, refocus]);
  const scan = useCallback((barcode: string) => consumptionActive ? addConsumptionBarcode(barcode) : addLine({ barcode }), [consumptionActive, addLine]);
  const pickProduct = useCallback((product: Product) => consumptionActive
    ? Promise.resolve(addConsumptionProduct(product)) : addLine({ productId: product.id, label: product.name }), [consumptionActive, addLine]);

  const changeQuantity = useCallback(async (itemId: string, quantity: number) => {
    if (consumptionActive) {
      setSelectedId(quantity <= 0 ? "" : itemId);
      setConsumption(current => current ? { ...current, items: quantity <= 0
        ? current.items.filter(item => item.id !== itemId)
        : current.items.map(item => item.id === itemId
          ? { ...item, quantity: String(quantity), line_total: String(quantity * Number(item.unit_cost)), unit_price: item.unit_cost }
          : item) } : current);
      return;
    }
    const sale = activeRef.current; if (!sale) return;
    setSelectedId(quantity <= 0 ? "" : itemId);
    setSales(current => current.map(item => item.id === sale.id ? optimistic(item, itemId, quantity) : item));
    try { await run(() => quantity <= 0 ? api.remove(sale.id, itemId) : api.quantity(sale.id, itemId, quantity), updateSale); }
    catch (error) { setNotice({ tone: "danger", text: errorText(error) }); await resync(); }
    finally { refocus(); }
  }, [consumptionActive, run, updateSale, resync, refocus]);

  async function newSale() {
    try { const sale = await api.createSale(); setSales(current => [...current, sale]); setActiveId(sale.id); setNotice(null); }
    catch (error) { setNotice({ tone: "danger", text: errorText(error) }); }
  }

  function openConsumption() {
    if (consumption) { setActiveId(consumption.id); setSelectedId(""); return; }
    const id = `consumption-${crypto.randomUUID()}`;
    setConsumption({ id, idempotencyKey: crypto.randomUUID(), items: [] });
    setActiveId(id); setSelectedId(""); setNotice(null);
  }

  function discardConsumption() {
    if (!consumption || busy) return;
    const next = salesRef.current[0]?.id ?? "";
    setConsumption(null); setActiveId(next); setSelectedId(""); setConsumptionConfirmOpen(false);
  }

  function addConsumptionProduct(product: Product): boolean {
    if (!consumptionActive) return false;
    setConsumption(current => {
      if (!current) return current;
      const found = current.items.find(item => item.product_id === product.id);
      const items = found
        ? current.items.map(item => item.product_id === product.id
          ? { ...item, quantity: String(Number(item.quantity) + 1), line_total: String((Number(item.quantity) + 1) * Number(item.unit_cost)), unit_price: product.unit_cost }
          : item)
        : [...current.items, { id: product.id, product_id: product.id, product_name: product.name,
          quantity: "1", unit_price: product.unit_cost, unit_cost: product.unit_cost,
          reference_sale_price: product.sale_price, line_total: product.unit_cost, manual_kind: null }];
      return { ...current, items };
    });
    return true;
  }

  async function addConsumptionBarcode(barcode: string): Promise<boolean> {
    try {
      const found = await api.search(barcode);
      const product = found.find(item => item.barcode === barcode) ?? (found.length === 1 ? found[0] : undefined);
      if (!product) { setNotice({ tone: "danger", text: `No se encontró un producto para el código ${barcode}` }); return false; }
      return addConsumptionProduct(product);
    } catch (error) { setNotice({ tone: "danger", text: errorText(error) }); return false; }
  }

  async function registerConsumption() {
    if (!consumption || !consumption.items.length || busy) return;
    setBusy(true); setNotice(null);
    try {
      await api.createInternalConsumptionBatch({
        items: consumption.items.map(item => ({ product_id: item.product_id!, quantity: Number(item.quantity) })),
        idempotency_key: consumption.idempotencyKey,
      });
      const next = salesRef.current[0]?.id ?? "";
      setConsumption(null); setActiveId(next); setSelectedId(""); setConsumptionConfirmOpen(false);
      setNotice({ tone: "success", text: "Consumo interno registrado. El inventario fue actualizado." });
    } catch (error) {
      // Keep the cart and its idempotency key: an uncertain response can be retried safely.
      setNotice({ tone: "danger", text: errorText(error) });
    } finally { setBusy(false); }
  }

  useEffect(() => {
    if (phase !== "ready" || consumption || typeof window === "undefined") return;
    if (new URLSearchParams(window.location.search).get("consumption") === "1") openConsumption();
  // The URL action is intentionally consumed once; the created cart makes subsequent renders no-ops.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, consumption]);

  /** Cancel = close this OPEN sale without charging (status CANCELLED in the backend; lines are kept there, nothing else moves). */
  async function cancelCurrentSale() {
    const sale = activeRef.current; if (!sale) return;
    setBusy(true);
    try {
      await run(() => api.cancelSale(sale.id), () => undefined); // queued behind any pending quantity edits of the same sale
      const before = salesRef.current;
      const index = before.findIndex(item => item.id === sale.id);
      let list = before.filter(item => item.id !== sale.id);
      if (!list.length) { try { list = [await api.createSale()]; } catch { list = []; } } // same "always one open sale" flow as after a checkout
      setSales(list); setActiveId(list[Math.min(index, list.length - 1)]?.id ?? ""); setSelectedId("");
      setDrafts(current => { const { [sale.id]: _removed, ...others } = current; return others; });
      setNotice({ tone: "info", text: "Venta cancelada. No se cobró nada ni se movió el inventario." });
    } catch (error) { setNotice({ tone: "danger", text: errorText(error) }); await resync(); }
    finally { setBusy(false); setCancelOpen(false); }
  }

  // ---- payment (rules unchanged) ------------------------------------------------------------------------
  async function finishCheckout(action: () => Promise<Sale>, hasCash: boolean, invoiced: boolean) {
    if (chargingRef.current) return;
    chargingRef.current = true;
    setBusy(true); setNotice(null);
    try {
      const done = await action();
      await hardware.printReceipt(done);
      if (hasCash) await hardware.openCashDrawer();
      const rest = salesRef.current.filter(sale => sale.id !== activeId);
      const next = rest[0] ?? await api.createSale();
      setSales(rest.length ? rest : [next]); setActiveId(next.id); setSelectedId("");
      setDrafts(current => { const { [activeId]: _removed, ...others } = current; return others; });
      setSplitOpen(false); setPayOpen(false); setLastSaleId(done.id);
      const change = done.payments.reduce((sum, payment) => sum + Number(payment.change_amount ?? 0), 0);
      setNotice({ tone: "success", text: `Venta ${done.number} completada`, change, invoiceSaleId: invoiced ? done.id : undefined });
      if (hasCash) void refresh();
    } catch (error) { setNotice({ tone: "danger", text: errorText(error) }); }
    finally { setBusy(false); chargingRef.current = false; }
  }

  /** What COBRAR sends: the checkbox and its recipient. Absent = no electronic invoice. */
  function invoiceIntent(): ElectronicInvoiceIntent | undefined {
    if (!electronicInvoice) return undefined;
    return recipient ? { recipient: "CUSTOMER", customer_id: recipient.id } : { recipient: "FINAL_CONSUMER" };
  }

  async function charge() {
    if (!active || !channel) return;
    const intent = invoiceIntent();
    await finishCheckout(() => api.checkout(active.id, channel.id, channel.affects_cash ? Number(received) : undefined, cardType, intent, accountId || undefined), channel.affects_cash, Boolean(intent));
  }

  async function chargeMixed(allocations: { channel_id: string; amount: number; received_amount?: number; card_type?: CardType; destination_account_id?: string }[]) {
    if (!active) return;
    const hasCash = allocations.some(allocation => channels.find(item => item.id === allocation.channel_id)?.affects_cash);
    const intent = invoiceIntent();
    await finishCheckout(() => api.checkoutMixed(active.id, allocations, intent), Boolean(hasCash), Boolean(intent));
  }

  const setDraft = (patch: Draft) => setDrafts(current => ({ ...current, [activeId]: { ...current[activeId], ...patch } }));

  // ---- customer: stored on the backend OPEN sale (PUT /sales/{id}/customer). Never triggers an invoice. ----
  async function assignCustomer(customerId: string | null, note?: string) {
    const sale = activeRef.current; if (!sale) return;
    setCustomerBusy(true);
    try {
      await run(() => api.setSaleCustomer(sale.id, customerId), updateSale);
      setPickerOpen(false);
      if (note) setNotice({ tone: "info", text: note });
    } catch (error) { setNotice({ tone: "danger", text: errorText(error) }); }
    finally { setCustomerBusy(false); }
  }
  async function changeGranelSettlement(settlement: "EXTERNAL_SCALE" | "POS") {
    const sale = activeRef.current; if (!sale || sale.granel_settlement === settlement) return;
    try { await run(() => api.setGranelSettlement(sale.id, settlement), updateSale); }
    catch (error) { setNotice({ tone: "danger", text: errorText(error) }); await resync(); }
  }

  // "Factura electrónica": ON preselects the sale's customer if it has one, else Consumidor final (no typing).
  // OFF never clears the sale's customer: there is simply no fiscal recipient.
  function toggleElectronicInvoice(value: boolean) {
    setDraft({ electronicInvoice: value, recipient: customer ? "CUSTOMER" : "FINAL_CONSUMER" });
  }
  function billFinalConsumer() {
    setPickerOpen(false); setDraft({ recipient: "FINAL_CONSUMER" });
  }

  // ---- render -------------------------------------------------------------------------------------------
  const tabs = useMemo<TouchTabItem[]>(() => [
    ...sales.map((sale, index) => ({ id: sale.id, label: `Venta ${index + 1}`, sub: money.format(Number(sale.total)), module: "ventas" as const })),
    ...(consumption ? [{ id: consumption.id, label: "Casa / consumo interno", sub: money.format(consumption.items.reduce((sum, item) => sum + Number(item.line_total), 0)), module: "caja" as const }] : []),
  ], [sales, consumption]);

  if (phase === "loading") {
    return <main className="sale sale-status-page" data-module="ventas"><Toldo module="ventas" /><div className="sale-status" role="status"><LoaderCircle className="ui-btn__spin" size={32} aria-hidden="true" />Cargando ventas…</div></main>;
  }
  if (phase === "error") {
    return (
      <main className="sale sale-status-page" data-module="ventas"><Toldo module="ventas" />
        <div className="sale-status">
          <InlineMessage tone="danger" title="No se pudieron cargar las ventas" actions={<TouchButton module="ventas" onClick={() => void load()}>Reintentar</TouchButton>}>{loadError}</InlineMessage>
        </div>
      </main>
    );
  }
  if (!sales.length) {
    return (
      <main className="sale sale-status-page" data-module="ventas"><Toldo module="ventas" />
        <div className="sale-status">
          {cajaClosed ? (
            <div className="sale-closed">
              <Landmark size={48} aria-hidden="true" />
              <h1>La caja está cerrada</h1>
              <p>Abre la caja para empezar a vender. Tus productos y precios siguen disponibles.</p>
              <Link href="/cash" data-module="caja" className="ui-btn ui-btn--primary ui-btn--xl">Abrir caja</Link>
            </div>
          ) : (<><LoaderCircle className="ui-btn__spin" size={32} aria-hidden="true" />Preparando la primera venta…</>)}
        </div>
      </main>
    );
  }

  const panel = (variant: "dock" | "sheet") => (
    <PaymentPanel
      variant={variant} total={total} itemCount={itemCount} unitCount={unitCount}
      mode={mode} onMode={next => setDraft({ mode: next, channelId: next === "cash" ? cashChannel?.id : undefined, received: "" })}
      hasCashChannel={Boolean(cashChannel)} electronicChannels={electronicChannels}
      channelId={channelId} onChannel={id => setDraft({ mode: "electronic", channelId: id, received: "", cardType: undefined, accountId: undefined })}
      cardType={cardType} onCardType={value => setDraft({ cardType: value })}
      accounts={accounts} accountId={accountId} onAccount={id => setDraft({ accountId: id })}
      received={received} onReceived={value => setDraft({ received: value })}
      invoiceEnabled={invoiceConfigured} electronicInvoice={electronicInvoice} onElectronicInvoice={toggleElectronicInvoice}
      recipient={recipient} onPickCustomer={() => setPickerOpen(true)} customerBusy={customerBusy}
      cajaOpen={cajaOpen} busy={busy} canCharge={canCharge} hasItems={itemCount > 0} granelInCash={granelInCash}
      granelTotal={granelTotal} granelSettlement={granelSettlement} onGranelSettlement={value => void changeGranelSettlement(value)} posTotal={posTotal} scaleOnly={scaleOnly}
      onCharge={() => void charge()} onSplit={() => setSplitOpen(true)}
    />
  );

  return (
    <main className="sale" data-module="ventas">
      <Toldo module="ventas" />
      <div className="sale__tabs">
        <TouchTabs
          ariaLabel="Ventas abiertas" items={tabs} value={activeId}
          onChange={id => { setActiveId(id); setSelectedId(""); }}
          trailing={<div className="sale-tab-actions">
            <button type="button" className="ui-tab ui-tab--action sale-new" onClick={() => void newSale()} disabled={busy}><span className="ui-tab__body"><Plus size={24} aria-hidden="true" />Nueva</span></button>
            <button type="button" className="ui-tab ui-tab--action sale-consumption" onClick={openConsumption} disabled={busy}><span className="ui-tab__body"><Home size={21} aria-hidden="true" />Casa / consumo interno</span></button>
          </div>}
        />
      </div>

      <div className="sale__body">
        <section className="sale__main" aria-label={consumptionActive ? "Consumo interno activo" : "Venta activa"}>
          <ScanBar disabled={busy} inputRef={inputRef} onScan={scan} onPick={pickProduct} onNewProduct={() => { setProductCreated(null); setProductOpen(true); }} notify={text => setNotice({ tone: "warning", text })} />
          {productCreated && !consumptionActive && (
            <InlineMessage tone="success" title={`Producto creado: ${productCreated.name}`} onDismiss={() => setProductCreated(null)} actions={<TouchButton variant="secondary" onClick={() => { void pickProduct({ id: productCreated.id, barcode: productCreated.barcode, name: productCreated.name, sale_price: String(productCreated.sale_price), unit_cost: String(productCreated.purchase_cost), stock: String(productCreated.current_stock) }); setProductCreated(null); }}>Agregar al carrito</TouchButton>}>
              El producto quedó disponible en la búsqueda. Puedes agregarlo a esta venta.
            </InlineMessage>
          )}
          {cajaClosed && (
            <InlineMessage tone="warning" title="La caja está cerrada" actions={<Link href="/cash" data-module="caja" className="ui-btn ui-btn--secondary ui-btn--md">Abrir caja</Link>}>
              Puedes preparar ventas, pero no registrar cobros ni productos hasta abrir la jornada.
            </InlineMessage>
          )}
          {notice && (
            <InlineMessage
              tone={notice.tone}
              onDismiss={() => setNotice(null)}
              actions={notice.invoiceSaleId && invoiceConfigured ? <TouchButton variant="secondary" icon={FileText} onClick={() => setInvoiceOpen(true)}>Factura electrónica</TouchButton> : undefined}
            >
              <span className="sale-notice">
                {notice.text}{notice.change ? <strong className="sale-change ui-num">Cambio {money.format(notice.change)}</strong> : null}
                {notice.invoiceSaleId && <SaleInvoiceStatus saleId={notice.invoiceSaleId} />}
              </span>
            </InlineMessage>
          )}
          {consumptionActive && <InlineMessage tone="info" title="Consumo interno">
            Se descuenta inventario al confirmar. No genera venta, pago, ingreso ni movimiento de caja.
          </InlineMessage>}
          <div className="sale__cart">
            {itemCount ? <CartTable items={cartItems} selectedId={selectedId} onSelect={setSelectedId} onChange={changeQuantity} priceLabel={consumptionActive ? "Costo" : undefined} /> : <EmptyCart />}
          </div>
          <div className="sale-foot">
            {consumptionActive ? <>
              <span className="sale-client"><Home size={20} aria-hidden="true" />Costo contable {money.format(total)}</span>
              <TouchButton variant="ghost" icon={Ban} className="sale-del" disabled={busy} onClick={discardConsumption}>Descartar consumo</TouchButton>
            </> : <>
            {/* "Producto manual" adds a cart line (no payment): it lives with the cart, reachable without opening Cobrar. */}
            <TouchButton variant="secondary" icon={PenLine} onClick={() => setManualOpen(true)} disabled={busy || !cajaOpen}>Producto manual</TouchButton>
            <span className="sale-client"><User size={20} aria-hidden="true" />{customer ? customer.name : "Consumidor final"}</span>
            {invoiceConfigured && lastSaleId && <TouchButton variant="ghost" icon={FileText} onClick={() => setInvoiceOpen(true)}>Factura de la última venta</TouchButton>}
            <TouchButton variant="ghost" icon={Ban} className="sale-del" disabled={busy || (sales.length === 1 && !itemCount)} onClick={() => (itemCount ? setCancelOpen(true) : void cancelCurrentSale())}>Cancelar venta</TouchButton>
            </>}
          </div>
        </section>

        {wide && <section className="sale__panel" aria-label={consumptionActive ? "Registro de consumo interno" : "Cobro"}>
          {consumptionActive ? <div className="sale-consumption-panel"><Display size="figure" label="Costo total" value={money.format(total)} caption={`${itemCount} ${itemCount === 1 ? "producto" : "productos"}`} tickKey={total} live />
            {consumption!.items.some(item => Number(item.unit_cost) === 0) && <InlineMessage tone="warning">Hay productos sin costo registrado. Se contabilizarán a $0.</InlineMessage>}
            <TouchButton size="cta" module="caja" disabled={!itemCount || busy} onClick={() => setConsumptionConfirmOpen(true)}>Registrar consumo interno</TouchButton></div> : <div className="sale-consumption-panel"><Display size="figure" label="Total" value={money.format(total)} caption={`${itemCount} ${itemCount === 1 ? "producto" : "productos"}`} tickKey={total} live /><TouchButton size="cta" module="ventas" disabled={!itemCount || busy} onClick={() => setPayOpen(true)}>COBRAR</TouchButton></div>}
        </section>}
      </div>

      {!wide && (
        <div className="sale__dock">
          <Display size="figure" label={consumptionActive ? "Costo total" : "Total"} value={money.format(total)} caption={`${itemCount} ${itemCount === 1 ? "producto" : "productos"}`} tickKey={total} live />
          <TouchButton size="cta" module={consumptionActive ? "caja" : "ventas"} disabled={!itemCount || busy} onClick={() => consumptionActive ? setConsumptionConfirmOpen(true) : setPayOpen(true)}>{consumptionActive ? "Registrar consumo interno" : "COBRAR"}</TouchButton>
        </div>
      )}
      <Sheet open={payOpen} title="Cobrar" persistent wide side={wide} module="ventas" onClose={() => setPayOpen(false)}>{payOpen && !consumptionActive && panel("sheet")}</Sheet>

      <ProductFormSheet
        target={productOpen ? null : undefined}
        categories={productCategories}
        suppliers={productSuppliers}
        onClose={() => setProductOpen(false)}
        onSaved={() => setProductOpen(false)}
        create={async body => { const created = await api.createProduct(body); setProductCreated(created); return created; }}
        update={async () => { throw new Error("La edición de productos se realiza desde Inventario."); }}
      />

      {splitOpen && active && <SplitPaymentSheet total={posTotal} scaleTotal={scaleTotal} channels={channels} accounts={accounts} busy={busy} close={() => setSplitOpen(false)} confirm={chargeMixed} />}
      {manualOpen && (
        <ManualItemSheet
          busy={busy} close={() => setManualOpen(false)}
          confirm={async body => {
            const sale = activeRef.current; if (!sale) return;
            setBusy(true); setNotice(null);
            try { await run(() => api.addManualItem(sale.id, body), updateSale); setManualOpen(false); }
            catch (error) { setNotice({ tone: "danger", text: errorText(error) }); await resync(); }
            finally { setBusy(false); }
          }}
        />
      )}
      <ConfirmDialog open={cancelOpen} module="ventas" tone="danger" title="Cancelar esta venta" confirmLabel="Cancelar venta" cancelLabel="Volver" busy={busy} onCancel={() => setCancelOpen(false)} onConfirm={() => void cancelCurrentSale()}>
        Los productos de esta venta se descartarán. No se cobra ni se mueve el inventario.
      </ConfirmDialog>
      <ConfirmDialog open={consumptionConfirmOpen} module="caja" tone="primary" title="Registrar consumo interno" confirmLabel="Registrar consumo interno" cancelLabel="Volver" busy={busy} onCancel={() => setConsumptionConfirmOpen(false)} onConfirm={() => void registerConsumption()}>
        Se descontarán {unitCount} unidades del inventario por un costo contable de {money.format(total)}. Esta operación no genera una venta ni mueve efectivo.
        {consumption?.items.some(item => Number(item.unit_cost) === 0) ? " Hay productos sin costo registrado y se contabilizarán a $0." : ""}
      </ConfirmDialog>
      <CustomerPickerSheet
        open={pickerOpen} selectedId={customer?.id}
        onSelect={(picked: Customer, note?: string) => { setDraft({ recipient: "CUSTOMER" }); void assignCustomer(picked.id, note); }}
        onClose={() => setPickerOpen(false)} onUseFinalConsumer={billFinalConsumer}
      />
      <Sheet open={invoiceOpen} title="Factura electrónica" onClose={() => setInvoiceOpen(false)} module="ventas">
        {invoiceOpen && lastSaleId && <ElectronicInvoicePanel saleId={lastSaleId} />}
      </Sheet>
    </main>
  );
}
