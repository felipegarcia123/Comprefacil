/** Centralised formatting helpers (es-CO). Import these instead of creating Intl formatters per page. */

/** COP without decimals. Kept as an Intl instance so `money.format(n)` stays a drop-in for legacy pages. */
export const money = new Intl.NumberFormat("es-CO", { style: "currency", currency: "COP", maximumFractionDigits: 0 });
export const preciseMoney = new Intl.NumberFormat("es-CO", { style: "currency", currency: "COP", maximumFractionDigits: 2 });
export const dateTime = new Intl.DateTimeFormat("es-CO", { dateStyle: "medium", timeStyle: "short" });
export const dateTimeShort = new Intl.DateTimeFormat("es-CO", { dateStyle: "short", timeStyle: "short" });
export const dateMedium = new Intl.DateTimeFormat("es-CO", { dateStyle: "medium" });
export const dateFull = new Intl.DateTimeFormat("es-CO", { dateStyle: "full" });
export const time = new Intl.DateTimeFormat("es-CO", { hour: "numeric", minute: "2-digit" });

/** Money from an untyped value (API decimals arrive as strings). */
export const formatMoney = (value: unknown) => money.format(Number(value));
/** Plain number with up to 2 decimals. */
export const formatNumber = (value: unknown) => new Intl.NumberFormat("es-CO", { maximumFractionDigits: 2 }).format(Number(value));
/** Ledger quantities: up to 3 decimals (products can be sold/counted in fractional units). */
export const formatQuantity = (value: unknown) => new Intl.NumberFormat("es-CO", { maximumFractionDigits: 3 }).format(Number(value));
/** "+12" / "-3" — always shows the sign for a signed movement/delta. */
export const signed = (value: number) => `${value > 0 ? "+" : ""}${new Intl.NumberFormat("es-CO", { maximumFractionDigits: 3 }).format(value)}`;

export const DEFAULT_ERROR = "Ocurrió un error";

/** User-facing message for a caught error. */
export function errorText(error: unknown, fallback: string = DEFAULT_ERROR): string {
  return error instanceof Error ? error.message : fallback;
}
