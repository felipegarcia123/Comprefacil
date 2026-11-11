import { AuditListItem, AuditReport, AuditSection, PhysicalAudit, AppliedPrice, BlockedSales, CatalogSyncPage, CatalogSyncResult, CatalogSyncState, CatalogSyncStatus, PriorityPosProduct, CutoverReport, SalesCoverage, CardType, CatalogCoverage, CutoverReadiness, InvoiceDetail, InvoicePage, InvoicingMode, InvoicingScope, InvoicingSummary, UnmappedProductPage, InvoiceDisplayState, CashHistoryRow, Customer, CustomerCreateResult, CustomerInput, CustomerSyncResult, CustomerSyncStatus, ElectronicInvoiceIntent, GranelSettlement, ManualItemKind, ReconciliationBlock1, InvoicePreview, CashMovement, CashSummary, CurrentReport, DashboardReport, DailyOperationsSummary, DailyReportListItem, DailyReportOut, ElectronicInvoice, ElectronicInvoiceConfigStatus, CountConfirmBody, CountConfirmResult, CountPreview, Expense, InternalConsumption, InternalConsumptionBatch, InventoryDetail, InventoryMovement, InventoryPage, InventorySort, MonthlyReport, OperationalCategory, PaymentChannel, PaymentSummary, Product, ProductCatalog, ProductImportPreview, ProductImportResult, ProductInput, Purchase, Sale, SaleSource, Supplier, SupplierDetail, WithdrawalReason, SaleDetail, ReturnReason, ExchangePreview, ExchangeReplacementInput, UnitSource, BusinessAccount, AccountReconciliation, AccountMovement, AccountsHistory, InventoryValuation, PatrimonyDeclaration, PatrimonyHistory, PatrimonySource } from "./types";
import { RoundingPolicy } from "./pricing";
import { demoRequest } from "./demoApi";

