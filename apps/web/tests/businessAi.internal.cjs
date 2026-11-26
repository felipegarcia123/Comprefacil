"use strict";

const { chromium } = require("playwright");

async function main() {
  const browser = await chromium.launch({ headless: true, args: ["--disable-background-networking"] });
  try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await context.route("**/*", route => {
    const host = new URL(route.request().url()).hostname;
    return host === "comps2004-web" ? route.continue() : route.abort();
  });
  await page.goto("http://comps2004-web:3104/business-ai", { waitUntil: "networkidle" });
  await page.getByLabel("Código de acceso").fill("synthetic-ui-access-code-32-characters");
  await page.getByRole("button", { name: "Ingresar" }).click();
  await page.getByLabel("Escribe tu pregunta").fill("Ventas del 24 de noviembre de 2026?");
  const firstResponse = page.waitForResponse(response => response.url().endsWith("/api/business-ai/chat"));
  await page.getByRole("button", { name: "Enviar pregunta" }).click();
  const firstStatus = await firstResponse;
  if (!firstStatus.ok()) throw new Error(`Synthetic chat HTTP ${firstStatus.status()}: ${await firstStatus.text()}`);
  await page.getByText("Venta sintética verificada: 400 pesos.").waitFor();
  await page.getByLabel("Escribe tu pregunta").fill("seguimiento de ese período");
  await page.getByRole("button", { name: "Enviar pregunta" }).click();
  await page.getByText("Seguimiento del período anterior con datos sintéticos.").waitFor();
  await page.getByRole("button", { name: "Nueva conversación" }).click();
  await page.locator(".ai-message").first().waitFor({ state: "detached" });
  await page.getByLabel("Escribe tu pregunta").fill("seguimiento de ese período");
  await page.getByRole("button", { name: "Enviar pregunta" }).click();
  await page.getByText("Contexto no disponible.").waitFor();
  if (errors.length) throw new Error(`Browser errors: ${errors.join(" | ")}`);
  await page.screenshot({ path: "/artifacts/comp-s2-004-internal.png", fullPage: true });
  console.log("PASS browser → Next.js → FastAPI → agent → tools → PostgreSQL synthetic");
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
