"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { ClipboardList, LoaderCircle, Package, Plus, ScanLine } from "lucide-react";
import { api } from "@/lib/api";
import { errorText, money } from "@/lib/format";
import type { RoundingPolicy } from "@/lib/pricing";
import { useBreakpoint } from "@/lib/useBreakpoint";
import type { InventoryDetail, InventoryPage, InventoryProduct, InventorySort, OperationalCategory, ProductInput, Supplier } from "@/lib/types";
import { InlineMessage, Pagination, SearchBar, Sheet, Toldo, TouchButton } from "@/components/ui";
import { AdjustmentSheet } from "./_components/AdjustmentSheet";
import { ProductDetail } from "./_components/ProductDetail";
import { ProductFormSheet } from "./_components/ProductFormSheet";
import { ProductPricingSheet } from "./_components/ProductPricingSheet";
import { ProductTable } from "./_components/ProductTable";

type StatusFilter = "active" | "inactive" | "all";
type StockFilter = "all" | "low" | "out";
type Notice = { tone: "success" | "info" | "warning" | "danger"; text: string };
const isBarcodeLike = (value: string) => /^\d{8,14}$/.test(value);
const emptyPage: InventoryPage = { items: [], total: 0, page: 1, page_size: 50, total_pages: 1 };

export default function InventoryPage() {
  const breakpoint = useBreakpoint();
  const wide = breakpoint === "pos"; // >=1200: inline detail panel. Below that, the detail opens as a Sheet.
  const showCategory = wide;
  const compact = breakpoint === "movil"; // <640: stacked rows, fewer columns — a shrunk desktop table doesn't fit a phone.

  const [query, setQuery] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [supplierId, setSupplierId] = useState("");
  const [status, setStatus] = useState<StatusFilter>("active");
  const [stockFilter, setStockFilter] = useState<StockFilter>("all");
  const [sort, setSort] = useState<InventorySort>("name");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [page, setPage] = useState(1);

  const [data, setData] = useState<InventoryPage>(emptyPage);
  const [loading, setLoading] = useState(true);
  const [categories, setCategories] = useState<OperationalCategory[]>([]);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [policy, setPolicy] = useState<RoundingPolicy>("round_up_500");

  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<InventoryDetail | null>(null);
  const [detailOpen, setDetailOpen] = useState(false); // controls the <1200 Sheet only
  const [formTarget, setFormTarget] = useState<InventoryDetail | null | undefined>(undefined);
  const [adjustTarget, setAdjustTarget] = useState<InventoryDetail | null>(null);
  const [pricingTarget, setPricingTarget] = useState<InventoryDetail | null>(null);
  const [togglingActive, setTogglingActive] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await api.inventory({
        q: query, category_id: categoryId || undefined, supplier_id: supplierId || undefined,
        active: status === "all" ? undefined : status === "active",
        low_stock: stockFilter === "low", out_of_stock: stockFilter === "out",
        sort, sort_dir: sortDir, page, page_size: 50,
      });
      setData(result);
    } catch (error) { setNotice({ tone: "danger", text: errorText(error) }); }
    finally { setLoading(false); }
  }, [query, categoryId, supplierId, status, stockFilter, sort, sortDir, page]);

  useEffect(() => { const timer = setTimeout(() => void load(), 250); return () => clearTimeout(timer); }, [load]);
  useEffect(() => { setPage(1); }, [query, categoryId, supplierId, status, stockFilter, sort, sortDir]);
  useEffect(() => {
    void api.operationalCategories().then(setCategories).catch(() => undefined);
    void api.suppliers("", true).then(setSuppliers).catch(() => undefined);
    void api.pricingConfig().then(config => setPolicy(config.commercial_rounding_policy)).catch(() => undefined);
  }, []);

  async function openProduct(product: InventoryProduct) {
    setSelectedId(product.id);
    if (!wide) setDetailOpen(true);
    try { setDetail(await api.inventoryDetail(product.id)); }
    catch (error) { setNotice({ tone: "danger", text: errorText(error) }); }
  }

  async function resolveBarcode(value: string) {
    try {
      const product = await api.productByBarcode(value);
      const full = await api.inventoryDetail(product.id);
      setSelectedId(product.id); setDetail(full); if (!wide) setDetailOpen(true);
      setNotice({ tone: "success", text: `Código leído: ${product.barcode} · ${product.name}` });
    } catch (error) {
      if (errorText(error).includes("No existe un producto")) {
        setNotice({
          tone: "warning", text: `Código no encontrado: ${value}.`,
        });
      } else setNotice({ tone: "danger", text: errorText(error) });
    }
  }

  function onSearchSubmit(value: string) {
    const trimmed = value.trim();
    if (isBarcodeLike(trimmed)) void resolveBarcode(trimmed);
  }

  /** Reloads the table page and (if still selected) the open detail — used after any write. */
  async function refresh() {
    await load();
    if (selectedId) { try { setDetail(await api.inventoryDetail(selectedId)); } catch { /* product may no longer match the current filters */ } }
  }

  async function toggleActive() {
    if (!detail) return;
    setTogglingActive(true);
    try {
      await api.setProductActive(detail.id, !detail.active);
      setNotice({ tone: "success", text: detail.active ? "Producto desactivado" : "Producto activado" });
      await refresh();
    } catch (error) { setNotice({ tone: "danger", text: errorText(error) }); }
    finally { setTogglingActive(false); }
  }

  const totalLabel = status === "active" ? "productos activos" : status === "inactive" ? "productos inactivos" : "productos";

  return (
    <main className="sale purchase inventory" data-module="inventario">
      <Toldo module="inventario" />
      <div className="purchase__head">
        <div className="purchase__head-title"><h1>Inventario</h1></div>
        <div className="ui-row">
          <Link href="/inventory/audit" className="ui-btn ui-btn--secondary ui-btn--md"><ScanLine size={22} aria-hidden="true" />Interventoría</Link>
          <Link href="/inventory/count" className="ui-btn ui-btn--secondary ui-btn--md"><ClipboardList size={22} aria-hidden="true" />Conteo físico</Link>
          <TouchButton module="inventario" icon={Plus} onClick={() => setFormTarget(null)}>Nuevo producto</TouchButton>
        </div>
      </div>

      {notice && <div className="purchase__notice"><InlineMessage tone={notice.tone} onDismiss={() => setNotice(null)}>{notice.text}</InlineMessage></div>}

      <div className="sale__body">
        <section className="sale__main" aria-label="Catálogo de inventario">
          <div className="inventory-scan">
            <div className="purchase-scan">
              <SearchBar label="Escanear código o buscar producto" placeholder="Escanea un código o busca por nombre" value={query} onChange={setQuery} onSubmit={onSearchSubmit} scanner module="inventario" inputRef={inputRef} autoFocus />
            </div>
            <div className="inventory-filters">
              <div className="purchase-filters" role="group" aria-label="Filtrar por stock">
                {([["all", "Todo stock"], ["low", "Stock bajo"], ["out", "Agotados"]] as const).map(([value, label]) => (
                  <TouchButton key={value} variant={stockFilter === value ? "primary" : "secondary"} module="inventario" size="md" onClick={() => setStockFilter(value)}>{label}</TouchButton>
                ))}
              </div>
              <select className="inventory-select" value={categoryId} onChange={event => setCategoryId(event.target.value)} aria-label="Categoría">
                <option value="">Todas las categorías</option>
                {categories.map(category => <option key={category.id} value={category.id}>{category.name}</option>)}
              </select>
              <select className="inventory-select" value={supplierId} onChange={event => setSupplierId(event.target.value)} aria-label="Proveedor">
                <option value="">Todos los proveedores</option>
                {suppliers.map(supplier => <option key={supplier.id} value={supplier.id}>{supplier.name}</option>)}
              </select>
              <select className="inventory-select" value={status} onChange={event => setStatus(event.target.value as StatusFilter)} aria-label="Estado">
                <option value="active">Activos</option>
                <option value="inactive">Inactivos</option>
                <option value="all">Todos</option>
              </select>
            </div>
          </div>

          <div className="sale__cart">
            {loading && !data.items.length ? (
              <div className="sale-status" role="status"><LoaderCircle className="ui-btn__spin" size={28} aria-hidden="true" />Cargando inventario…</div>
            ) : data.items.length ? (
              <ProductTable items={data.items} selectedId={selectedId} onSelect={product => void openProduct(product)}
                sort={sort} sortDir={sortDir} showCategory={showCategory} compact={compact}
                onSort={field => { if (field === sort) setSortDir(current => current === "asc" ? "desc" : "asc"); else { setSort(field); setSortDir("asc"); } }} />
            ) : (
              <div className="sale-empty" data-module="inventario"><span className="sale-empty__icon"><Package size={44} aria-hidden="true" /></span><h2>Sin resultados</h2><p>Ningún producto coincide con estos filtros.</p></div>
            )}
          </div>
          <Pagination page={data.page} totalPages={data.total_pages} total={data.total} itemLabel={totalLabel} module="inventario" onChange={setPage} />
        </section>

        {wide && (
          <section className="sale__panel purchase__panel" aria-label="Detalle del producto">
            {detail ? (
              <ProductDetail product={detail} busy={togglingActive}
                onEdit={() => setFormTarget(detail)} onAdjust={() => setAdjustTarget(detail)}
                onPricing={() => setPricingTarget(detail)} onToggleActive={() => void toggleActive()} />
            ) : (
              <div className="ui-panel-empty">
                <span className="ui-panel-empty__icon"><Package size={40} aria-hidden="true" /></span>
                <p>Toca un producto de la tabla para ver su stock, precios y movimientos, o escanea su código.</p>
              </div>
            )}
          </section>
        )}
      </div>

      <Sheet open={!wide && detailOpen} title={detail?.name ?? "Detalle"} module="inventario" onClose={() => setDetailOpen(false)}>
        {!wide && detail && (
          <ProductDetail product={detail} busy={togglingActive}
            onEdit={() => setFormTarget(detail)} onAdjust={() => setAdjustTarget(detail)}
            onPricing={() => setPricingTarget(detail)} onToggleActive={() => void toggleActive()} />
        )}
      </Sheet>

      <ProductFormSheet
        target={formTarget} categories={categories} suppliers={suppliers}
        onClose={() => setFormTarget(undefined)}
        onSaved={() => { setNotice({ tone: "success", text: formTarget ? "Producto actualizado correctamente" : "Producto creado correctamente" }); setFormTarget(undefined); void refresh(); }}
        create={(body: ProductInput & { initial_stock: number }) => api.createProduct(body)}
        update={(id, body: ProductInput) => api.updateProduct(id, body)}
      />
      <AdjustmentSheet
        product={adjustTarget} onClose={() => setAdjustTarget(null)}
        onSaved={delta => { setAdjustTarget(null); setNotice({ tone: "success", text: `Ajuste ${delta > 0 ? "+" : ""}${delta} registrado correctamente` }); void refresh(); }}
        adjust={(productId, delta, reason) => api.adjustInventory(productId, delta, reason)}
      />
      <ProductPricingSheet
        product={pricingTarget} policy={policy} onClose={() => setPricingTarget(null)}
        onApplied={salePrice => { setNotice({ tone: "success", text: `Precio de venta actualizado a ${money.format(Number(salePrice))}` }); void refresh(); }}
      />
    </main>
  );
}
