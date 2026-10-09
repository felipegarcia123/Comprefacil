"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { errorText, time } from "@/lib/format";
import type { CurrentReport } from "@/lib/types";
import { JornadaReport } from "./JornadaReport";

const LIVE_POLL_MS = 15000;

/** OPEN -> live snapshot, polled every 15s + on focus/visibility. CLOSED -> the frozen DailyReport, no polling. NONE -> empty state. Behaviour unchanged from pre-Phase-5. */
export function LatestTab() {
  const [report, setReport] = useState<CurrentReport | null>(null);
  const [error, setError] = useState("");
  const loadingRef = useRef(false);

  async function load() {
    if (loadingRef.current) return;
    loadingRef.current = true;
    try { setReport(await api.currentReport()); setError(""); }
    catch (e) { setError(errorText(e)); }
    finally { loadingRef.current = false; }
  }

  useEffect(() => {
    void load();
    function onVisible() { if (document.visibilityState === "visible") void load(); }
    window.addEventListener("focus", load);
    document.addEventListener("visibilitychange", onVisible);
    return () => { window.removeEventListener("focus", load); document.removeEventListener("visibilitychange", onVisible); };
  }, []);

  useEffect(() => {
    if (report?.status !== "OPEN") return;
    const id = setInterval(() => void load(), LIVE_POLL_MS);
    return () => clearInterval(id);
  }, [report?.status]);

  if (error) return <p className="chart-empty">{error}</p>;
  if (!report) return <p className="chart-empty">Cargando…</p>;
  if (report.status === "NONE") return <p className="chart-empty">Aún no hay jornadas. Abre caja para comenzar.</p>;

  return (
    <div>
      <div className="reports-live-title">
        <h2>{report.status === "OPEN" ? "Jornada actual" : "Última jornada"}</h2>
        {report.status === "OPEN" && report.opened_at && <span className="reports-window">Abierta desde {time.format(new Date(report.opened_at))}</span>}
      </div>
      <JornadaReport report={{ ...report, opened_at: report.opened_at!, closed_at: report.closed_at! }} />
    </div>
  );
}
