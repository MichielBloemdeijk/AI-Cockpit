// Unpaid journey against the selected personal host. No credentials in reports.
import { chromium } from "@playwright/test";
import { spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const origin = process.argv[2];
if (!origin) throw new Error("Pass the selected personal HTTPS origin.");
const reportPath = resolve(process.argv[3] || "../data/m11-offline/browser.json");
const restart = process.argv.includes("--restart");
const browser = await chromium.launch({ channel: "chrome", headless: true });
const owner = await browser.newContext();
const phone = await browser.newContext({ userAgent: "JarvisPhone/1" });
const report = { models_called: 0, status: "failed" };
let deviceId;
try {
  const page = await owner.newPage();
  await page.goto(`${origin}/settings`);
  await page.getByRole("button", { name: "Link phone", exact: true }).waitFor();
  assert.equal(new URL(page.url()).pathname, "/settings");
  assert.equal(await page.locator('input[name="password"]').count(), 0);
  await page.getByRole("button", { name: "Link phone", exact: true }).click();
  await page.getByAltText("Scan to link Jarvis").waitFor();
  const href = await page.getByRole("link", { name: "Open on phone", exact: true }).getAttribute("href");
  assert.equal(new URL(href).origin, origin);
  const fields = new URLSearchParams(new URL(href).hash.slice(1));
  assert.match(fields.get("code"), /^\d{6}$/);
  const landing = await owner.newPage();
  await landing.goto(href);
  const deepLink = await landing.getByRole("link", { name: "Open Jarvis and link phone" }).getAttribute("href");
  assert.match(deepLink, /^jarvis:\/\/pair\?/);
  const paired = await owner.request.post(`${origin}/api/jarvis/devices/pair`, { data: { code: fields.get("code"), label: "Browser setup verification" } });
  assert.equal(paired.status(), 200);
  const device = await paired.json();
  deviceId = device.device_id;
  const renew = await phone.request.post(`${origin}/api/jarvis/devices/web-session`, { headers: { Authorization: `Bearer ${device.credential}` } });
  assert.equal(renew.status(), 200); // Playwright's request context stores Set-Cookie.
  const phonePage = await phone.newPage();
  await phonePage.goto(`${origin}/tasks`);
  assert.equal((await phone.request.get(`${origin}/api/tasks`)).status(), 200);
  assert.equal((await phone.request.get(`${origin}/api/jarvis/devices`)).status(), 401);
  assert.equal((await phone.request.post(`${origin}/api/tasks/pool/control`, { data: { action: "stop" } })).status(), 403);
  const deniedUiRequests = [];
  phonePage.on("response", response => { if (response.status() === 401) deniedUiRequests.push(new URL(response.url()).pathname); });
  await phonePage.goto(`${origin}/settings`);
  await phonePage.getByRole("heading", { name: "This phone is linked" }).waitFor();
  assert.equal(await phonePage.getByRole("button", { name: "Link phone", exact: true }).count(), 0);
  assert.equal(await phonePage.getByRole("link", { name: "Open phone setup and status" }).getAttribute("href"), "jarvis://setup");
  assert.deepEqual(deniedUiRequests, []);
  report.phone_settings_has_paired_status_without_owner_requests = true;
  report.personal_bootstrap = report.qr_and_landing = report.device_scope = true;
  if (restart) {
    const before = await (await owner.request.get(`${origin}/api/tasks`)).json();
    const script = fileURLToPath(new URL("../../scripts/jarvis.ps1", import.meta.url));
    for (const action of ["Stop", "Start"]) {
      // No captured pipes: the detached service must not keep this runner's
      // output handles open after PowerShell has finished starting it.
      const result = spawnSync("powershell", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, action], { stdio: "ignore", timeout: 150_000 });
      assert.equal(result.status, 0, `Host ${action} failed; inspect personal service logs.`);
    }
    assert.equal((await phone.request.get(`${origin}/api/auth/status`)).status(), 200);
    const after = await (await owner.request.get(`${origin}/api/tasks`)).json();
    assert.deepEqual(after.map(task => task.id).sort(), before.map(task => task.id).sort());
    report.host_restart_saved_session_and_history = true;
  }
  await owner.request.delete(`${origin}/api/jarvis/devices/${deviceId}`);
  assert.equal((await phone.request.get(`${origin}/api/auth/status`)).status(), 401);
  assert.equal((await phone.request.post(`${origin}/api/jarvis/devices/web-session`, { headers: { Authorization: `Bearer ${device.credential}` } })).status(), 401);
  report.revocation = true;
  report.status = "passed";
} catch (error) {
  report.failure = error instanceof Error ? error.message : "Journey failed";
  process.exitCode = 1;
} finally {
  if (deviceId) await owner.request.delete(`${origin}/api/jarvis/devices/${deviceId}`).catch(() => {});
  await browser.close();
  mkdirSync(resolve(reportPath, ".."), { recursive: true });
  writeFileSync(reportPath, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
}
