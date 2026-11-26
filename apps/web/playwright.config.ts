import { defineConfig } from "@playwright/test";

const baseURL = "http://127.0.0.1:3103";
const browserExecutable = process.env.BUSINESS_AI_TEST_BROWSER;

export default defineConfig({
  testDir: "./tests",
  testMatch: "businessAi.e2e.spec.ts",
  timeout: 45_000,
  workers: 1,
  reporter: "list",
  outputDir: process.env.BUSINESS_AI_EVIDENCE_DIR || "test-results/business-ai",
  use: {
    baseURL,
    browserName: "chromium",
    launchOptions: {
      ...(browserExecutable ? { executablePath: browserExecutable } : {}),
      args: ["--disable-background-networking", "--disable-extensions", "--no-first-run"],
    },
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "node tests/businessAi.mock.mjs",
      url: "http://127.0.0.1:4313/health",
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: "node node_modules/next/dist/bin/next dev -H 127.0.0.1 -p 3103",
      url: `${baseURL}/api/business-ai/session`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        BUSINESS_AI_UI_ENABLED: "true",
        BUSINESS_AI_UI_ACCESS_CODE: "synthetic-ui-access-code-32-characters",
        BUSINESS_AI_SESSION_SECRET: "synthetic-session-secret-32-characters",
        BUSINESS_AGENT_ACCESS_TOKEN: "synthetic-backend-token-32-chars",
        BUSINESS_AI_API_URL: "http://127.0.0.1:4313/api/v1/agent/chat",
        BUSINESS_NOTIFICATIONS_ENABLED: "true",
        BUSINESS_NOTIFICATIONS_BUSINESS_ID: "synthetic-store",
        BUSINESS_NOTIFICATIONS_ACTORS_JSON: JSON.stringify([
          { id: "merchant-a", business_id: "synthetic-store", user_id: "11111111-1111-4111-8111-111111111111",
            access_code: "synthetic-personal-code-merchant-a-32", api_token: "synthetic-notification-token-merchant-a-32" },
          { id: "merchant-b", business_id: "synthetic-store", user_id: "22222222-2222-4222-8222-222222222222",
            access_code: "synthetic-personal-code-merchant-b-32", api_token: "synthetic-notification-token-merchant-b-32" },
        ]),
        BUSINESS_AI_NOTIFICATIONS_API_URL: "http://127.0.0.1:4313/api/v1/notifications",
        NEXT_PUBLIC_API_URL: "http://127.0.0.1:4313/api/v1",
        NEXT_TELEMETRY_DISABLED: "1",
      },
    },
  ],
});
