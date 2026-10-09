"use client";

import { useEffect, useState } from "react";
import { useOperationalSession } from "@/components/OperationalSessionProvider";
import { ILLUSTRATIONS } from "@/components/illustrations";
import { InlineMessage, MODULE_ORDER, ModuleCard, StatusBadge, TouchButton, type ModuleId } from "@/components/ui";
import { api } from "@/lib/api";
import { cx } from "@/lib/cx";
import { publishSalesContexts, saleLabel } from "@/lib/workContexts";
import { formatMoney } from "@/lib/format";

type Counts = { sales?: number; purchases?: number };

/**
 * Operational Home: "what do I need to do now?". Pure navigation — six modules, no KPIs.
 * Secondary reads (open sales, draft purchases) only decorate the cards; if they fail, Home keeps working.
 */
export default function HomePage() {
  const { summary, loading, error, refresh } = useOperationalSession();
  const [counts, setCounts] = useState<Counts>({});

  useEffect(() => {
    let alive = true;
    void Promise.allSettled([api.openSales(), api.purchases("DRAFT")]).then(([sales, purchases]) => {
      if (!alive) return;
      if (sales.status === "fulfilled") publishSalesContexts(sales.value.map((sale, index) => ({ id: sale.id, label: saleLabel(index), sub: formatMoney(sale.total), module: "ventas" as const }))); // Home never has an "active" tab to report
      setCounts({
        sales: sales.status === "fulfilled" ? sales.value.length : undefined,
        purchases: purchases.status === "fulfilled" ? purchases.value.length : undefined,
      });
    });
    return () => { alive = false; };
  }, []);

  /** The single place Home shows the jornada state (the global bar omits it on Home). */
  const cajaStatus = loading ? <StatusBadge>Consultando…</StatusBadge>
    : error ? <StatusBadge tone="danger">Sin conexión</StatusBadge>
    : summary ? <StatusBadge tone="success">Abierta</StatusBadge>
    : <StatusBadge tone="warning">Cerrada</StatusBadge>;

  function card(id: ModuleId) {
    const Art = ILLUSTRATIONS[id];
    const common = { module: id, art: <Art />, className: cx("home__slot", `home__slot--${id}`), size: id === "ventas" ? ("feature" as const) : ("standard" as const) };
    if (id === "ventas") return <ModuleCard key={id} {...common} count={counts.sales} countLabel={counts.sales === 1 ? "abierta" : "abiertas"} />;
    if (id === "compras") return <ModuleCard key={id} {...common} count={counts.purchases} countLabel={counts.purchases === 1 ? "borrador" : "borradores"} />;
    if (id === "caja") return <ModuleCard key={id} {...common} meta={cajaStatus} cta={summary ? "Ver caja" : "Abrir caja"} />;
    return <ModuleCard key={id} {...common} />;
  }

  return (
    <main className="home">
      <h1 className="ui-sr-only">Inicio</h1>
      {error && (
        <InlineMessage tone="danger" title="Sin conexión con el servidor" actions={<TouchButton variant="secondary" onClick={() => void refresh()}>Reintentar</TouchButton>}>
          Puedes abrir los módulos, pero no registrar operaciones hasta que vuelva la conexión.
        </InlineMessage>
      )}
      <div className="home__grid">{MODULE_ORDER.map(card)}</div>
    </main>
  );
}
