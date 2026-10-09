"use client";

import { useSyncExternalStore } from "react";
import type { ModuleId } from "@/components/ui/modules";

/**
 * Cross-module "what's in progress" registry (Phase 3). Screens publish here; the global
 * ContextBar and AppHeader read from here. The backend stays the source of truth for the
 * data itself (open Sales, DRAFT Purchase) — this store only holds the small, derived,
 * UI-facing summary needed to render chips without every consumer re-fetching.
 */
export type WorkContext = { id: string; label: string; sub?: string; module: ModuleId };

/** Same numbering convention used by the Sales screen and Home, so a sale is called the same thing everywhere. */
export const saleLabel = (index: number) => `Venta ${index + 1}`;

type State = { sales: WorkContext[]; activeSaleId: string; purchase: WorkContext | null };
let state: State = { sales: [], activeSaleId: "", purchase: null };
// Referentially stable across calls — useSyncExternalStore's getServerSnapshot must return the
// same value when nothing changed, or React logs "should be cached" and re-renders needlessly.
const SERVER_SNAPSHOT: State = { sales: [], activeSaleId: "", purchase: null };
const listeners = new Set<() => void>();
const notify = () => listeners.forEach(listener => listener());
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };

/** Called by the Sales screen (and Home, on read-only mount) whenever its open-sales list changes. `activeSaleId` (default "") lets the bar highlight which tab is currently selected while on /sales; Home never has one to give. */
export function publishSalesContexts(sales: WorkContext[], activeSaleId = "") {
  state = { ...state, sales, activeSaleId };
  notify();
}

/** Called by the Purchases screen: the one DRAFT currently loaded in its workspace, or null. Never a RECEIVED/CANCELLED purchase. */
export function publishPurchaseContext(purchase: WorkContext | null) {
  state = { ...state, purchase };
  notify();
}

export function useWorkContexts(): State {
  return useSyncExternalStore(subscribe, () => state, () => SERVER_SNAPSHOT);
}

export function useOpenSalesCount(): number {
  return useWorkContexts().sales.length;
}

/** Lets a ContextBar chip switch the ALREADY-mounted Sales screen to a specific tab without a full navigation. */
const activateListeners = new Set<(saleId: string) => void>();
export function requestActivateSale(saleId: string) {
  activateListeners.forEach(listener => listener(saleId));
}
export function onActivateSale(listener: (saleId: string) => void) {
  activateListeners.add(listener);
  return () => { activateListeners.delete(listener); };
}
