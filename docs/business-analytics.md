# Business analytics read layer (Sprint 2)

`PosBusinessAnalytics` in `apps/api/app/services/business_analytics.py` implements the
`BusinessAnalyticsSource` protocol. Its Pydantic return contracts live in
`apps/api/app/schemas/business_analytics.py`. A future agent tool can depend on
the protocol and receive another store adapter without changing the contracts.
There is no HTTP endpoint, LLM integration, migration, or write path in this ticket.

Pass a caller-owned SQLAlchemy `Session` to the adapter. For a coherent multi-tool
report under concurrent POS activity, the caller should use a **read-only,
repeatable-read transaction**. The adapter never commits or changes session settings.

```python
analytics = PosBusinessAnalytics(db)
result = analytics.sales_summary(date(2026, 11, 24), date(2026, 11, 24))
```

Date ranges are inclusive calendar dates in `America/Bogota`. Completed sales
are assigned by `completed_at`; returns by their own `created_at`. Open and
cancelled sales are excluded. `net_sales` follows the existing dashboard rule:
completed sales less **completed refunds** in the selected period. Registered
returns and pending electronic refunds are separate fields. A return can belong
to a different period from its original sale, so a negative period net is valid.

`gross_profit` uses the historical `SaleItem.unit_cost` snapshot, not today's
product cost. It reports `insufficient_data` and `null` cost/profit/margin if
the period contains returns or exchanges, a manual/uncosted sale line, a sale
whose total differs from its line totals, or non-positive revenue. No FIFO,
tax-adjusted profit, or cost of a returned item is inferred. It is gross
merchandise profit, not business net income.

`top_products` ranks catalog products by completed-sale units minus original
sale-line returns registered in the period. Manual lines are counted as
excluded; returns of exchange replacement lines cause `insufficient_data`.
`low_stock` follows the inventory screen's rule: active products with **known
positive ledger stock** at or below `minimum_stock`. Zero-stock products are
reported separately in `inventory_summary`; products with no ledger entries are
`untracked`, not assumed physically empty. `registered_stock_value` reuses the
POS valuation diagnostic and is never marked as verified patrimony.

Supplier obligations and the delivery agenda delegate to the existing purchase
services. Scheduled amounts are estimates; received unpaid amounts are actual
obligations. The legacy agenda returns at most 200 orders. This adapter includes
`total_matching`, `truncated`, and `insufficient_data` so a tool cannot silently
present that page as complete. The underlying UI/API limit remains unchanged.

Every result has a `state`: `no_data` means no relevant records, `available`
includes legitimate zero values, and `insufficient_data` means a number cannot
be trusted or a list is incomplete. Consumers should retain the reasons and
coverage fields instead of replacing `null` with zero.

Tests: `apps/api/tests/test_business_analytics.py` uses only synthetic SQLite
data, including UTC/Bogota midnight, cancellations, returns, missing cost,
ledger stock, supplier commitments, and the 200-order boundary.
