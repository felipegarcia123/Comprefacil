export type AgentReply = {
  answer: string;
  tools_used: string[];
  periods: { date_from: string; date_to: string; timezone: string }[];
  limitations: string[];
  usage: { model_calls: number; input_tokens: number; output_tokens: number; total_tokens: number };
  duration_ms: number;
};

export const TOOL_LABELS: Record<string, string> = {
  get_sales_summary: "Ventas",
  get_profit_summary: "Rentabilidad",
  get_top_products: "Productos más vendidos",
  get_low_stock_products: "Stock bajo",
  get_inventory_summary: "Inventario",
  get_supplier_commitments: "Obligaciones con proveedores",
  get_purchase_agenda: "Agenda de compras",
};

export function bogotaToday(now = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Bogota", year: "numeric", month: "2-digit", day: "2-digit",
  }).format(now);
}

export function suggestedQuestions(day: string) {
  return [
    { title: "Ventas", question: `¿Cuánto vendimos el ${day}?` },
    { title: "Rentabilidad", question: `¿Cuál fue la ganancia bruta el ${day} y qué limitaciones tiene el cálculo?` },
    { title: "Inventario", question: "¿Qué productos tienen stock bajo?" },
    { title: "Productos", question: `¿Cuáles fueron los productos más vendidos el ${day}?` },
    { title: "Proveedores", question: `¿Qué obligaciones con proveedores hay entre ${day} y ${day}?` },
  ];
}

export function isAgentReply(value: unknown): value is AgentReply {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  const usage = item.usage as Record<string, unknown> | undefined;
  return typeof item.answer === "string" && item.answer.length <= 2400
    && Array.isArray(item.tools_used) && item.tools_used.length <= 7
    && item.tools_used.every(v => typeof v === "string" && v in TOOL_LABELS)
    && Array.isArray(item.periods) && item.periods.length <= 7 && item.periods.every(v => {
      if (!v || typeof v !== "object") return false;
      const p = v as Record<string, unknown>;
      return typeof p.date_from === "string" && /^\d{4}-\d{2}-\d{2}$/.test(p.date_from)
        && typeof p.date_to === "string" && /^\d{4}-\d{2}-\d{2}$/.test(p.date_to)
        && p.timezone === "America/Bogota";
    })
    && Array.isArray(item.limitations) && item.limitations.length <= 20
    && item.limitations.every(v => typeof v === "string" && v.length <= 500)
    && !!usage && ["model_calls", "input_tokens", "output_tokens", "total_tokens"].every(k =>
      Number.isSafeInteger(usage[k]) && (usage[k] as number) >= 0)
    && typeof item.duration_ms === "number" && Number.isFinite(item.duration_ms) && item.duration_ms >= 0;
}
