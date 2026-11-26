import { expect, test } from "@playwright/test";

const code = "synthetic-ui-access-code-32-characters";

for (const [name, width, height] of [
  ["desktop", 1440, 900], ["tablet horizontal", 1024, 768],
  ["tablet vertical", 768, 1024], ["móvil", 390, 844],
] as const) {
  test(`${name}: acceso, conversación y adaptación`, async ({ browser }, testInfo) => {
    const context = await browser.newContext({ viewport: { width, height }, reducedMotion: "reduce" });
    const page = await context.newPage();
    const errors: string[] = [];
    const clientBodies: unknown[] = [];
    await context.route("**/*", route => {
      const url = new URL(route.request().url());
      return url.hostname === "127.0.0.1" ? route.continue() : route.abort();
    });
    page.on("pageerror", error => errors.push(error.message));
    page.on("request", request => {
      if (request.url().endsWith("/api/business-ai/chat")) {
        clientBodies.push(request.postDataJSON());
        expect(request.headers().authorization).toBeUndefined();
      }
    });
    try {
      await page.goto("http://127.0.0.1:3103/business-ai", { waitUntil: "networkidle" });
      await expect(page.getByRole("heading", { name: "Acceso experimental" })).toBeVisible();
      await page.screenshot({ path: testInfo.outputPath("locked.png"), fullPage: true });
      await page.getByLabel("Código de acceso").fill(code);
      await page.getByRole("button", { name: "Ingresar" }).click();
      await expect(page.getByRole("heading", { name: "¿Qué quieres saber?" })).toBeVisible();
      await expect(page.locator(".ai-prompt")).toHaveCount(5);
      const fit = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth }));
      expect(fit.scroll).toBeLessThanOrEqual(fit.width + 1);
      await page.screenshot({ path: testInfo.outputPath("ready.png"), fullPage: true });
      const responseReady = page.waitForResponse(response => response.url().endsWith("/api/business-ai/chat"));
      await page.getByRole("button", { name: /Ventas.*Cuánto vendimos/s }).click();
      await expect(page.getByText("Consultando datos del negocio…")).toBeVisible();
      const responseBody = await (await responseReady).json();
      expect(responseBody.ignored_private_field).toBeUndefined();
      await expect(page.getByText("Ventas verificadas: 120 000 pesos.", { exact: false })).toBeVisible();
      await expect(page.getByText("Limitaciones de los datos")).toBeVisible();
      expect(Object.keys(clientBodies[0] as object)).toEqual(["question"]);
      if (name === "desktop") {
        await page.getByLabel("Escribe tu pregunta").fill("seguimiento de ese período");
        await page.getByRole("button", { name: "Enviar pregunta" }).click();
        await expect(page.getByText("Seguimiento del período anterior con datos sintéticos.")).toBeVisible();
      }
      await page.screenshot({ path: testInfo.outputPath("answer.png"), fullPage: true });
      if (name === "desktop") {
        await page.getByLabel("Escribe tu pregunta").fill("provocar error");
        await page.getByRole("button", { name: "Enviar pregunta" }).click();
        await expect(page.getByRole("alert").filter({ hasText: "No se pudo completar la consulta" })).toBeVisible();
        await page.getByRole("button", { name: "Nueva conversación" }).click();
        await expect(page.locator(".ai-message")).toHaveCount(0);
        await page.getByLabel("Escribe tu pregunta").fill("seguimiento de ese período");
        await page.getByRole("button", { name: "Enviar pregunta" }).click();
        await expect(page.getByText("Contexto no disponible.")).toBeVisible();
        await page.getByLabel("Escribe tu pregunta").fill("inventario");
        await page.getByRole("button", { name: "Enviar pregunta" }).click();
        await expect(page.getByText("Hay 2 productos con stock bajo", { exact: false })).toBeVisible();
        await expect(page.locator(".ai-message__sources").filter({ hasText: "Stock bajo" })).toBeVisible();
        expect(Object.keys(clientBodies.at(-1) as object)).toEqual(["question"]);
        await page.getByRole("button", { name: "Cerrar acceso" }).click();
        await expect(page.getByRole("heading", { name: "Acceso experimental" })).toBeVisible();
        expect((await page.request.post("http://127.0.0.1:3103/api/business-ai/chat", {
          headers: { Origin: "http://127.0.0.1:3103" }, data: { question: "Ventas hoy" },
        })).status()).toBe(401);
      }
      if (name === "móvil") {
        await page.getByRole("button", { name: "Menú" }).click();
        await expect(page.getByRole("link", { name: /Comprefácil AI/ }).last()).toBeVisible();
      }
      expect(errors).toEqual([]);
    } finally {
      await context.close();
    }
  });
}

test("el puente rechaza solicitudes sin sesión y de otro origen", async ({ request }) => {
  const base = "http://127.0.0.1:3103";
  const options = { headers: { Origin: base }, data: { question: "Ventas hoy" } };
  expect((await request.post(`${base}/api/business-ai/chat`, options)).status()).toBe(401);
  expect((await request.post(`${base}/api/business-ai/session`, {
    headers: { Origin: "http://other.test" }, data: { code },
  })).status()).toBe(403);
  expect((await request.post(`${base}/api/business-ai/session`, {
    headers: { Origin: base }, data: { code: "wrong" },
  })).status()).toBe(401);
});
