"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Block1Report } from "@/components/reconciliation/Block1Report";
import { ScaleClosingForm } from "@/components/reconciliation/ScaleClosingForm";
import type { ReconciliationBlock1 } from "@/lib/types";

/** Resumen operativo: the day's partial reconciliation (block 1) with the scale closing form. */
export function ReconciliationPanel({ refreshKey }: { refreshKey: number }) {
  const [data, setData] = useState<ReconciliationBlock1 | null>(null);
  useEffect(() => { api.reconciliation().then(setData).catch(() => setData(null)); }, [refreshKey]);
  if (!data) return null;
  return (
    <section className="operations-panel recon" aria-label="Cuadre parcial de la jornada">
      <header className="operations-panel__head"><h2>Cuadre parcial</h2></header>
      <ScaleClosingForm data={data} onSaved={setData} />
      <Block1Report data={data} showScale={false} />
    </section>
  );
}
