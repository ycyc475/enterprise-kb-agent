import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  fullyParallel: false,
  timeout: 30000,
  reporter: [["list"], ["json", { outputFile: "../.m6/browser-results.json" }]],
  outputDir: "../.m6/browser-artifacts",
  use: {
    baseURL: "http://127.0.0.1:5173",
    channel: "msedge",
    headless: true,
    viewport: { width: 1440, height: 1000 },
    trace: "off",
    screenshot: "off",
  },
});
