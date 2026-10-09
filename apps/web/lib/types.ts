export type PaymentMethod = "CASH" | "CARD" | "TRANSFER";
export type PaymentGroup = "CASH" | "ELECTRONIC" | "OTHER";
export type SaleSource = "POS_PRODUCT" | "MANUAL_BULK" | "MANUAL_SACK";
export type WithdrawalReason = "HOME" | "OTHER";
export type ElectronicInvoiceStatus = "PENDING" | "BLOCKED" | "PROCESSING" | "ISSUED" | "REJECTED" | "ERROR";
export type ElectronicInvoice = { id: string; sale_id: string; provider: string; status: ElectronicInvoiceStatus; provider_invoice_id: string | null; provider_number: string | null; provider_status: string | null; legal_status: string | null; pdf_url: string | null; xml_url: string | null; alegra_contact_id: string | null; customer_id: string | null; customer_snapshot: InvoiceCustomerSnapshot | null; requested_at: string; issued_at: string | null; last_synced_at: string | null; error_code: string | null; error_message: string | null; needs_reconciliation?: boolean; attempt_count?: number; cufe?: string | null; display_status?: "PENDING" | "BLOCKED" | "HELD" | "PROCESSING" | "DRAFT" | "SENT" | "ACCEPTED" | "DIAN_REJECTED" | "REJECTED" | "ERROR"; created_at: string; updated_at: string };
export type ElectronicInvoiceConfigStatus = { enabled: boolean; configured: boolean; missing: string[]; auto_issue?: boolean; auto_issue_stamp?: boolean };
export type InvoiceCustomerSnapshot = { final_consumer: boolean; alegra_contact_id: string; name: string; identification_type: string; identification_number: string; dv?: string | null; email?: string | null };
export type InvoicePreview = { sale_id: string; sale_number: string; ready: boolean; reasons: string[]; alegra_contact_id: string | null; customer: InvoiceCustomerSnapshot | null; generate_stamp: boolean; payload: Record<string, unknown> | null };
export type IdentificationType = "CC" | "NIT" | "CE" | "PP" | "TI" | "RC" | "TE" | "DIE" | "NUIP" | "FOREIGN_NIT";
export type KindOfPerson = "PERSON_ENTITY" | "LEGAL_ENTITY";
export type Customer = { id: string; alegra_contact_id: string | null; identification_type: string; identification_number: string; dv: string | null; kind_of_person: string; name: string; first_name: string | null; last_name: string | null; email: string | null; phone: string | null; address: string | null; active: boolean; last_synced_at: string | null; created_at: string; updated_at: string };
export type CustomerInput = { identification_type: IdentificationType; identification_number: string; kind_of_person: KindOfPerson; name?: string; first_name?: string; last_name?: string; email?: string; phone?: string; address?: string };
export type CustomerCreateResult = { customer: Customer; outcome: "EXISTING_LOCAL" | "LINKED_REMOTE" | "CREATED" };
export type CustomerSyncResult = { created: number; updated: number; unchanged: number; errors: number; error_messages: string[]; total_remote: number; finished_at: string | null };
export type CustomerSyncStatus = { alegra_configured: boolean; total_customers: number; last_synced_at: string | null; final_consumer_resolved: boolean };
export type SaleCustomer = { id: string; name: string; identification_type: string; identification_number: string; dv: string | null; email: string | null };
/** "Producto manual" line kind (product_id null): Súper, comida a granel or bulto. */
export type ManualItemKind = "SUPER" | "GRANEL" | "BULTO" | "PET_OTHER";
export type Item = { id: string; product_id: string | null; product_name: string; quantity: string; unit_price: string; line_total: string; manual_kind?: ManualItemKind | null; category_code?: string | null };
export type PaymentChannel = { id: string; name: string; payment_type: PaymentMethod; payment_group: PaymentGroup; active: boolean; affects_cash: boolean; display_order: number; description: string | null; provider: string | null; account_reference: string | null; alegra_payment_method?: string | null; default_account_id?: string | null; requires_destination_account?: boolean; created_at: string; updated_at: string };
export type Payment = { method: PaymentMethod | null; payment_channel_id: string; channel_name: string; affects_cash: boolean; amount: string; received_amount: string | null; change_amount: string | null; reference: string | null; card_type?: CardType | null };
/** Card channels (Datáfono): what the cashier picks. Never shown as a DIAN code. */
export type CardType = "DEBIT" | "CREDIT";
export type Sale = { id: string; number: string; status: "OPEN" | "COMPLETED" | "CANCELLED"; source: SaleSource; notes: string | null; customer?: SaleCustomer | null; requires_electronic_invoice?: boolean; invoice_customer_id?: string | null; granel_settlement?: GranelSettlement | null; external_items?: ExternalItem[]; total: string; items: Item[]; payments: Payment[] };
/** How the sale's comida a granel was settled: by the scale ("Báscula / efectivo", never POS money) or in the POS ("Pago electrónico"). */
/** Cuadre de jornada, first block only ("Subtotal parcial del cuadre") — never expected cash nor a difference. */
export type ReconciliationBlock1 = { cash_session_id: string; session_status: string; date_from: string; date_to: string; scale: { total: string; digital: string; cash: string; updated_at: string | null } | null; pos_cash_by_category: { category_code: string; amount: string }[]; pos_cash_subtotal: string; block1_subtotal: string | null; observed: { granel_external_scale: string; granel_pos: string; granel_pos_cash_legacy: string }; returns_cash_by_category?: { category_code: string; amount: string }[]; returns_cash_total?: string; exchange_difference_cash_by_category?: { category_code: string; amount: string }[]; exchange_difference_cash_total?: string; exchange_refund_cash_by_category?: { category_code: string; amount: string }[]; exchange_refund_cash_total?: string };
export type GranelSettlement = "EXTERNAL_SCALE" | "POS";
/** Granel settled by the scale, kept after checkout: part of the customer's bill, not a POS line. */
export type ExternalItem = { kind: string; settlement: string; description: string; quantity: string; unit_price: string; amount: string };
/** The "Factura electrónica" checkbox sent with COBRAR (absent = no invoice). Consumidor final is never typed. */
export type ElectronicInvoiceIntent = { recipient: "FINAL_CONSUMER" } | { recipient: "CUSTOMER"; customer_id: string };
export type Product = { id: string; barcode: string | null; name: string; sale_price: string; stock: string };
export type InventoryProduct = { id: string; barcode: string | null; name: string; sale_price: string; unit_cost: string; last_purchase_cost: string | null; estimated_margin_percent: string | null; current_stock: string; supplier: string | null; supplier_id: string | null; category: string | null; category_id: string | null; minimum_stock: number; unit: string; active: boolean; description: string | null };
export type InventoryMovement = { id: string; product_id: string; product_name: string; movement_type: "INITIAL_STOCK" | "PURCHASE" | "SALE" | "RETURN" | "DAMAGE" | "ADJUSTMENT" | "INTERNAL_CONSUMPTION" | "EXCHANGE_OUT"; quantity: string; unit_cost: string | null; reference_id: string | null; reason: string | null; created_at: string; user_id: string | null };
export type InventoryDetail = InventoryProduct & { recent_movements: InventoryMovement[] };
export type InventorySort = "name" | "stock" | "cost" | "price" | "margin";
export type InventoryPage = { items: InventoryProduct[]; total: number; page: number; page_size: number; total_pages: number };
export type ProductCatalog = { id: string; barcode: string | null; name: string; sale_price: string; purchase_cost: string; category_id: string | null; category: string | null; supplier_id: string | null; supplier: string | null; minimum_stock: number; unit: string; active: boolean; description: string | null; current_stock: string; last_purchase_cost: string | null; created_at: string; updated_at: string };
export type ProductInput = { barcode: string | null; name: string; sale_price: number | string; purchase_cost: number | string; category_id: string | null; supplier_id: string | null; initial_stock?: number; minimum_stock: number; unit: string; description: string | null; active: boolean };
export type ProductImportRow = { row_number:number; parsed_data:Record<string,string|number|boolean|null>; status:"READY"|"WARNING"|"ERROR"; warnings:string[]; errors:string[] };
export type ProductImportPreview = { filename:string; delimiter:string; headers:string[]; total_rows:number; ready_count:number; warning_count:number; error_count:number; rows:ProductImportRow[] };
export type ProductImportResult = { imported_count:number; skipped_count:number; warning_count:number; initial_stock_total:number; rows:ProductImportRow[] };
export type DashboardReport = { period:{preset:string;date_from:string;date_to:string;previous_from:string;previous_to:string;granularity:"hour"|"day"}; summary:{returns_total?:string;net_sales?:string|null;exchange_count?:number;exchange_difference?:string;exchange_difference_refunded?:string;sales_total:string;tickets:number;average_ticket:string;units:string;expenses:string;internal_consumption:string;sales_change:string|null;tickets_change:string|null;average_ticket_change:string|null;peak_tickets_label:string|null;peak_tickets:number;peak_sales_label:string|null;peak_sales:string;leading_category:string|null;leading_channel:string|null}; series:{key:string;label:string;sales:string;tickets:number;average_ticket:string}[]; categories:{code:string;name:string;cash:string;electronic:string;other:string;total:string;percentage:string}[]; payment_groups:{group:string;amount:string;percentage:string}[]; payment_channels:{channel_id:string;name:string;group:string;amount:string;payment_count:number;percentage:string}[]; top_products:{product_id:string;name:string;units:string;sales:string}[] };
export type Supplier = { id: string; name: string; document: string | null; phone: string | null; email: string | null; address: string | null; contact_name: string | null; notes: string | null; active: boolean; created_at: string };
export type PurchaseStatus = "DRAFT" | "RECEIVED" | "CANCELLED";
export type SupplierDetail = Supplier & { purchases: { id: string; status: PurchaseStatus; invoice_number: string | null; purchased_at: string; received_at: string | null; total: string }[]; total_purchased: string; last_purchase_at: string | null };
export type PurchaseItem = { id: string; product_id: string; product_name: string; barcode: string | null; quantity: string; unit_cost: string; line_total: string };
export type SupplierPayment = { id:string; purchase_id:string; supplier_id:string; amount:string; payment_channel_id:string; channel_name:string; payment_type:PaymentMethod; affects_cash:boolean; reference:string|null; notes:string|null; paid_at:string };
export type Purchase = { id: string; supplier_id: string; supplier_name: string; status: PurchaseStatus; invoice_number: string | null; purchased_at: string; received_at: string | null; notes: string | null; subtotal: string; total: string; created_at: string; created_by: string; payment: SupplierPayment | null; items: PurchaseItem[] };
export type AppliedPrice = { product_id: string; previous_sale_price: string; sale_price: string; mathematical_price: string; target_margin_percent: string; resulting_margin_percent: string | null; rounding_policy: import("./pricing").RoundingPolicy };
export type PaymentSummary = { total_sales: string; channels: { channel_id: string; channel_name: string; payment_type: PaymentMethod; amount: string; payment_count: number; percentage: string }[] };
export type CashMovementType = "SALE" | "DEPOSIT" | "WITHDRAWAL" | "REFUND" | "ADJUSTMENT" | "EXPENSE" | "SUPPLIER_PAYMENT";
export type CashSession = { id: string; status: "OPEN" | "CLOSED"; opening_amount: string; opening_note: string | null; opened_at: string; opened_by: string; closed_at: string | null; closed_by: string | null; closing_expected: string | null; closing_actual: string | null; difference: string | null; closing_note: string | null };
export type CashMovement = { id: string; movement_type: CashMovementType; amount: string; reference_id: string | null; reference: string | null; notes: string | null; created_at: string; user_id: string };
export type FinancialSessionMovement = {id:string;occurred_at:string;type:string;direction:"IN"|"OUT";amount:string;payment_channel:string|null;affects_cash:boolean;title:string;subtitle:string|null;reference_type:string;reference_id:string|null;withdrawal_reason:WithdrawalReason|null};
export type CashSummary = { session: CashSession; cash_sales: string; deposits: string; withdrawals: string; refunds: string; adjustments: string; expenses: string; supplier_payments_cash:string; payment_corrections_cash?:string; exchanges_cash?:string; exchange_refunds_cash?:string; expected_cash: string; total_sales: string; transaction_count: number; payment_channels: { channel_id: string; channel_name: string; payment_type: PaymentMethod; amount: string; payment_count: number }[]; recent_movements: CashMovement[]; financial_movements:FinancialSessionMovement[]; supplier_payments_total:string; supplier_payment_channels:{channel_id:string;channel_name:string;payment_type:PaymentMethod;amount:string;payment_count:number}[] };
export type CashHistoryRow = { session: CashSession; cash_sales: string; deposits: string; withdrawals: string; expected_cash: string; inventory_value?: string | null; declared_patrimony?: string | null; accounts_registered?: number | null; accounts_total?: number | null };

