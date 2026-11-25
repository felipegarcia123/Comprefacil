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
        NEXT_PUBLIC_API_URL: "http://127.0.0.1:4313/api/v1",
        NEXT_TELEMETRY_DISABLED: "1",
      },
    },
  ],
});
