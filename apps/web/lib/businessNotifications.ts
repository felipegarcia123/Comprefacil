export type BusinessAlert = {
  id: string;
  category: "LOW_STOCK" | "SUPPLIER_DUE" | "PURCHASE_DELIVERY" | "OVERDUE_OBLIGATION";
  priority: "URGENT" | "HIGH" | "NORMAL";
  title: string;
  detail: string;
  href: string;
  due_date: string | null;
};

export type PersonalReminder = {
  id: string;
  title: string;
  due_at: string;
  timezone: "America/Bogota";
  status: "PENDING" | "COMPLETED" | "CANCELLED";
  created_at: string;
};

export type NotificationCenter = {
  as_of: string;
  alerts: BusinessAlert[];
  reminders: PersonalReminder[];
  alerts_truncated: boolean;
  reminders_truncated: boolean;
};

export type ReminderDraft = { title: string; due_local: string; idempotency_key: string };

export const ALERT_LABELS: Record<BusinessAlert["category"], string> = {
  LOW_STOCK: "Inventario bajo",
  SUPPLIER_DUE: "Próximo vencimiento",
  PURCHASE_DELIVERY: "Entrega programada",
  OVERDUE_OBLIGATION: "Obligación vencida",
};

export const PRIORITY_LABELS: Record<BusinessAlert["priority"], string> = {
  URGENT: "Urgente", HIGH: "Alta", NORMAL: "Normal",
};

export function isNotificationCenter(value: unknown): value is NotificationCenter {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  return typeof item.as_of === "string" && /^\d{4}-\d{2}-\d{2}$/.test(item.as_of)
    && typeof item.alerts_truncated === "boolean" && typeof item.reminders_truncated === "boolean"
    && Array.isArray(item.alerts) && item.alerts.length <= 400
    && item.alerts.every(value => {
      if (!value || typeof value !== "object") return false;
      const alert = value as Record<string, unknown>;
      return typeof alert.id === "string" && typeof alert.title === "string"
        && typeof alert.detail === "string" && typeof alert.href === "string"
        && ["/inventory", "/purchases", "/suppliers"].includes(alert.href)
        && typeof alert.category === "string" && alert.category in ALERT_LABELS
        && typeof alert.priority === "string" && alert.priority in PRIORITY_LABELS;
    })
    && Array.isArray(item.reminders) && item.reminders.length <= 100
    && item.reminders.every(value => {
      if (!value || typeof value !== "object") return false;
      const reminder = value as Record<string, unknown>;
      return typeof reminder.id === "string" && typeof reminder.title === "string"
        && typeof reminder.due_at === "string" && reminder.timezone === "America/Bogota"
        && reminder.status === "PENDING";
    });
}

export function bogotaDate(now = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Bogota", year: "numeric", month: "2-digit", day: "2-digit",
  }).format(now);
}

export function nextDate(day: string): string {
  return new Date(Date.parse(`${day}T12:00:00Z`) + 86400000).toISOString().slice(0, 10);
}

export function reminderCommand(text: string, now = new Date()): { title: string; due_local: string } | null {
  const match = /^\s*recu[eé]rdame\s+(.+?)\s+ma[nñ]ana\s+a\s+las\s+(\d{1,2})(?::(\d{2}))?\s*(a\.?\s*m\.?|p\.?\s*m\.?)\s*\.?\s*$/i.exec(text);
  if (!match) return null;
  let hour = Number(match[2]);
  const minute = Number(match[3] ?? "0");
  if (hour < 1 || hour > 12 || minute > 59) return null;
  if (/^p/i.test(match[4]) && hour < 12) hour += 12;
  if (/^a/i.test(match[4]) && hour === 12) hour = 0;
  return { title: match[1].trim(), due_local: `${nextDate(bogotaDate(now))}T${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}` };
}

export function isReminderRequest(text: string): boolean { return /^\s*recu[eé]rdame\b/i.test(text); }
export function isAttentionQuestion(text: string): boolean {
  return /\b(?:pendiente|pendientes|atenci[oó]n|alertas?|recordatorios?)\b/i.test(text);
}

export function attentionAnswer(center: NotificationCenter, question: string): string {
  const tomorrow = /\bma[nñ]ana\b/i.test(question);
  const day = nextDate(center.as_of);
  const alerts = tomorrow ? center.alerts.filter(item => item.due_date === day) : center.alerts;
  const reminders = tomorrow ? center.reminders.filter(item => bogotaDate(new Date(item.due_at)) === day) : center.reminders;
  const heading = tomorrow ? `Para mañana, ${day}:` : "Situaciones pendientes:";
  const lines = [heading, ...alerts.slice(0, 8).map(item => `• ${item.title}: ${item.detail}`),
    ...reminders.slice(0, 8).map(item => `• Recordatorio: ${item.title}.`)];
  if (alerts.length + reminders.length === 0) lines.push("No hay alertas ni recordatorios pendientes para ese período.");
  if (alerts.length + reminders.length > 16 || center.alerts_truncated || center.reminders_truncated)
    lines.push("El centro muestra una selección; consulta la lista para ver lo disponible.");
  return lines.join("\n");
}