/** 0028 — business accounts (Nequi / Davivienda / Nu / DaviPlata): reconciliation only, never a payment or a credential. */
export type BusinessAccount = { id: string; code: string; name: string; active: boolean; display_order: number };
export type AccountBreakdown = { payments: string; corrections_in: string; corrections_out: string; exchange_differences_in: string; exchange_differences_out: string; refunds_out: string };
export type AccountCorrection = { id: string; old_observed_balance: string; new_observed_balance: string; reason: string; created_at: string };
/** CAPTURED = balance typed (frozen); PENDING = not typed yet (live preview); LOCKED = a later jornada already has one. */
export type AccountReconciliationRow = { account_id: string; code: string; name: string; status: "CAPTURED" | "PENDING" | "LOCKED"; is_baseline: boolean; snapshot_id: string | null; observed_balance: string | null; original_observed_balance?: string; previous_observed_balance: string | null; pos_inflows: string; pos_outflows: string; registered_external_inflows: string; registered_external_outflows: string; expected_balance: string | null; difference: string | null; breakdown: AccountBreakdown; pending_outflows: string; corrections: AccountCorrection[]; can_correct: boolean; captured_at?: string };
/** 0028 — VALOR DECLARADO POR EL DUEÑO (origin MANUAL), the official patrimony source by default. */
export type PatrimonySource = { source: "MANUAL" | "AUTOMATIC"; label: string; since: string | null; changes?: { id: string; source: "MANUAL" | "AUTOMATIC"; label: string; reason: string; created_at: string }[]; confirmed_physical_counts?: number };
export type PatrimonyDeclaration = { cash_session_id: string; status: "DECLARED" | "NOT_DECLARED"; official_source: PatrimonySource; id?: string; source?: "MANUAL"; declared_value?: string | number; original_value?: string | number; note?: string | null; declared_at?: string; closed_at?: string; corrections?: { id: string; old_value: string | number; new_value: string | number; reason: string; created_at: string }[]; previous_value?: string | number | null; variation_amount?: string | number | null; variation_percent?: string | number | null; previous_closed_at?: string | null; is_first?: boolean; last_declared_value?: string | number | null; last_declared_closed_at?: string | null };
export type PatrimonyHistory = { official_source: PatrimonySource; source_changes: NonNullable<PatrimonySource["changes"]>; confirmed_physical_counts: number; sessions: { cash_session_id: string; opened_at: string; closed_at: string; declared: PatrimonyDeclaration | null; automatic: InventoryValuation }[] };
/** Electronic money of the period without destination account: never attributed by guessing. */
export type UnclassifiedOperations = { count: number; amount: string | number; items: { kind: "PAYMENT" | "REFUND" | "EXCHANGE"; label: string; amount: string | number }[] };
export type AccountReconciliation = { cash_session_id: string; session_status: "OPEN" | "CLOSED"; status: "COMPLETE" | "PARTIAL" | "PENDING"; accounts: AccountReconciliationRow[]; unclassified?: UnclassifiedOperations; verified?: boolean };
export type AccountMovementReason = "OTHER_IN" | "OTHER_OUT" | "TRANSFER_BETWEEN_ACCOUNTS" | "REVERSAL";
export type AccountMovement = { id: string; account_id: string; account_name: string; cash_session_id: string; direction: "IN" | "OUT"; amount: string; reason: AccountMovementReason; reason_label: string; note: string | null; transfer_group_id: string | null; reversal_of_id: string | null; reversed: boolean; created_at: string };
export type AccountsHistory = { accounts: { id: string; code: string; name: string; active: boolean }[]; latest: Record<string, (Partial<AccountReconciliationRow> & { cash_session_id: string }) | null>; sessions: { cash_session_id: string; opened_at: string; closed_at: string; accounts: (Partial<AccountReconciliationRow> & { account_id: string; status: "CAPTURED" | "NONE" })[] }[] };
/** 0028 — inventory at cost (stock x Product.unit_cost). Never $0 for a jornada without photo: NO_SNAPSHOT. */
export type InventoryValuation = { status?: "LIVE" | "SNAPSHOT" | "NO_SNAPSHOT"; inventory_value: string | null; active_value?: string | number; inactive_value?: string | number; active_catalog_count?: number; active_with_stock?: number; is_official?: boolean; verification?: "NO VERIFICADA" | "OFICIAL"; coverage_message?: string; products_with_positive_stock?: number; products_valued?: number; products_missing_cost?: number; products_negative_stock?: number; quantity_valued?: string; quantity_missing_cost?: string; complete?: boolean; message?: string; missing_cost_products?: { id: string; name: string; barcode: string | null; stock: string }[]; previous_value?: string | null; variation_amount?: string | null; variation_percent?: string | null; is_baseline?: boolean; captured_at?: string; previous_closed_at?: string | null; cash_session_id?: string; opened_at?: string; closed_at?: string };
export type OperationalCategory = { id: string; code: string; name: string; active: boolean; display_order: number };
export type Expense = { id: string; amount: string; category: string; description: string; payment_source: PaymentGroup; payment_channel_id: string | null; channel_name: string | null; notes: string | null; created_at: string; created_by: string };
export type InternalConsumption = { id: string; product_id: string | null; product_name: string | null; description: string; quantity: string | null; unit_cost: string | null; reference_sale_price: string | null; cost_total: string; sale_value_total: string | null; estimated_value: string | null; notes: string | null; created_at: string; created_by: string };
export type OperationalWindowStatus = "OPEN" | "CLOSED" | "NONE" | "CUSTOM";
export type DailyOperationsSummary = { returns?: ReturnsBlock | null; exchanges?: ExchangesBlock | null; date_from: string; date_to: string; session_status: OperationalWindowStatus; sales_total: string; external_scale_total?: string; categories: { category_code: string; category_name: string; cash: string; electronic: string; other: string; total: string; channels: { channel_id: string; channel_name: string; payment_group: PaymentGroup; amount: string }[] }[]; outgoing: { expenses_total: string; expenses_cash: string; expenses_electronic: string; expenses_other: string; internal_consumption_cost: string; internal_consumption_estimated: string; home_withdrawals: string; other_withdrawals: string } };

