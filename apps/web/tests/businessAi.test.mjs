import { test } from "node:test";
import assert from "node:assert/strict";
import { bogotaToday, isAgentReply, suggestedQuestions } from "../lib/businessAi.ts";

test("fecha comercial de Bogotá alrededor del cambio de día UTC", () => {
  assert.equal(bogotaToday(new Date("2026-11-25T02:00:00Z")), "2026-11-24");
  assert.equal(bogotaToday(new Date("2026-11-25T05:00:00Z")), "2026-11-25");
  assert.equal(suggestedQuestions("2026-11-24").length, 5);
});

test("solo acepta el contrato estructurado del backend", () => {
  const reply = { answer: "Dato sintético", tools_used: ["get_sales_summary"],
    periods: [{ date_from: "2026-11-24", date_to: "2026-11-24", timezone: "America/Bogota" }],
    limitations: [], usage: { model_calls: 2, input_tokens: 10, output_tokens: 5, total_tokens: 15 }, duration_ms: 20 };
  assert.equal(isAgentReply(reply), true);
  assert.equal(isAgentReply({ ...reply, tools_used: ["execute_sql"] }), false);
  assert.equal(isAgentReply({ ...reply, usage: { ...reply.usage, total_tokens: -1 } }), false);
  assert.equal(isAgentReply({ ...reply, answer: 123 }), false);
});
