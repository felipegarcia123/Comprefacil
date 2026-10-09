import { money } from "@/lib/format";

/** Works on plain http (LAN) too, where crypto.randomUUID is not available. One key per form opening:
 *  a double tap or a retry of the same submission never creates a second record. */
export function newKey(): string {
  const random = typeof crypto !== "undefined" && "randomUUID" in crypto && window.isSecureContext
    ? crypto.randomUUID() : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
  return `web-${random}`;
}

export const pesos = (value: string | number) => money.format(Number(value));