export type DailyReportCategory = { code: string; name: string; total: string; cash: string; electronic: string; channels: { channel_id: string; channel_name: string; payment_group: PaymentGroup; amount: string }[] };
export type DailyReportChannel = { channel_id: string; channel_name: string; payment_type: PaymentMethod; payment_group: PaymentGroup; amount: string; transaction_count: number };
export type DailyReportProduct = { product_id: string; product_name: string; units: string; revenue: string; cost: string; gross_profit: string };
export type DailyReportHour = { hour: string; tickets: number; revenue: string };
export type DailyReportOperations = { expenses: string; supplier_payments: string; withdrawals_home: string; withdrawals_other: string; internal_consumption: string; deposits: string };
export type DailyReportHighlights = {
  payment_channel_by_revenue: { channel_id: string; channel_name: string; amount: string } | null;
  payment_channel_by_transactions: { channel_id: string; channel_name: string; transaction_count: number } | null;
  peak_hour_by_tickets: { hour: string; tickets: number } | null;
  peak_hour_by_revenue: { hour: string; revenue: string } | null;
  top_product_by_units: { product_id: string; product_name: string; units: string } | null;
  top_product_by_revenue: { product_id: string; product_name: string; revenue: string } | null;
  top_product_by_gross_profit: { product_id: string; product_name: string; gross_profit: string } | null;
  top_category_by_revenue: { code: string; name: string; total: string } | null;
};
export type DailyReportData = { categories: DailyReportCategory[]; payment_channels: DailyReportChannel[]; products: DailyReportProduct[]; hourly: DailyReportHour[]; operations: DailyReportOperations; highlights: DailyReportHighlights; returns?: ReturnsBlock; exchanges?: ExchangesBlock };
/** Product exchanges of the window: product movement, not sales nor refunds; only the difference is money. */
export type ExchangesBlock = { exchange_count: number; same_value_count: number; customer_pays_count?: number; store_refunds_count?: number; returned_credit: string; replacement_total: string; difference_collected: string; difference_cash: string; difference_electronic: string; difference_refunded?: string; refunded_cash?: string; refunded_electronic?: string; refunded_electronic_pending?: string };
/** Post-venta (0027): returns of the window (by return date). sales_total stays GROSS. Absent on reports frozen before 0027. */
export type ReturnsBlock = { gross_sales: string; returns_total: string; net_sales: string; refunds_cash: string; refunds_electronic: string; refunds_electronic_pending: string; return_count: number; void_count: number };
export type DailyReportListItem = { id: string; cash_session_id: string; generated_at: string; opened_at: string; closed_at: string; sales_total: string; cash_total: string; electronic_total: string; tickets: number; average_ticket: string; gross_profit: string; gross_margin: string; closing_expected: string; closing_actual: string; closing_difference: string };
export type DailyReportOut = DailyReportListItem & { gross_cost: string; report_data: DailyReportData };
export type MonthlyReport = { year: number; month: number; days: number; sales_total: string; tickets: number; average_ticket: string; cash_total: string; electronic_total: string; gross_cost: string; gross_profit: string; gross_margin: string; categories: { code: string; name: string; cash: string; electronic: string; total: string }[]; payment_channels: DailyReportChannel[]; products: DailyReportProduct[]; hourly: DailyReportHour[]; highlights: DailyReportHighlights; days_breakdown: { cash_session_id: string; daily_report_id: string; date: string; sales_total: string; tickets: number }[] };
export type CurrentReportStatus = "OPEN" | "CLOSED" | "NONE";
export type CurrentReport = { status: CurrentReportStatus; cash_session_id: string | null; daily_report_id: string | null; generated_at: string | null; opened_at: string | null; closed_at: string | null; sales_total: string; cash_total: string; electronic_total: string; tickets: number; average_ticket: string; gross_cost: string; gross_profit: string; gross_margin: string; closing_expected: string | null; closing_actual: string | null; closing_difference: string | null; report_data: DailyReportData };

