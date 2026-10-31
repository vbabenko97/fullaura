import { defineConfig, devices } from "@playwright/test";

/* The browser end-to-end run: the built app served by scripts/e2e_server.py, whose planner and
 * image model are scripted stand-ins. Build the frontend first (npm run build). The "-design-off"
 * projects run the beta flow against a second server with Design My Look off, as the closed beta
 * serves it. */
const PORT = 8765;
const DESIGN_OFF_PORT = 8766;
const desktop = { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } };
const designOff = { baseURL: `http://127.0.0.1:${DESIGN_OFF_PORT}` };

function server(port: number, designMode: "on" | "off") {
  return {
    command: `uv run --frozen python -m scripts.e2e_server --port ${port} --design-mode ${designMode}`,
    cwd: "..",
    url: `http://127.0.0.1:${port}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
  };
}

export default defineConfig({
  testDir: "e2e",
  testMatch: "beta.e2e.ts",
  outputDir: "../runs/e2e/results",
  fullyParallel: false,
  workers: 1, // one invite per server, which runs one model request at a time
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
    { name: "desktop", use: desktop },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
    { name: "desktop-design-off", grepInvert: /Design my look/, use: { ...desktop, ...designOff } },
    { name: "mobile-design-off", grepInvert: /Design my look/, use: { ...devices["Pixel 7"], ...designOff } },
  ],
  webServer: [server(PORT, "on"), server(DESIGN_OFF_PORT, "off")],
});
