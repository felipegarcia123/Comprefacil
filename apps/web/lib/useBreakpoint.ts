"use client";

import { useEffect, useState } from "react";

/** Layout tiers from CLAUDE.md: movil <640, tablet 640–899, laptop 900–1199, pos >=1200. */
export type Breakpoint = "movil" | "tablet" | "laptop" | "pos";

const QUERIES: [Breakpoint, string][] = [
  ["pos", "(min-width: 1200px)"],
  ["laptop", "(min-width: 900px)"],
  ["tablet", "(min-width: 640px)"],
];

function current(): Breakpoint {
  for (const [name, query] of QUERIES) if (window.matchMedia(query).matches) return name;
  return "movil";
}

/**
 * Use only to change BEHAVIOUR (e.g. keypad panel vs. drawer). Layout must be done in CSS.
 * Renders "pos" on the server/first paint to avoid hydration mismatch, then syncs.
 */
export function useBreakpoint(): Breakpoint {
  const [value, setValue] = useState<Breakpoint>("pos");
  useEffect(() => {
    const lists = QUERIES.map(([, query]) => window.matchMedia(query));
    const update = () => setValue(current());
    update();
    lists.forEach(list => list.addEventListener("change", update));
    return () => lists.forEach(list => list.removeEventListener("change", update));
  }, []);
  return value;
}