export type CountRowStatus = "CHANGE" | "UNCHANGED" | "SKIPPED" | "ERROR";
export type CountRow = { row_number:number; source_barcode:string; source_product_id?:string|null; product_id:string|null; barcode:string|null; name:string|null; current_stock:string|null; physical_count:number|null; delta:string|null; status:CountRowStatus; errors:string[] };
export type CountSummary = { file_rows:number; with_difference:number; unchanged:number; skipped_blank:number; errors:number; positive_units:string; negative_units:string; net_units:string; active_products_not_in_file:number };
export type CountPreview = { batch_id:string; session_open:boolean; can_confirm:boolean; summary:CountSummary; rows:CountRow[] };
export type CountConfirmBody = { batch_id:string; rows:{ product_id:string; expected_current_stock:string; physical_count:number }[] };
export type CountConfirmResult = { batch_id:string; batch_label:string; reason:string; movements_created:number; positive_units:string; negative_units:string; net_units:string };

/** Electronic-invoicing control panel (read-mostly). display states are derived by the backend. */
export type InvoiceDisplayState = "PENDING" | "BLOCKED" | "HELD" | "PROCESSING" | "DRAFT" | "SENT" | "ACCEPTED" | "DIAN_REJECTED" | "REJECTED" | "ERROR";
export type InvoicingScope = "session" | "day" | "all";
export type InvoicingMode = { automatic: boolean; label: string };
export type InvoicingSummary = { date_from: string | null; date_to: string | null; counts: Record<InvoiceDisplayState, number>; completed_sales: number; sales_without_invoice: number };
export type InvoiceRow = { id: string; sale_id: string; sale_number: string; completed_at: string; customer: string; total: string; display_status: InvoiceDisplayState; provider_number: string | null; reason: string | null };
export type InvoicePage = { items: InvoiceRow[]; total: number; page: number; page_size: number; total_pages: number };
export type InvoiceReason = { code: string; message: string; detail: string | null };
export type InvoiceDetail = {
  id: string; sale_id: string; sale_number: string; completed_at: string; source: string; customer: string; customer_snapshot: InvoiceCustomerSnapshot | null; total: string;
  items: { product_name: string; quantity: string; unit_price: string; line_total: string }[];
  payments: { channel_name: string; amount: string; card_type: CardType | null }[];
  status: ElectronicInvoiceStatus; display_status: InvoiceDisplayState; provider_status: string | null; legal_status: string | null; cufe: string | null;
  provider_invoice_id: string | null; provider_number: string | null; attempt_count: number; last_attempt_at: string | null; needs_reconciliation: boolean;
  error_code: string | null; error_message: string | null; reasons: InvoiceReason[]; has_pdf: boolean; has_xml: boolean; retryable: boolean;
};
export type ReadinessCheck = { code: string; ok: boolean; blocking: boolean; message: string; detail: string | null };
export type CutoverReadiness = { status: "READY_FOR_CUTOVER" | "NOT_READY"; auto_issue: boolean; checks: ReadinessCheck[]; reasons: string[]; checked_at: string };
export type BlockedSales = { completed_sales: number; groups: { READY: number; UNMAPPED_PRODUCT: number; MANUAL: number; PAYMENT_METHOD: number; OTHER: number }; blocked: number; already_invoiced: number; held: number };
export type CatalogCoverage = { active: number; mapped: number; unmapped: number; unmapped_invalid_barcode: number; unmapped_valid_barcode: number; mapped_pct: number };
export type UnmappedProductPage = { items: { id: string; barcode: string | null; name: string; sale_price: string; valid_barcode: boolean }[]; total: number; page: number; page_size: number; total_pages: number };
export type CoverageBlock = { total: string | number; mapped: string | number; unmapped: string | number; manual_covered: string | number; manual_uncovered: string | number; pct: number | null };
export type SalesCoverage = {
  since: string | null; excluded: { before_catalog: number; inactive_product: number; total_completed: number };
  sales: number; sales_fully_invoiceable: number; sales_invoiceable_pct: number | null;
  catalog: { active: number; mapped: number; pct: number | null };
  products: { distinct_sold: number; distinct_sold_mapped: number; pct: number | null };
  lines: CoverageBlock; value: CoverageBlock;
  by_session: { cash_session_id: string; opened_at: string; closed_at: string | null; sales: number; lines: number; lines_pct: number | null; value_pct: number | null }[];
  top_unmapped: { product_id: string; barcode: string; name: string; times_sold: number; lines: number; units_sold: string; revenue: string }[];
  pareto: { target_pct: number; products_needed: number | null; reachable: boolean }[];
};
export type CutoverReport = {
  technical: { pass: boolean; reasons: string[] };
  catalog: SalesCoverage;
  payments: { cases: { case: string; ready: boolean; method: string | null; note?: string }[]; real_sales: number; real_sales_blocked_by_payment: Record<string, number> };
  manual: { granel: { ready: boolean; item_id: string | null }; bulto: { ready: boolean; item_id: string | null } };
  operational: { cash: "OPEN" | "CLOSED"; note: string | null; auto_issue: boolean };
};

