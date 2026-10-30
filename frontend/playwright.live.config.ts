import { defineConfig, devices } from "@playwright/test";

/* Live runs with real providers and synthetic personas (e2e/live.e2e.ts); never part of CI. */
export default defineConfig({
  testDir: "e2e",
  testMatch: "live.e2e.ts",
  outputDir: "../data/private/beta/live/results",
  workers: 1,
  retries: 0,
  timeout: 360_000,
  reporter: [["list"]],
  use: {
    ...devices["Desktop Chrome"],
    baseURL: process.env.FULLAURA_LIVE_URL ?? "http://127.0.0.1:8000",
    trace: "off",
    screenshot: "off",
  },
});
