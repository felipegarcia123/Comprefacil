"use client";

import { useCallback, useEffect, useState } from "react";
import { CircleAlert, CircleCheck, Info, LoaderCircle, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { errorText } from "@/lib/format";
import type { BlockedSales, CatalogCoverage, CutoverReadiness } from "@/lib/types";
import { InlineMessage, StatusBadge, TouchButton } from "@/components/ui";
import { CatalogSyncView } from "./CatalogSyncView";
import { CutoverReportView } from "./CutoverReportView";

const GROUPS: { key: keyof BlockedSales["groups"]; label: string }[] = [
  { key: "READY", label: "Listas para facturar" },
  { key: "UNMAPPED_PRODUCT", label: "Con productos nuevos para Alegra (creación automática desactivada)" },
  { key: "MANUAL", label: "Ventas manuales sin ítem configurado" },
  { key: "PAYMENT_METHOD", label: "Medio de pago por revisar" },
  { key: "OTHER", label: "Otros motivos" },
];

/**
 * Pre-cutover checklist. Read-only: it never turns anything on. The cutover itself (with the register closed,
 * then a new jornada) is a separate, explicit operation.
 */
export function ReadinessView() {
  const [readiness, setReadiness] = useState<CutoverReadiness | null>(null);
  const [coverage, setCoverage] = useState<CatalogCoverage | null>(null);
  const [blocked, setBlocked] = useState<BlockedSales | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");

  const check = useCallback(async (remote = false) => {
    setChecking(true); setError("");
    try {
      const [nextReadiness, nextCoverage, nextBlocked] = await Promise.all([api.cutoverReadiness(remote), api.catalogCoverage(), api.blockedSales()]);
      setReadiness(nextReadiness); setCoverage(nextCoverage); setBlocked(nextBlocked);
    } catch (cause) { setError(errorText(cause)); }
    finally { setChecking(false); }
  }, []);
  useEffect(() => { void check(); }, [check]);

  const ready = readiness?.status === "READY_FOR_CUTOVER";
  return (
    <div className="einv-ready">
      {error && <InlineMessage tone="danger" onDismiss={() => setError("")}>{error}</InlineMessage>}

      <section className="einv-card" aria-labelledby="einv-ready-title">
        <div className="einv-card__head">
          <h2 id="einv-ready-title">Preparación para facturación automática</h2>
          {readiness && <StatusBadge tone={ready ? "success" : "warning"}>{ready ? "Lista para el cambio" : "Todavía no"}</StatusBadge>}
        </div>
        <p className="einv-card__hint">El cambio se hace con la caja cerrada, antes de abrir una jornada nueva. Esta revisión no activa nada.</p>
        {!readiness ? <p className="customer-picker__hint" role="status"><LoaderCircle className="ui-btn__spin" size={20} aria-hidden="true" />Revisando…</p> : (
          <ul className="einv-checks">
            {readiness.checks.map(item => {
              const Icon = !item.blocking ? Info : item.ok ? CircleCheck : CircleAlert;
              return (
                <li key={item.code} data-ok={item.ok} data-blocking={item.blocking}>
                  <Icon size={22} aria-hidden="true" />
                  <span>{item.message}{!item.ok && <span className="ui-sr-only"> — pendiente</span>}{item.detail && <small>{item.detail}</small>}</span>
                </li>
              );
            })}
          </ul>
        )}
        <div className="ui-row">
          <TouchButton variant="secondary" icon={RefreshCw} loading={checking} onClick={() => void check(false)}>Revisar de nuevo</TouchButton>
          <TouchButton variant="ghost" loading={checking} onClick={() => void check(true)}>Verificar también en Alegra</TouchButton>
        </div>
      </section>

      <CutoverReportView />

      <section className="einv-card" aria-labelledby="einv-blocked-title">
        <div className="einv-card__head"><h2 id="einv-blocked-title">Ventas de la jornada que no se podrían facturar hoy</h2></div>
        {blocked && (
          <>
            <p className="einv-card__hint">{blocked.completed_sales} ventas completadas · {blocked.blocked} bloqueadas{blocked.held ? ` · ${blocked.held} fuera de emisión` : ""}{blocked.already_invoiced ? ` · ${blocked.already_invoiced} ya facturadas` : ""}</p>
            <ul className="einv-groups">
              {GROUPS.map(group => <li key={group.key}><span>{group.label}</span><b className="ui-num">{blocked.groups[group.key]}</b></li>)}
            </ul>
          </>
        )}
      </section>

      <CatalogSyncView />
    </div>
  );
}