/** POS <-> Alegra catalog sync (AlegraCatalogSyncService). */
export type CatalogSyncStatus = "ALREADY_LINKED" | "EXACT_MATCH" | "UPC_EAN_MATCH" | "READY_TO_CREATE" | "NO_BARCODE" | "REVIEW_REQUIRED" | "CONFLICT" | "INVALID_BARCODE" | "ERROR";
export type CatalogSyncRow = { product_id: string; barcode: string | null; product_name: string; sale_price: string; current_alegra_item_id: string | null; matched_alegra_item_id: string | null; matched_alegra_name: string | null; matched_reference: string | null; sync_status: CatalogSyncStatus; confidence: string; reason: string; proposed_action: "NONE" | "LINK" | "CREATE" | "REVIEW"; review_hint: string | null };
export type CatalogSyncSummary = { active: number; linked: number; unlinked: number; exact_match: number; ready_to_create: number; review: number; no_barcode: number; conflicts: number; invalid_barcode: number; errors: number; remote_items: number; alegra_items_without_pos: number };
export type CatalogSyncRunMeta = { id: string; status: "RUNNING" | "DONE" | "FAILED"; started_at: string; finished_at: string | null; remote_items: number | null; error: string | null };
export type CatalogSyncState = { create_enabled: boolean; batch_limit: number; alegra_configured: boolean; latest_run: CatalogSyncRunMeta | null; summary: CatalogSyncSummary | null; summary_run: CatalogSyncRunMeta | null };
export type CatalogSyncPage = { run: CatalogSyncRunMeta | null; summary: CatalogSyncSummary | null; rows: CatalogSyncRow[]; total: number; page: number; total_pages: number };
export type CatalogSyncResult = { action: "LINK" | "CREATE"; dry_run: boolean; results: { product_id: string; status: string; reason: string }[]; summary: Record<string, number> };
export type PriorityPosProduct = { alegra_item_id: string; name: string; sale_price: number; category: string; barcode: null; unit_cost: string; initial_stock: number; status: string; reason: string };

