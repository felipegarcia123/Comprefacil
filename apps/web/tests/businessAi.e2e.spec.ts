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

for (const [name, width, height] of [["desktop", 1440, 900], ["móvil", 390, 844]] as const) {
  test(`${name}: centro personal, consulta y confirmación de recordatorios`, async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width, height }, reducedMotion: "reduce" });
    const page = await context.newPage();
    try {
      await page.goto("http://127.0.0.1:3103/business-ai", { waitUntil: "networkidle" });
      await page.getByLabel("Código de acceso").fill("synthetic-personal-code-merchant-a-32");
      await page.getByRole("button", { name: "Ingresar" }).click();
      await expect(page.getByRole("heading", { name: "Alertas y recordatorios" })).toBeVisible();
      await expect(page.getByText("Stock bajo: arroz")).toBeVisible();
      const fit = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth }));
      expect(fit.scroll).toBeLessThanOrEqual(fit.width + 1);
      await page.getByLabel("Escribe tu pregunta").fill("Recuérdame revisar el pedido de bebidas mañana a las 8 a. m.");
      await page.getByRole("button", { name: "Enviar pregunta" }).click();
      await expect(page.getByLabel("¿Qué quieres recordar?")).toHaveValue("revisar el pedido de bebidas");
      const due = await page.getByLabel("Fecha y hora · Bogotá").inputValue();
      await page.getByRole("button", { name: "Revisar y crear" }).click();
      await expect(page.getByRole("dialog", { name: "Crear recordatorio" })).toBeVisible();
      await page.getByRole("dialog", { name: "Crear recordatorio" }).getByRole("button", { name: "Crear recordatorio" }).click();
      await expect(page.getByRole("listitem").filter({ hasText: "revisar el pedido de bebidas" })).toBeVisible();
      await page.getByLabel("Escribe tu pregunta").fill("¿Qué tengo pendiente para mañana?");
      await page.getByRole("button", { name: "Enviar pregunta" }).click();
      await expect(page.getByText("Recordatorio: revisar el pedido de bebidas", { exact: false })).toBeVisible();
      if (name === "desktop") {
        const other = await browser.newContext({ viewport: { width: 1024, height: 768 } });
        const otherPage = await other.newPage();
        try {
          await otherPage.goto("http://127.0.0.1:3103/business-ai", { waitUntil: "networkidle" });
          await otherPage.getByLabel("Código de acceso").fill("synthetic-personal-code-merchant-b-32");
          await otherPage.getByRole("button", { name: "Ingresar" }).click();
          await expect(otherPage.getByText("No tienes recordatorios pendientes.")).toBeVisible();
        } finally { await other.close(); }
      }
      await page.getByRole("listitem").filter({ hasText: "revisar el pedido de bebidas" }).getByRole("button", { name: "Completar" }).click();
      await page.getByRole("dialog", { name: "Completar recordatorio" }).getByRole("button", { name: "Completar recordatorio" }).click();
      await expect(page.getByText("No tienes recordatorios pendientes.")).toBeVisible();
      if (name === "desktop") {
        await page.getByLabel("¿Qué quieres recordar?").fill("Llamar al proveedor");
        await page.getByLabel("Fecha y hora · Bogotá").fill(due);
        await page.getByRole("button", { name: "Revisar y crear" }).click();
        await page.getByRole("dialog", { name: "Crear recordatorio" }).getByRole("button", { name: "Crear recordatorio" }).click();
        await page.getByRole("listitem").filter({ hasText: "Llamar al proveedor" }).getByRole("button", { name: "Cancelar" }).click();
        await page.getByRole("dialog", { name: "Cancelar recordatorio" }).getByRole("button", { name: "Cancelar recordatorio" }).click();
        await expect(page.getByText("No tienes recordatorios pendientes.")).toBeVisible();
      }
    } finally { await context.close(); }
  });
}
