import { defineConfig, devices } from "@playwright/test";

/* The browser end-to-end run: the built app served by scripts/e2e_server.py, whose planner and
 * image model are scripted stand-ins. Build the frontend first (npm run build). */
const PORT = 8765;

export default defineConfig({
  testDir: "e2e",
  testMatch: "**/*.e2e.ts",
  outputDir: "../runs/e2e/results",
  fullyParallel: false,
  workers: 1, // one invite, which runs one model request at a time
  retries: 0,
  timeout: 90_000,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    permissions: ["clipboard-read", "clipboard-write"],
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
  webServer: {
    command: `uv run --frozen python -m scripts.e2e_server --port ${PORT}`,
    cwd: "..",
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