/* Physical audit ("interventoría física"): local walk-through, never writes inventory. */
export type AuditStatus = "OPEN" | "FINISHED";
export type AuditItem = {
  id: string; product_id: string; name: string; barcode: string | null; unit: string; category: string | null;
  quantity: number; counted: boolean; scans: number; system_stock: number; difference: number | null;
  alegra_item_id: string | null; active: boolean; last_seen_at: string;
};
/** A scanned code no Product has: in the store, missing from the POS catalog ("por crear"). */
export type AuditUnknownCode = { barcode: string; status: "UNKNOWN_PRODUCT"; scan_count: number; first_scanned_at: string; last_scanned_at: string };
export type PhysicalAudit = {
  id: string; status: AuditStatus; started_at: string; finished_at: string | null;
  found: number; counted: number; unknown: number; unknown_codes: AuditUnknownCode[]; active_products: number; items: AuditItem[];
};
export type AuditListItem = { id: string; status: AuditStatus; started_at: string; finished_at: string | null; found: number };
export type AuditSection = "present" | "not_found" | "counted" | "without_alegra" | "to_create";
export type AuditReportRow = {
  product_id: string; name: string; barcode: string | null; unit: string; category: string | null;
  system_stock: number; alegra_item_id: string | null; quantity?: number; counted?: boolean;
  difference?: number | null; active?: boolean; last_sale_at?: string | null;
};
export type AuditReport = {
  audit: { id: string; status: AuditStatus; started_at: string; finished_at: string | null };
  summary: Record<AuditSection, number> & { active_products: number; present_inactive: number; counted_with_difference: number };
  sections: Record<Exclude<AuditSection, "to_create">, AuditReportRow[]> & { to_create: AuditUnknownCode[] };
};

