"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";
import { Toldo } from "@/components/ui";
import { api } from "@/lib/api";
import { requestActivateSale, publishSalesContexts, saleLabel, useWorkContexts } from "@/lib/workContexts";

/**
 * Global "what's in progress" strip — bridges modules (Venta ⇄ Compra); it does not replace
 * the AppHeader's module navigation or Sales' own per-sale tabs. To avoid showing the same
 * thing three times (CLAUDE.md, section 20), each screen's own dedicated UI hides its OWN
 * contexts here: Sales already has its per-sale TouchTabs, so while on /sales this bar shows
 * only the purchase chip (if any); on /purchases it shows only the sale chips. Everywhere else
 * it shows both. It is hidden outright unless at least two contexts exist in total — a single
 * ordinary sale is the normal idle state and earns no bar.
 */
export function ContextBar() {
  const pathname = usePathname();
  const { sales, activeSaleId, purchase } = useWorkContexts();

  // Mounted once for the whole session (it lives in the root layout, outside route changes): if the
  // user's very first screen is Compras (or anything else that isn't Home/Sales), seed the sales list
  // once from the backend so the bar is correct even before Home or Sales has had a chance to publish it.
  // `seeded` guards against React's dev-mode double-invoke of this effect firing the request twice.
  const seeded = useRef(false);
  useEffect(() => {
    if (pathname === "/sales" || seeded.current) return; // /sales publishes its own list (with the active tab) right away
    seeded.current = true;
    void api.openSales().then(list => publishSalesContexts(list.map((sale, index) => ({ id: sale.id, label: saleLabel(index), sub: `$${Number(sale.total).toLocaleString("es-CO")}`, module: "ventas" as const })))).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const showSales = pathname !== "/sales";
  const showPurchase = pathname !== "/purchases" && Boolean(purchase);
  const visible = sales.length + (purchase ? 1 : 0) >= 2 && (showSales || showPurchase);

  // The Sales/Purchases full-height screens size themselves as (viewport - chrome); they need to
  // know whether this bar is actually taking up space. See --chrome-h in shell.css.
  useEffect(() => {
    document.body.classList.toggle("has-context-bar", visible);
    return () => document.body.classList.remove("has-context-bar");
  }, [visible]);

  if (!visible) return null;

  function onSaleClick(id: string, event: React.MouseEvent) {
    if (pathname === "/sales") { event.preventDefault(); requestActivateSale(id); }
    // else: plain navigation — /sales restores this exact tab itself from sessionStorage on mount.
  }

  return (
    <nav className="ui-context-bar" aria-label="Trabajos activos">
      <ul className="ui-context-bar__list">
        {showSales && sales.map(sale => (
          <li key={sale.id}>
            <Link
              href="/sales"
              className="ui-context-chip"
              data-module={sale.module}
              aria-current={pathname === "/sales" && sale.id === activeSaleId ? "page" : undefined}
              onClick={event => onSaleClick(sale.id, event)}
            >
              <Toldo module={sale.module} />
              <span className="ui-context-chip__body">
                <span>{sale.label}</span>
                {sale.sub && <small className="ui-num">{sale.sub}</small>}
              </span>
            </Link>
          </li>
        ))}
        {showPurchase && purchase && (
          <li>
            <Link href="/purchases" className="ui-context-chip ui-context-chip--single" data-module={purchase.module} title={purchase.label}>
              <Toldo module={purchase.module} />
              <span className="ui-context-chip__body"><span>{purchase.label}</span></span>
            </Link>
          </li>
        )}
      </ul>
    </nav>
  );
}
