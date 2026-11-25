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

http.createServer(async (request, response) => {
  response.setHeader("Content-Type", "application/json");
  response.setHeader("Cache-Control", "no-store");
  response.setHeader("Access-Control-Allow-Origin", "http://127.0.0.1:3103");
  response.setHeader("Access-Control-Allow-Headers", "Content-Type");
  if (request.method === "OPTIONS") { response.writeHead(204).end(); return; }
  if (request.url === "/health") { response.writeHead(200).end("{}"); return; }
  if (request.url === "/api/v1/agent/chat" && request.method === "POST") {
    if (request.headers.authorization !== "Bearer synthetic-backend-token-32-chars") {
      response.writeHead(401).end(JSON.stringify({ detail: "Unauthorized" })); return;
    }
    let body = "";
    for await (const chunk of request) body += chunk;
    const question = JSON.parse(body).question ?? "";
    await new Promise(resolve => setTimeout(resolve, 1200));
    if (question.includes("error")) { response.writeHead(502).end(JSON.stringify({ detail: "mock provider error" })); return; }
    response.writeHead(200).end(JSON.stringify({
      ...reply,
      answer: question.includes("inventario") ? "Hay 2 productos con stock bajo en los datos sintéticos." : reply.answer,
      tools_used: question.includes("inventario") ? ["get_low_stock_products"] : reply.tools_used,
    }));
    return;
  }
  if (request.url === "/api/v1/cash/current") { response.writeHead(200).end("null"); return; }
  response.writeHead(404).end(JSON.stringify({ detail: "mock only" }));
}).listen(4313, "127.0.0.1");