// ---- Post-venta (0027): detalle de venta, devoluciones, anulación, correcciones de pago, aclaraciones ----
export type PostSaleAction = { allowed: boolean; reason: string | null };
export type SaleRefund = { id: string; kind: "RETURN" | "EXCHANGE"; exchange_id: string | null; return_id: string | null; payment_id: string | null; channel_id: string; channel_name: string; affects_cash: boolean; amount: string; status: "PENDING" | "COMPLETED"; account_id?: string | null; account_name?: string | null; requires_account?: boolean; created_at: string; confirmed_at: string | null; confirmed_by_text: string | null };
export type PaymentCorrection = { id: string; sequence: number; from_channel_name: string; to_channel_name: string; from_account_name?: string | null; to_account_name?: string | null; amount: string; reason: string; cash_effect: string; performed_by_text: string | null; created_at: string };
export type SaleDetailLine = { id: string; product_id: string | null; product_name: string; category_code: string; manual_kind: string | null; is_granel: boolean; restockable: boolean; quantity: string; unit_price: string; line_total: string; returned_quantity: string; returned_amount: string; exchanged_quantity: string; returnable_quantity: string };
/** Settlement of an exchange: who pays the (signed) difference. Only that difference is money. */
export type ExchangeSettlement = "EVEN" | "CUSTOMER_PAYS" | "STORE_REFUNDS";
export type SaleExchangeRecord = { id: string; number: number; reason_code: string; reason_label: string; reason_text: string | null; returned_credit: string; replacement_total: string; difference: string; settlement: ExchangeSettlement; settlement_amount: string; status: string; created_at: string; performed_by_text: string | null;
  returned: { sale_item_id: string | null; replacement_line_id: string | null; product_name: string | null; origin: string | null; quantity: string; unit_credit: string; line_credit: string; restock: boolean }[];
  replacements: { id: string; product_id: string | null; manual_kind: string | null; product_name: string; quantity: string; unit_price: string; line_total: string; category_code: string; later_consumed_quantity: string }[];
  money: { id: string; direction: "IN" | "OUT"; channel_id: string; channel_name: string; amount: string; affects_cash: boolean; status: "PENDING" | "COMPLETED"; received_amount: string | null; change_amount: string | null; confirmed_at: string | null; account_id?: string | null; account_name?: string | null } | null };
