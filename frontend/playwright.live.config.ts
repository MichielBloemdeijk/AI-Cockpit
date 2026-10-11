import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";

const root = __dirname;
const baseURL = process.env.JARVIS_SMOKE_BASE_URL?.trim() || "http://127.0.0.1:3100";
const externalServer = Boolean(process.env.JARVIS_SMOKE_BASE_URL?.trim());
const runDirectory = process.env.JARVIS_SMOKE_RUN_DIR?.trim();
if (!externalServer && !runDirectory) {
  throw new Error("The Jarvis Live runner did not provide its isolated run directory");
}
const normalizedDatabasePath = resolve(runDirectory || root, "jarvis-live-smoke.db").replaceAll("\\", "/");

export default defineConfig({
  testDir: "./e2e",
  testMatch: "jarvis-live.smoke.spec.ts",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "line",
  outputDir: "test-results/jarvis-live",
  use: {
    baseURL,
    channel: process.env.JARVIS_SMOKE_BROWSER_CHANNEL?.trim() || "chrome",
    headless: true,
    permissions: ["microphone"],
    viewport: { width: 1440, height: 900 },
    trace: "off",
    screenshot: "off",
    video: "off",
    launchOptions: { args: ["--autoplay-policy=no-user-gesture-required"] },
  },
  webServer: externalServer ? undefined : [
    {
      command: "uv run uvicorn app.main:app --host 127.0.0.1 --port 8100",
      cwd: resolve(root, "../backend"),
      env: {
        ...process.env,
        AUTH_PASSWORD: "",
        JARVIS_ACCESS_MODE: "personal",
        TASK_POOL_ENABLED: "false",
        TASK_ORCHESTRATION_ENABLED: "false",
        TASK_ORCHESTRATION_LIVE_ENABLED: "false",
        DATABASE_URL: `sqlite+aiosqlite:///${normalizedDatabasePath}`,
        CONVERSATION_WORKSPACES_DIR: resolve(runDirectory || root, "conversations"),
        LOCAL_LLM_ENABLED: "false",
        SESSION_COOKIE_SECURE: "false",
        SESSION_SECRET_KEY: `jarvis-live-smoke-${process.pid}`,
      },
      url: "http://127.0.0.1:8100/api/health",
      timeout: 120_000,
      reuseExistingServer: false,
    },
    {
      command: "npx next dev --hostname 127.0.0.1 --port 3100",
      cwd: root,
      env: {
        ...process.env,
        AI_COCKPIT_BACKEND_URL: "http://127.0.0.1:8100",
        AI_COCKPIT_API_PROXY_URL: "http://127.0.0.1:8100",
        AI_COCKPIT_NEXT_DIST_DIR: ".next-live-smoke",
        AI_COCKPIT_TSCONFIG_PATH: "tsconfig.live-smoke.json",
      },
      url: baseURL,
      timeout: 120_000,
      reuseExistingServer: false,
    },
  ],
});
