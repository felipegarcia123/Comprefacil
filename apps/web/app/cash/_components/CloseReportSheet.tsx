"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { dateTime, errorText, money } from "@/lib/format";
import { InlineMessage, Sheet, StatusBadge, TouchButton } from "@/components/ui";
import { Block1Report } from "@/components/reconciliation/Block1Report";
import { ScaleClosingForm } from "@/components/reconciliation/ScaleClosingForm";
import { AccountsReport } from "@/components/accounts/AccountsReconciliation";
import { DeclaredPatrimonyReport, PatrimonyLegend, RegisteredValuationBlock } from "@/components/accounts/PatrimonyBlock";
import type { CashSummary, InventoryValuation, ReconciliationBlock1 } from "@/lib/types";

/** Block 1 + scale, recomputed by the backend from the session's own window (unchanged since 0026). */
function Block1View({ sessionId, allowScaleEdit }: { sessionId: string; allowScaleEdit: boolean }) {
  const [data, setData] = useState<ReconciliationBlock1 | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setData(null); setError("");
    api.reconciliation(sessionId).then(setData).catch(failure => setError(errorText(failure)));
  }, [sessionId]);
  if (error) return <InlineMessage tone="danger">{error}</InlineMessage>;
  if (!data) return <p className="recon-report__note" role="status">Cargando cuadre parcial…</p>;
  return (
    <div className="recon-close">
      <p className="recon-close__when">
        Apertura {dateTime.format(new Date(data.date_from))}
        {data.session_status === "CLOSED" && <> · Cierre {dateTime.format(new Date(data.date_to))}</>}
      </p>
      <Block1Report data={data} />
      {allowScaleEdit && !data.scale && <ScaleClosingForm data={data} onSaved={setData} />}
    </div>
  );
}

function CashBlock({ sessionId }: { sessionId: string }) {
  const [summary, setSummary] = useState<CashSummary | null>(null);
  useEffect(() => { setSummary(null); api.cashSession(sessionId).then(setSummary).catch(() => setSummary(null)); }, [sessionId]);
  const session = summary?.session;
  if (!session || session.closing_actual === null) return null;
  const diff = Number(session.difference ?? 0);
  return (
    <dl className="exchange__totals">
      <div><dt>Esperado</dt><dd className="ui-num">{money.format(Number(session.closing_expected))}</dd></div>
      <div><dt>Contado</dt><dd className="ui-num">{money.format(Number(session.closing_actual))}</dd></div>
      <div><dt>Diferencia</dt><dd><StatusBadge tone={diff < 0 ? "danger" : diff > 0 ? "warning" : "success"}>{diff === 0 ? "Caja cuadrada" : `${diff < 0 ? "Faltante" : "Sobrante"} ${money.format(Math.abs(diff))}`}</StatusBadge></dd></div>
    </dl>
  );
}

function PatrimonyView({ sessionId }: { sessionId: string }) {
  const [data, setData] = useState<InventoryValuation | null>(null);
  useEffect(() => { setData(null); api.sessionInventoryValuation(sessionId).then(setData).catch(() => setData(null)); }, [sessionId]);
  return (
    <div className="ui-stack">
      <PatrimonyLegend />
      <DeclaredPatrimonyReport sessionId={sessionId} />
      {data && <details className="patrimony__diag"><summary className="patrimony__coverage">Ver el valor calculado por el POS (no verificado)</summary><RegisteredValuationBlock data={data} /></details>}
    </div>
  );
}

/**
 * "Cierre de jornada" (right after closing and in Historial): Caja, Báscula / bloque 1, Cuentas and Patrimonio,
 * each with its own numbers — they are never added together (balances are not profit).
 */
export function CloseReportView({ sessionId, allowScaleEdit = true }: { sessionId: string; allowScaleEdit?: boolean }) {
  return (
    <div className="ui-stack">
      <section className="ui-stack" aria-label="Caja"><h3 className="close-report__title">Caja</h3><CashBlock sessionId={sessionId} /></section>
      <section className="ui-stack" aria-label="Báscula y bloque 1"><h3 className="close-report__title">Báscula / Bloque 1</h3><Block1View sessionId={sessionId} allowScaleEdit={allowScaleEdit} /></section>
      <section className="ui-stack" aria-label="Cuentas"><h3 className="close-report__title">Cuentas</h3><AccountsReport sessionId={sessionId} /></section>
      <section className="ui-stack" aria-label="Patrimonio"><h3 className="close-report__title">Patrimonio de mercancía</h3><PatrimonyView sessionId={sessionId} /></section>
    </div>
  );
}

export function CloseReportSheet({ sessionId, onClose }: { sessionId: string | null; onClose: () => void }) {
  return (
    <Sheet open={Boolean(sessionId)} title="Cierre de jornada" module="caja" onClose={onClose}>
      {sessionId && <CloseReportView sessionId={sessionId} />}
      <div className="ui-form__actions"><TouchButton size="lg" module="caja" onClick={onClose}>Listo</TouchButton></div>
    </Sheet>
  );
}