/** A unit the customer can bring back: an original sale line or a unit delivered by an earlier exchange. */
export type SaleUnit = { key: string; source_type: "SALE_ITEM" | "EXCHANGE_LINE"; source_id: string; product_name: string; product_id: string | null; manual_kind: string | null; is_granel: boolean; restockable: boolean; origin: string; quantity: string; unit_price: string; line_total: string; returned_quantity: string; exchanged_quantity: string; returnable_quantity: string };
/** Provenance of a requested unit: exactly one of the two ids. */
export type UnitSource = { sale_item_id?: string; replacement_line_id?: string };
export type ExchangeReplacementInput = { product_id?: string; manual_kind?: "SUPER" | "BULTO" | "PET_OTHER"; description?: string; amount?: number; quantity: number };
export type ExchangePreview = { returned_credit: string; replacement_total: string; difference: string; settlement: ExchangeSettlement; settlement_amount: string;
  returned: { source_type: "SALE_ITEM" | "EXCHANGE_LINE"; source_id: string; product_name: string; origin: string; quantity: string; unit_credit: string; line_credit: string; restock: boolean }[];
  replacements: { product_id: string | null; manual_kind: string | null; product_name: string; quantity: string; unit_price: string; line_total: string }[] };
export type SaleDetailPayment = { id: string; amount: string; original_channel_id: string; original_channel_name: string; effective_channel_id: string; effective_channel_name: string; effective_affects_cash: boolean; original_account_id?: string | null; original_account_name?: string | null; effective_account_id?: string | null; effective_account_name?: string | null; corrected: boolean; card_type: string | null; received_amount: string | null; change_amount: string | null; created_at: string; corrections: PaymentCorrection[] };
export type SaleReturnKind = "PARTIAL" | "TOTAL" | "VOID";
export type SaleReturnRecord = { id: string; kind: SaleReturnKind; reason_code: string; reason_label: string; reason_text: string | null; total_amount: string; performed_by_text: string | null; created_at: string; lines: { sale_item_id: string; quantity: string; amount: string; restock: boolean }[]; refunds: SaleRefund[] };
export type SaleDetail = {
  id: string; number: string; status: "OPEN" | "COMPLETED" | "CANCELLED";
  display_status: "OPEN" | "CANCELLED" | "COMPLETED" | "PARTIALLY_RETURNED" | "RETURNED" | "VOIDED" | "EXCHANGED" | "EXCHANGED_AND_RETURNED"; display_status_label: string;
  created_at: string; completed_at: string | null; cancelled_at: string | null;
  total: string; returned_total: string; net_total: string;
  customer: { id: string; name: string; identification_number: string } | null;
  lines: SaleDetailLine[]; units: SaleUnit[]; external_items: { description: string; quantity: string; amount: string }[];
  payments: SaleDetailPayment[];
  invoice: { id: string; status: string; display_status: string; provider_number: string | null } | null;
  notes: { id: string; text: string; performed_by_text: string | null; created_at: string }[];
  returns: SaleReturnRecord[]; exchanges: SaleExchangeRecord[]; exchange_difference_total: string; pending_refunds: SaleRefund[]; refund_status: "NONE" | "PENDING" | "COMPLETED";
  allowed_actions: { note: PostSaleAction; exchange: PostSaleAction; return: PostSaleAction; void: PostSaleAction; correct_payment: PostSaleAction; correct_payment_cash: PostSaleAction };
};
export type ReturnReason = { code: string; label: string };
