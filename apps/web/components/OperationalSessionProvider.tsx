"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { CashSummary } from "@/lib/types";

/** `error` = the jornada state could not be read (server unreachable); it does NOT mean the jornada is closed. */
type OperationalSessionState = { summary: CashSummary | null; loading: boolean; error: boolean; refresh: () => Promise<void> };
const OperationalSessionContext = createContext<OperationalSessionState | null>(null);

export function OperationalSessionProvider({ children }: { children: React.ReactNode }) {
  const [summary, setSummary] = useState<CashSummary | null>(null); const [loading, setLoading] = useState(true); const [error, setError] = useState(false);
  // Prevents an overlapping duplicate call — mainly React's dev-mode double-invoke of this effect
  // on mount (section 21/22), but also a safety net if `operational-session-changed` fires twice
  // in quick succession; a call already in flight is never abandoned, so no update is ever lost.
  const inFlight = useRef(false);
  const refresh = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try { setSummary(await api.currentCash()); setError(false); }
    catch { setError(true); }
    finally { setLoading(false); inFlight.current = false; }
  }, []);
  useEffect(() => { void refresh(); window.addEventListener("operational-session-changed", refresh); return () => window.removeEventListener("operational-session-changed", refresh); }, [refresh]);
  return <OperationalSessionContext.Provider value={{summary, loading, error, refresh}}>{children}</OperationalSessionContext.Provider>;
}
export function useOperationalSession() { const context = useContext(OperationalSessionContext); if (!context) throw new Error("OperationalSessionProvider requerido"); return context; }
