import http from "node:http";

const day = new Intl.DateTimeFormat("en-CA", { timeZone: "America/Bogota", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
const reply = {
  answer: "Ventas verificadas: 120 000 pesos. El dato es sintético para esta prueba.",
  tools_used: ["get_sales_summary"],
  periods: [{ date_from: day, date_to: day, timezone: "America/Bogota" }],
  limitations: ["Una devolución pendiente no está incluida en la ganancia."],
  usage: { model_calls: 2, input_tokens: 90, output_tokens: 30, total_tokens: 120 },
  duration_ms: 450,
  ignored_private_field: "mock-private-field-must-not-reach-browser",
};

const conversations = new Map();
const reminders = new Map();
const tomorrow = new Date(Date.parse(`${day}T12:00:00Z`) + 86400000).toISOString().slice(0, 10);

async function bodyOf(request) {
  let body = "";
  for await (const chunk of request) body += chunk;
  return JSON.parse(body || "{}");
}

http.createServer(async (request, response) => {
  response.setHeader("Content-Type", "application/json");
  response.setHeader("Cache-Control", "no-store");
  response.setHeader("Access-Control-Allow-Origin", "http://127.0.0.1:3103");
  response.setHeader("Access-Control-Allow-Headers", "Content-Type");
  if (request.method === "OPTIONS") { response.writeHead(204).end(); return; }
  if (request.url === "/health") { response.writeHead(200).end("{}"); return; }
  if (request.url?.startsWith("/api/v1/notifications")) {
    const token = request.headers.authorization?.replace("Bearer ", "");
    const actor = token === "synthetic-notification-token-merchant-a-32" ? "a"
      : token === "synthetic-notification-token-merchant-b-32" ? "b" : null;
    if (!actor) { response.writeHead(401).end("{}"); return; }
    if (request.url === "/api/v1/notifications/center" && request.method === "GET") {
      response.writeHead(200).end(JSON.stringify({ as_of: day,
        alerts: [{ id: "alert-stock", category: "LOW_STOCK", priority: "HIGH", title: "Stock bajo: arroz",
          detail: "Registrado: 1; mínimo configurado: 2.", href: "/inventory", due_date: null },
          { id: "alert-due", category: "SUPPLIER_DUE", priority: "HIGH", title: "Obligación próxima a vencer",
            detail: `Vencimiento registrado: ${tomorrow}.`, href: "/suppliers", due_date: tomorrow }],
        reminders: [...reminders.values()].filter(item => item.actor === actor && item.status === "PENDING")
          .map(({ actor: _actor, ...item }) => item), alerts_truncated: false, reminders_truncated: false }));
      return;
    }
    if (request.url === "/api/v1/notifications/intent" && request.method === "POST") {
      const body = await bodyOf(request);
      response.writeHead(200).end(JSON.stringify({ approval_token: `synthetic-approval-${actor}-${body.action}-${body.reminder_id ?? "new"}`, expires_in_seconds: 300 }));
      return;
    }
    if (request.url === "/api/v1/notifications/reminders" && request.method === "POST") {
      const body = await bodyOf(request);
      if (!body.approval_token?.startsWith(`synthetic-approval-${actor}-create`)) {
        response.writeHead(403).end("{}"); return;
      }
      const item = { actor, id: body.idempotency_key, title: body.title,
        due_at: new Date(`${body.due_local}:00-05:00`).toISOString(), timezone: "America/Bogota",
        status: "PENDING", created_at: new Date().toISOString() };
      reminders.set(item.id, item);
      const { actor: _actor, ...publicItem } = item;
      response.writeHead(201).end(JSON.stringify(publicItem)); return;
    }
    const resolution = /^\/api\/v1\/notifications\/reminders\/([a-f0-9-]{36})\/(complete|cancel)$/.exec(request.url ?? "");
    if (resolution && request.method === "POST") {
      const body = await bodyOf(request);
      const item = reminders.get(resolution[1]);
      if (!item || item.actor !== actor || !body.approval_token?.startsWith(`synthetic-approval-${actor}-${resolution[2]}`)) {
        response.writeHead(403).end("{}"); return;
      }
      item.status = resolution[2] === "complete" ? "COMPLETED" : "CANCELLED";
      const { actor: _actor, ...publicItem } = item;
      response.writeHead(200).end(JSON.stringify(publicItem)); return;
    }
  }
  if (request.url === "/api/v1/agent/chat" && request.method === "POST") {
    if (request.headers.authorization !== "Bearer synthetic-backend-token-32-chars") {
      response.writeHead(401).end(JSON.stringify({ detail: "Unauthorized" })); return;
    }
    const session = request.headers["x-conversation-session"];
    if (typeof session !== "string" || !/^[a-f0-9]{64}$/.test(session)) {
      response.writeHead(400).end(JSON.stringify({ detail: "Missing server session" })); return;
    }
    let body = "";
    for await (const chunk of request) body += chunk;
    const question = JSON.parse(body).question ?? "";
    const previous = conversations.get(session);
    if (!question.includes("error")) conversations.set(session, question);
    await new Promise(resolve => setTimeout(resolve, 1200));
    if (question.includes("error")) { response.writeHead(502).end(JSON.stringify({ detail: "mock provider error" })); return; }
    response.writeHead(200).end(JSON.stringify({
      ...reply,
      answer: question.includes("seguimiento")
        ? previous ? "Seguimiento del período anterior con datos sintéticos." : "Contexto no disponible."
        : question.includes("inventario") ? "Hay 2 productos con stock bajo en los datos sintéticos." : reply.answer,
      tools_used: question.includes("inventario") ? ["get_low_stock_products"] : reply.tools_used,
    }));
    return;
  }
  if (request.url === "/api/v1/cash/current") { response.writeHead(200).end("null"); return; }
  response.writeHead(404).end(JSON.stringify({ detail: "mock only" }));
}).listen(4313, "127.0.0.1");
