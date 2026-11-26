import { test } from "node:test";
import assert from "node:assert/strict";
import { attentionAnswer, isAttentionQuestion, isNotificationCenter, nextDate,
  reminderCommand } from "../lib/businessNotifications.ts";

test("interpreta mañana a las 8 a. m. en Bogotá sin usar el reloj UTC como fecha comercial", () => {
  const parsed = reminderCommand("Recuérdame revisar el pedido de bebidas mañana a las 8 a. m.",
    new Date("2026-11-26T02:00:00Z"));
  assert.deepEqual(parsed, { title: "revisar el pedido de bebidas", due_local: "2026-11-26T08:00" });
  assert.equal(nextDate("2026-12-31"), "2027-01-01");
  assert.equal(reminderCommand("Recuérdame pagar mañana a las 13 a. m."), null);
});

test("resume pendientes de mañana y valida enlaces comerciales", () => {
  const center = { as_of: "2026-11-26", alerts_truncated: false, reminders_truncated: false,
    alerts: [{ id: "one", category: "SUPPLIER_DUE", priority: "HIGH", title: "Vence mañana",
      detail: "Fecha registrada: 2026-11-27.", href: "/suppliers", due_date: "2026-11-27" }],
    reminders: [{ id: "two", title: "Revisar pedido", due_at: "2026-11-27T13:00:00Z",
      timezone: "America/Bogota", status: "PENDING", created_at: "2026-11-26T12:00:00Z" }] };
  assert.equal(isNotificationCenter(center), true);
  assert.equal(isAttentionQuestion("¿Qué tengo pendiente para mañana?"), true);
  assert.match(attentionAnswer(center, "¿Qué tengo pendiente para mañana?"), /Vence mañana/);
  assert.match(attentionAnswer(center, "¿Qué tengo pendiente para mañana?"), /Revisar pedido/);
  assert.equal(isNotificationCenter({ ...center, alerts: [{ ...center.alerts[0], href: "javascript:bad" }] }), false);
});