// No local fallback in browser builds. A public demo must never reach a developer
// machine or an operational POS by accident.
const IS_DEMO = process.env.NEXT_PUBLIC_DEMO_MODE === "1";
const API = IS_DEMO ? "" : (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1");

/** Shared by createPurchase (POST) and updatePurchase (PUT, which replaces the whole item list). */
type PurchaseBody = { supplier_id: string; invoice_number?: string; notes?: string; items: { product_id: string; quantity: number; unit_cost: number }[] };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (IS_DEMO) return demoRequest<T>(path, init);
  let response: Response;
  try {
    response = await fetch(`${API}${path}`, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  } catch {
    throw new Error("No fue posible conectar con el servidor. Intenta nuevamente.");
  }
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    const detail = data?.detail;
    const message = typeof detail === "string" ? detail
      : typeof detail?.message === "string" ? detail.message
      : response.status >= 500 ? "Ocurrió un error inesperado en el servidor. Intenta nuevamente."
      : "Datos inválidos";
    throw new Error(message);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

export const api = {
  dashboardReport: (preset:string, dateFrom?:string, dateTo?:string) => { const query=new URLSearchParams({preset}); if(dateFrom)query.set("date_from",dateFrom);if(dateTo)query.set("date_to",dateTo);return request<DashboardReport>(`/reports/dashboard?${query}`); },
  openSales: () => request<Sale[]>("/sales/open"),
  createSale: () => request<Sale>("/sales", { method: "POST" }),
  cancelSale: (saleId: string) => request<Sale>(`/sales/${saleId}/cancel`, { method: "POST" }),
  search: (q: string) => request<Product[]>(`/products?q=${encodeURIComponent(q)}`),
  /** Scanner path: a scanned/typed code (UPC/EAN rule applied by the backend). */
  addItem: (saleId: string, barcode: string) => request<Sale>(`/sales/${saleId}/items`, { method: "POST", body: JSON.stringify({ barcode, quantity: 1 }) }),
  /** Manual pick from the name search: always by product_id (the only way for products without barcode). */
  addProduct: (saleId: string, product_id: string) => request<Sale>(`/sales/${saleId}/items`, { method: "POST", body: JSON.stringify({ product_id, quantity: 1 }) }),
  quantity: (saleId: string, itemId: string, quantity: number) => request<Sale>(`/sales/${saleId}/items/${itemId}`, { method: "PATCH", body: JSON.stringify({ quantity }) }),
  remove: (saleId: string, itemId: string) => request<Sale>(`/sales/${saleId}/items/${itemId}`, { method: "DELETE" }),
  currentCash: () => request<CashSummary | null>("/cash/current"),
  // Post-venta (0027): every call answers with the refreshed detail; the backend decides what is allowed.
  saleDetail: (saleId: string) => request<SaleDetail>(`/sales/${saleId}/detail`),
  returnReasons: () => request<ReturnReason[]>("/post-sale/return-reasons"),
  exchangeReasons: () => request<ReturnReason[]>("/post-sale/exchange-reasons"),
  /** Backend-computed credit / replacement / difference of a product exchange, without writing. */
  previewExchange: (saleId: string, body: { return_lines: (UnitSource & { quantity: number; restock: boolean })[]; replacement_lines: ExchangeReplacementInput[] }) => request<ExchangePreview>(`/sales/${saleId}/exchanges/preview`, { method: "POST", body: JSON.stringify(body) }),
  createExchange: (saleId: string, body: { return_lines: (UnitSource & { quantity: number; restock: boolean })[]; replacement_lines: ExchangeReplacementInput[]; reason_code: string; reason_text?: string | null; settlement?: { channel_id: string; received_amount?: number; card_type?: string; account_id?: string } | null; idempotency_key: string }) => request<SaleDetail>(`/sales/${saleId}/exchanges`, { method: "POST", body: JSON.stringify(body) }),
  addSaleNote: (saleId: string, text: string, idempotency_key: string) => request<SaleDetail>(`/sales/${saleId}/notes`, { method: "POST", body: JSON.stringify({ text, idempotency_key }) }),
  createReturn: (saleId: string, body: { lines: (UnitSource & { quantity: number; restock: boolean })[]; reason_code: string; reason_text?: string | null; refund_channel_id?: string | null; refund_account_id?: string | null; idempotency_key: string }) => request<SaleDetail>(`/sales/${saleId}/returns`, { method: "POST", body: JSON.stringify(body) }),
  voidSale: (saleId: string, body: { reason_code: string; reason_text?: string | null; restock: boolean; idempotency_key: string }) => request<SaleDetail>(`/sales/${saleId}/void`, { method: "POST", body: JSON.stringify(body) }),
  confirmRefund: (saleId: string, refundId: string, accountId?: string) => request<SaleDetail>(`/sales/${saleId}/refunds/${refundId}/confirm`, { method: "POST", body: JSON.stringify(accountId ? { account_id: accountId } : {}) }),
  /** The difference of an exchange was given back electronically outside the POS (idempotent). */
  confirmExchangeRefund: (saleId: string, exchangeId: string, accountId?: string) => request<SaleDetail>(`/sales/${saleId}/exchanges/${exchangeId}/refund/confirm`, { method: "POST", body: JSON.stringify(accountId ? { account_id: accountId } : {}) }),
  correctPayment: (saleId: string, paymentId: string, body: { to_channel_id: string; to_account_id?: string; reason: string; idempotency_key: string }) => request<SaleDetail>(`/sales/${saleId}/payments/${paymentId}/correct`, { method: "POST", body: JSON.stringify(body) }),
  openCash: (opening_amount: number, note?: string) => request<CashSummary>("/cash/open", { method: "POST", body: JSON.stringify({ opening_amount, note: note || null }) }),
  cashDeposit: (amount: number, reason: string) => request<CashMovement>("/cash/deposit", { method: "POST", body: JSON.stringify({ amount, reason }) }),
  cashWithdrawal: (amount: number, reason: string, withdrawal_reason: WithdrawalReason) => request<CashMovement>("/cash/withdrawal", { method: "POST", body: JSON.stringify({ amount, reason, withdrawal_reason }) }),
  closeCash: (counted_amount: number, expected_amount: number, note?: string, account_balances?: { account_id: string; observed_balance: number }[], declared_patrimony?: { value: number; note?: string } | null) => request<CashSummary>("/cash/close", { method: "POST", body: JSON.stringify({ counted_amount, expected_amount, note: note || null, account_balances: account_balances?.length ? account_balances : null, declared_patrimony: declared_patrimony ?? null }) }),
  // 0028 — declared patrimony (MANUAL, official by default) and the official source.
  sessionPatrimonyDeclaration: (sessionId: string) => request<PatrimonyDeclaration>(`/cash/sessions/${sessionId}/patrimony-declaration`),
  declarePatrimony: (sessionId: string, value: number, note?: string) => request<PatrimonyDeclaration>(`/cash/sessions/${sessionId}/patrimony-declaration`, { method: "PUT", body: JSON.stringify({ value, note: note || null }) }),
  correctPatrimony: (declarationId: string, body: { new_value: number; reason: string; idempotency_key: string }) => request<PatrimonyDeclaration>(`/patrimony-declarations/${declarationId}/corrections`, { method: "POST", body: JSON.stringify(body) }),
  patrimonyHistory: (limit = 60) => request<PatrimonyHistory>(`/patrimony/history?limit=${limit}`),
  patrimonySource: () => request<PatrimonySource>("/patrimony/source"),
  setPatrimonySource: (body: { source: "MANUAL" | "AUTOMATIC"; reason: string; idempotency_key: string }) => request<PatrimonySource>("/patrimony/source", { method: "POST", body: JSON.stringify(body) }),
  // 0028 — patrimony & business accounts (never changes cash, sales or payments).
  businessAccounts: (active?: boolean) => request<BusinessAccount[]>(`/business-accounts${active === undefined ? "" : `?active=${active}`}`),
  sessionAccounts: (sessionId: string) => request<AccountReconciliation>(`/cash/sessions/${sessionId}/accounts`),
  registerAccountBalances: (sessionId: string, balances: { account_id: string; observed_balance: number }[]) => request<AccountReconciliation>(`/cash/sessions/${sessionId}/account-balances`, { method: "PUT", body: JSON.stringify({ balances }) }),
  correctAccountBalance: (snapshotId: string, body: { new_observed_balance: number; reason: string; idempotency_key: string }) => request<AccountReconciliation>(`/business-account-snapshots/${snapshotId}/corrections`, { method: "POST", body: JSON.stringify(body) }),
  accountMovements: (sessionId?: string) => request<AccountMovement[]>(`/business-account-movements${sessionId ? `?session_id=${sessionId}` : ""}`),
  createAccountMovement: (body: { reason: "OTHER_IN" | "OTHER_OUT" | "TRANSFER_BETWEEN_ACCOUNTS"; account_id: string; to_account_id?: string; amount: number; note?: string; idempotency_key: string }) => request<AccountMovement[]>("/business-account-movements", { method: "POST", body: JSON.stringify(body) }),
  reverseAccountMovement: (movementId: string, body: { reason: string; idempotency_key: string }) => request<AccountMovement[]>(`/business-account-movements/${movementId}/reverse`, { method: "POST", body: JSON.stringify(body) }),
  accountsHistory: (limit = 60) => request<AccountsHistory>(`/business-accounts/history?limit=${limit}`),
  inventoryValuationCurrent: () => request<InventoryValuation>("/inventory-valuation/current"),
  inventoryValuationHistory: (limit = 60) => request<InventoryValuation[]>(`/inventory-valuation/history?limit=${limit}`),
  sessionInventoryValuation: (sessionId: string) => request<InventoryValuation>(`/cash/sessions/${sessionId}/inventory-valuation`),
  cashHistory: () => request<CashHistoryRow[]>("/cash/history"),
  cashSession: (id: string) => request<CashSummary>(`/cash/sessions/${id}`),
  checkout: (saleId: string, channel_id: string, received_amount?: number, card_type?: CardType, electronic_invoice?: ElectronicInvoiceIntent, destination_account_id?: string) => request<Sale>(`/sales/${saleId}/checkout`, { method: "POST", body: JSON.stringify({ channel_id, received_amount, card_type, electronic_invoice, destination_account_id }) }),
  checkoutMixed: (saleId: string, payments: { channel_id: string; amount: number; received_amount?: number; reference?: string; card_type?: CardType; destination_account_id?: string }[], electronic_invoice?: ElectronicInvoiceIntent) => request<Sale>(`/sales/${saleId}/checkout`, { method: "POST", body: JSON.stringify({ payments, electronic_invoice }) }),
  reconciliation: (sessionId?: string) => request<ReconciliationBlock1>(`/cash/reconciliation${sessionId ? `?session_id=${sessionId}` : ""}`),
  saveScaleClosing: (sessionId: string, scale_total: number, scale_digital: number) => request<ReconciliationBlock1>(`/cash/sessions/${sessionId}/scale-closing`, { method: "PUT", body: JSON.stringify({ scale_total, scale_digital }) }),
  setGranelSettlement: (saleId: string, settlement: GranelSettlement) => request<Sale>(`/sales/${saleId}/granel-settlement`, { method: "PUT", body: JSON.stringify({ settlement }) }),
  addManualItem: (saleId: string, body: { kind: ManualItemKind; description: string; amount: number }) => request<Sale>(`/sales/${saleId}/manual-items`, { method: "POST", body: JSON.stringify(body) }),
  paymentChannels: (active?: boolean) => request<PaymentChannel[]>(`/payment-channels${active === undefined ? "" : `?active=${active}`}`),
  createPaymentChannel: (body: Pick<PaymentChannel, "name" | "payment_type" | "display_order" | "description" | "provider" | "account_reference" | "alegra_payment_method" | "default_account_id" | "requires_destination_account">) => request<PaymentChannel>("/payment-channels", { method: "POST", body: JSON.stringify(body) }),
  updatePaymentChannel: (id: string, body: Pick<PaymentChannel, "name" | "payment_type" | "display_order" | "description" | "provider" | "account_reference" | "alegra_payment_method" | "default_account_id" | "requires_destination_account">) => request<PaymentChannel>(`/payment-channels/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  setPaymentChannelActive: (id: string, active: boolean) => request<PaymentChannel>(`/payment-channels/${id}/active`, { method: "PATCH", body: JSON.stringify({ active }) }),
  paymentSummary: () => request<PaymentSummary>("/payments/summary"),
  operationalCategories: () => request<OperationalCategory[]>("/operational-categories?active=true"),
  setProductCategory: (productId: string, category_id: string) => request<OperationalCategory>(`/products/${productId}/operational-category`, { method: "PATCH", body: JSON.stringify({ category_id }) }),
  createExpense: (body: { amount: number; category: string; description: string; payment_source: "CASH" | "ELECTRONIC" | "OTHER"; payment_channel_id?: string; notes?: string; idempotency_key: string }) => request<Expense>("/expenses", { method: "POST", body: JSON.stringify(body) }),
  expenses: () => request<Expense[]>("/expenses"),
  createInternalConsumption: (body: { product_id?: string; quantity?: number; description: string; estimated_value?: number; notes?: string; idempotency_key: string }) => request<InternalConsumption>("/internal-consumption", { method: "POST", body: JSON.stringify(body) }),
  createInternalConsumptionBatch: (body: { items: { product_id: string; quantity: number }[]; notes?: string; idempotency_key: string }) => request<InternalConsumptionBatch>("/internal-consumption/batch", { method: "POST", body: JSON.stringify(body) }),
  internalConsumptions: () => request<InternalConsumption[]>("/internal-consumption"),
  dailyOperations: () => request<DailyOperationsSummary>("/daily-operations/summary"),
  /** Always server-side paginated — never fetches the full ~7 500-product catalog. */
  inventory: (params: { q?: string; low_stock?: boolean; out_of_stock?: boolean; category_id?: string; supplier_id?: string; active?: boolean; sort?: InventorySort; sort_dir?: "asc" | "desc"; page?: number; page_size?: number } = {}) => {
    const query = new URLSearchParams();
    if (params.q) query.set("q", params.q);
    if (params.low_stock) query.set("low_stock", "true");
    if (params.out_of_stock) query.set("out_of_stock", "true");
    if (params.category_id) query.set("category_id", params.category_id);
    if (params.supplier_id) query.set("supplier_id", params.supplier_id);
    if (params.active !== undefined) query.set("active", String(params.active));
    if (params.sort) query.set("sort", params.sort);
    if (params.sort_dir) query.set("sort_dir", params.sort_dir);
    query.set("page", String(params.page ?? 1));
    query.set("page_size", String(params.page_size ?? 50));
    return request<InventoryPage>(`/inventory?${query}`);
  },
  inventoryDetail: (id: string) => request<InventoryDetail>(`/inventory/${id}`),
  createProduct: (body: ProductInput & { initial_stock: number }) => request<ProductCatalog>("/products", { method: "POST", body: JSON.stringify(body) }),
  updateProduct: (id: string, body: ProductInput) => request<ProductCatalog>(`/products/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  setProductActive: (id: string, active: boolean) => request<ProductCatalog>(`/products/${id}/active`, { method: "PATCH", body: JSON.stringify({ active }) }),
  productByBarcode: (barcode: string) => request<ProductCatalog>(`/products/by-barcode?barcode=${encodeURIComponent(barcode)}`),
  countTemplateUrl: `${API}/inventory/count/template`,
  previewCount: (file: File) => request<CountPreview>("/inventory/count/preview", { method: "POST", body: file, headers: { "Content-Type": "application/octet-stream" } }),
  confirmCount: (body: CountConfirmBody) => request<CountConfirmResult>("/inventory/count/confirm", { method: "POST", body: JSON.stringify(body) }),
  /* Physical audit (interventoría física): local only, never writes inventory. */
  audits: () => request<AuditListItem[]>("/physical-audits"),
  currentAudit: () => request<PhysicalAudit | null>("/physical-audits/current"),
  startAudit: () => request<PhysicalAudit>("/physical-audits", { method: "POST" }),
  audit: (id: string) => request<PhysicalAudit>(`/physical-audits/${id}`),
  auditScan: (id: string, ref: { barcode: string } | { product_id: string }) => request<PhysicalAudit & { item_id: string | null; unknown_code: { barcode: string; scan_count: number } | null }>(`/physical-audits/${id}/scan`, { method: "POST", body: JSON.stringify(ref) }),
  auditQuantity: (id: string, itemId: string, quantity: number) => request<PhysicalAudit>(`/physical-audits/${id}/items/${itemId}`, { method: "PUT", body: JSON.stringify({ quantity }) }),
  auditRemove: (id: string, itemId: string) => request<PhysicalAudit>(`/physical-audits/${id}/items/${itemId}`, { method: "DELETE" }),
  finishAudit: (id: string) => request<PhysicalAudit>(`/physical-audits/${id}/finish`, { method: "POST" }),
  reopenAudit: (id: string) => request<PhysicalAudit>(`/physical-audits/${id}/reopen`, { method: "POST" }),
  auditReport: (id: string) => request<AuditReport>(`/physical-audits/${id}/report`),
  auditCsvUrl: (id: string, section: AuditSection) => `${API}/physical-audits/${id}/report.csv?section=${section}`,
  auditCountTemplateUrl: (id: string) => `${API}/physical-audits/${id}/count-template`,
  previewProductImport: (body: { filename:string; content:string; mapping:Record<string,string>; delimiter:"auto"|","|";" }) => request<ProductImportPreview>("/products/import/preview", { method:"POST", body:JSON.stringify(body) }),
  confirmProductImport: (body: { filename:string; content:string; mapping:Record<string,string>; delimiter:"auto"|","|";" }) => request<ProductImportResult>("/products/import/confirm", { method:"POST", body:JSON.stringify(body) }),
  receiptCatalogDiagnostic: () => request<import("./types").ReceiptCatalogDiagnostic>("/receipt-ai/catalog-diagnostic"),
  receiptCandidates: (q:string, barcode?:string, provider?:string, material_code?:string) => request<import("./types").ReceiptCandidate[]>(`/receipt-ai/candidates?${new URLSearchParams({ q, ...(barcode ? { barcode } : {}), ...(provider ? { provider } : {}), ...(material_code ? { material_code } : {}) })}`),
  receiptEquivalences: (params: { provider?: string; material_code?: string; active?: boolean } = {}) => request<import("./types").ReceiptEquivalence[]>(`/receipt-ai/equivalences?${new URLSearchParams({ ...(params.provider ? { provider: params.provider } : {}), ...(params.material_code ? { material_code: params.material_code } : {}), ...(params.active === undefined ? {} : { active: String(params.active) }) })}`),
  confirmReceiptEquivalence: (body: { provider:string; material_code:string; product_id:string; source_description?:string|null; source_brand?:string|null; source_variant?:string|null; source_size?:string|null }) => request<import("./types").ReceiptEquivalence>("/receipt-ai/equivalences", { method:"POST", body:JSON.stringify(body) }),
  setReceiptEquivalenceActive: (id:string, active:boolean) => request<import("./types").ReceiptEquivalence>(`/receipt-ai/equivalences/${id}/active`, { method:"PATCH", body:JSON.stringify({ active }) }),
  createReceiptPurchase: (body: { confirmed:boolean; supplier_id:string; invoice_number:string; purchased_at?:string; notes?:string|null; source_sha256?:string|null; items:{product_id:string; quantity:number; unit_cost:number}[] }) => request<Purchase>("/receipt-ai/purchase", { method:"POST", body:JSON.stringify(body) }),
  analyzeReceipt: (body:{ filename:string; content_type:string; content_base64:string; vendor_hint?:string; region?:import("./types").ReceiptRegion|null }) => request<import("./types").ReceiptAnalysis>("/receipt-ai/analyze", { method:"POST", body:JSON.stringify(body) }),
  inventoryMovements: (params: { product_id?: string; movement_type?: string } = {}) => {
    const query = new URLSearchParams(params);
    return request<InventoryMovement[]>(`/inventory/movements?${query}`);
  },
  adjustInventory: (product_id: string, quantity_delta: number, reason: string) =>
    request<{ movement: InventoryMovement; current_stock: string }>("/inventory/adjustments", {
      method: "POST", body: JSON.stringify({ product_id, quantity_delta, reason }),
    }),
  suppliers: (q = "", active?: boolean) => request<Supplier[]>(`/suppliers?q=${encodeURIComponent(q)}${active === undefined ? "" : `&active=${active}`}`),
  supplier: (id: string) => request<SupplierDetail>(`/suppliers/${id}`),
  createSupplier: (body: Omit<Supplier, "id" | "active" | "created_at">) => request<Supplier>("/suppliers", { method: "POST", body: JSON.stringify(body) }),
  updateSupplier: (id: string, body: Omit<Supplier, "id" | "active" | "created_at">) => request<Supplier>(`/suppliers/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  setSupplierActive: (id: string, active: boolean) => request<Supplier>(`/suppliers/${id}/active`, { method: "PATCH", body: JSON.stringify({ active }) }),
  purchases: (status = "") => request<Purchase[]>(`/purchases${status ? `?status=${status}` : ""}`),
  purchase: (id: string) => request<Purchase>(`/purchases/${id}`),
  createPurchase: (body: PurchaseBody) => request<Purchase>("/purchases", { method: "POST", body: JSON.stringify(body) }),
  updatePurchase: (id: string, body: PurchaseBody) => request<Purchase>(`/purchases/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  receivePurchaseWithPayment: (id:string, payment_channel_id:string, reference?:string, notes?:string) => request<Purchase>(`/purchases/${id}/receive-with-payment`, { method:"POST", body:JSON.stringify({payment_channel_id,reference:reference||null,notes:notes||null}) }),
  payPurchase: (id:string, payment_channel_id:string, reference?:string, notes?:string) => request<Purchase>(`/purchases/${id}/payment`, { method:"POST", body:JSON.stringify({payment_channel_id, reference:reference || null, notes:notes || null}) }),
  cancelPurchase: (id: string) => request<Purchase>(`/purchases/${id}/cancel`, { method: "POST" }),
  pricingConfig: () => request<{ commercial_rounding_policy: RoundingPolicy }>("/pricing/config"),
  applySuggestedPrice: (productId: string, unit_cost: number, target_margin_percent: number) =>
    request<AppliedPrice>(`/products/${productId}/sale-price/suggested`, {
      method: "POST", body: JSON.stringify({ unit_cost, target_margin_percent }),
    }),
  currentReport: () => request<CurrentReport>("/reports/current"),
  dailyReportLatest: () => request<DailyReportOut | null>("/reports/daily/latest"),
  dailyReports: (year?: number, month?: number) => { const q = new URLSearchParams(); if (year) q.set("year", String(year)); if (month) q.set("month", String(month)); return request<DailyReportListItem[]>(`/reports/daily?${q}`); },
  dailyReport: (id: string) => request<DailyReportOut>(`/reports/daily/${id}`),
  generateDailyReport: (cashSessionId: string) => request<DailyReportOut>(`/reports/sessions/${cashSessionId}/generate`, { method: "POST" }),
  monthlyReport: (year: number, month: number) => request<MonthlyReport>(`/reports/monthly?year=${year}&month=${month}`),
  electronicInvoiceConfigStatus: () => request<ElectronicInvoiceConfigStatus>("/electronic-invoices/config-status"),
  electronicInvoiceForSale: (saleId: string) => request<ElectronicInvoice | null>(`/electronic-invoices/sale/${saleId}`),
  /** Read-only: what would be sent to Alegra (never POSTs). */
  electronicInvoicePreview: (saleId: string) => request<InvoicePreview>(`/electronic-invoices/sale/${saleId}/preview`),
  /** Local search only (never calls Alegra). */
  customers: (q = "", active: boolean | null = true, limit = 20) => { const query = new URLSearchParams({ q, limit: String(limit) }); if (active !== null) query.set("active", String(active)); return request<Customer[]>(`/customers?${query}`); },
  createCustomer: (body: CustomerInput) => request<CustomerCreateResult>("/customers", { method: "POST", body: JSON.stringify(body) }),
  updateCustomer: (id: string, body: { email: string | null; phone: string | null; address: string | null }) => request<Customer>(`/customers/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  setCustomerActive: (id: string, active: boolean) => request<Customer>(`/customers/${id}/active`, { method: "PATCH", body: JSON.stringify({ active }) }),
  syncCustomers: () => request<CustomerSyncResult>("/customers/sync", { method: "POST" }),
  customerSyncStatus: () => request<CustomerSyncStatus>("/customers/sync-status"),
  /** null = Consumidor Final. */
  setSaleCustomer: (saleId: string, customer_id: string | null) => request<Sale>(`/sales/${saleId}/customer`, { method: "PUT", body: JSON.stringify({ customer_id }) }),
  // ---- electronic-invoicing control panel (read-only; the only Alegra calls are GETs) ----
  invoicingMode: () => request<InvoicingMode>("/electronic-invoices/mode"),
  invoicingSummary: (scope: InvoicingScope, day?: string) => request<InvoicingSummary>(`/electronic-invoices/summary?${new URLSearchParams({ scope, ...(day ? { day } : {}) })}`),
  invoicingList: (params: { state?: InvoiceDisplayState | ""; scope: InvoicingScope; day?: string; sale_number?: string; customer?: string; page?: number }) => {
    const query = new URLSearchParams({ scope: params.scope, page: String(params.page ?? 1), page_size: "50" });
    if (params.state) query.set("state", params.state);
    if (params.day) query.set("day", params.day);
    if (params.sale_number) query.set("sale_number", params.sale_number);
    if (params.customer) query.set("customer", params.customer);
    return request<InvoicePage>(`/electronic-invoices/list?${query}`);
  },
  invoiceDetail: (id: string) => request<InvoiceDetail>(`/electronic-invoices/${id}`),
  /** Usable PDF/XML link: the backend refreshes expired signed links with a GET to Alegra. */
  invoiceDocument: (id: string, kind: "pdf" | "xml") => request<{ url: string; refreshed: boolean }>(`/electronic-invoices/${id}/document?kind=${kind}`),
  cutoverReadiness: (remote = false) => request<CutoverReadiness>(`/electronic-invoices/cutover-readiness${remote ? "?remote=true" : ""}`),
  blockedSales: (scope: "session" | "day" = "session", day?: string) => request<BlockedSales>(`/electronic-invoices/blocked-sales?${new URLSearchParams({ scope, ...(day ? { day } : {}) })}`),
  // ---- POS <-> Alegra catalog sync (analysis = GET-only on Alegra; LINK = local write; CREATE disabled) ----
  catalogSyncState: () => request<CatalogSyncState>("/alegra-catalog/status"),
  startCatalogAnalysis: () => request<{ id: string; status: string }>("/alegra-catalog/analysis", { method: "POST" }),
  catalogAnalysis: (params: { status?: CatalogSyncStatus | ""; q?: string; page?: number }) => {
    const query = new URLSearchParams({ page: String(params.page ?? 1), page_size: "50" });
    if (params.status) query.set("status", params.status);
    if (params.q) query.set("q", params.q);
    return request<CatalogSyncPage>(`/alegra-catalog/analysis/latest?${query}`);
  },
  catalogAnalysisCsvUrl: `${API}/alegra-catalog/analysis/latest.csv`,
  linkCatalogProducts: (product_ids: string[], dry_run: boolean) => request<CatalogSyncResult>("/alegra-catalog/link", { method: "POST", body: JSON.stringify({ product_ids, dry_run }) }),
  priorityPosProducts: () => request<PriorityPosProduct[]>("/alegra-catalog/priority-products"),
  salesCoverage: () => request<SalesCoverage>("/electronic-invoices/sales-coverage"),
  cutoverReport: () => request<CutoverReport>("/electronic-invoices/cutover-report"),
  catalogCoverage: () => request<CatalogCoverage>("/electronic-invoices/catalog-coverage"),
  unmappedProducts: (q = "", page = 1) => request<UnmappedProductPage>(`/electronic-invoices/unmapped-products?${new URLSearchParams({ q, page: String(page), page_size: "25" })}`),
  unmappedProductsCsvUrl: `${API}/electronic-invoices/unmapped-products.csv`,
  retryElectronicInvoice: (id: string) => request<ElectronicInvoice>(`/electronic-invoices/${id}/retry`, { method: "POST" }),
  syncElectronicInvoice: (id: string) => request<ElectronicInvoice>(`/electronic-invoices/${id}/sync`, { method: "POST" }),
};
