// Invoked by the isolated pytest fixture backend, never against production.
import { chromium, expect } from "@playwright/test";

const base = process.argv[2];
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base || "")) throw new Error("An isolated localhost fixture backend is required");
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({ viewport: { width: 393, height: 852 }, permissions: ["clipboard-read", "clipboard-write"] });
  await context.addInitScript(() => {
    // Observe the real native transport; no fake events or timing sleeps.
    const NativeEventSource = window.EventSource;
    window.EventSource = class extends NativeEventSource {
      constructor(url, options) {
        super(url, options);
        this.addEventListener("snapshot", event => { window.__m5EventCursor = event.lastEventId; });
      }
    };
  });
  const errors = [];
  const page = await context.newPage();
  const eventRequests = [];
  page.on("request", request => { if (new URL(request.url()).pathname.endsWith("/events")) eventRequests.push({ url: request.url(), headers: request.headers() }); });
  page.on("pageerror", error => errors.push(error.message));
  const login = await context.request.post(`${base}/api/auth/login`, { data: { password: "m5-browser-fixture" } });
  expect(login.ok()).toBeTruthy();
  const started = performance.now();
  const admission = await context.request.post(`${base}/_fixture/admit`);
  expect(admission.ok()).toBeTruthy();
  const allocated = await admission.json();
  const admissionMs = performance.now() - started;
  // Admission is setup only: the actual page starts without a session and must
  // traverse the real password form to the exact future-ID return URL.
  await context.clearCookies();
  const returnPath = `${allocated.url}?via=notification#body`;
  await page.goto(`${base}${returnPath}`);
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
  expect(new URL(page.url()).searchParams.get("next")).toBe(returnPath);
  await page.getByPlaceholder("Password").fill("wrong-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByText("Invalid password.", { exact: true })).toBeVisible();
  expect(new URL(page.url()).pathname).toBe("/login");
  await page.getByPlaceholder("Password").fill("m5-browser-fixture");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect.poll(() => page.url()).toBe(`${base}${returnPath}`);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Document");
  await expect(page.getByText("Working", { exact: true })).toBeVisible();
  const marker = await page.evaluate(() => { window.__m5Marker = Math.random(); return window.__m5Marker; });
  const publishStart = performance.now();
  expect((await context.request.post(`${base}/_fixture/publish/1`)).ok()).toBeTruthy();
  await expect(page.getByText("Published body 1", { exact: true })).toBeVisible();
  const publicationVisibleMs = performance.now() - publishStart;
  expect(eventRequests.length).toBeGreaterThan(0);
  expect(publicationVisibleMs).toBeLessThan(5000);
  expect(await page.evaluate(() => window.__m5Marker)).toBe(marker);
  await page.getByRole("button", { name: "Copy", exact: true }).click();
  await expect(page.getByRole("button", { name: "Copied", exact: true })).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("Published body 1");
  await page.evaluate(() => { window.print = () => { window.__m5Printed = true; }; });
  await page.getByRole("button", { name: "Print", exact: true }).click();
  expect(await page.evaluate(() => window.__m5Printed)).toBe(true);
  await page.emulateMedia({ media: "print" });
  await expect(page.getByRole("button", { name: "Print", exact: true })).toBeHidden();
  await expect(page.getByText("Published body 1", { exact: true })).toBeVisible();
  await page.emulateMedia({ media: "screen" });
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("button", { name: "Saved", exact: true })).toHaveAttribute("aria-pressed", "true");
  await page.reload();
  await expect(page.getByText("Published body 1", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Saved", exact: true })).toHaveAttribute("aria-pressed", "true");
  const reconnectedMarker = await page.evaluate(() => { window.__m5Marker = Math.random(); return window.__m5Marker; });
  await expect.poll(() => page.evaluate(() => window.__m5EventCursor)).toMatch(/^\d+$/);
  const requestsBeforeLoss = eventRequests.length;
  await context.setOffline(true);
  await expect(page.getByText(/Reconnecting live updates|Live updates unavailable/)).toBeVisible({ timeout: 20000 });
  expect((await context.request.post(`${base}/_fixture/followup`)).ok()).toBeTruthy();
  expect((await context.request.post(`${base}/_fixture/publish/2`)).ok()).toBeTruthy();
  await context.setOffline(false);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("M5 revision 2");
  expect(eventRequests.length).toBeGreaterThan(requestsBeforeLoss);
  expect(eventRequests.slice(requestsBeforeLoss).some(request => request.headers["last-event-id"] || new URL(request.url).searchParams.has("cursor"))).toBe(true);
  expect(await page.evaluate(() => window.__m5Marker)).toBe(reconnectedMarker);
  expect(page.url()).toBe(`${base}${returnPath}`);
  expect((await context.request.post(`${base}/_fixture/followup`)).ok()).toBeTruthy();
  expect((await context.request.post(`${base}/_fixture/fail`)).ok()).toBeTruthy();
  await expect(page.getByText(/latest update did not publish/)).toBeVisible();
  await expect(page.getByText("Published body 2", { exact: true })).toBeVisible();
  const before = await (await context.request.get(`${base}/api/results/${allocated.public_id}`)).json();
  expect((await (await context.request.post(`${base}/_fixture/late`)).json()).blocked).toBe(true);
  const after = await (await context.request.get(`${base}/api/results/${allocated.public_id}`)).json();
  expect(after).toEqual(before);
  // Follow the production HTML routes, including unknown-at-build task IDs.
  await page.getByRole("link", { name: "View source task" }).click();
  await expect.poll(() => new URL(page.url()).pathname).toBe(`/tasks/${allocated.task_id}`);
  await expect(page.getByRole("link", { name: "Back to tasks" })).toBeVisible();
  // Long Windows target paths must not enlarge the implicit mobile grid track
  // and put the follow-up action outside the viewport.
  const sendBounds = await page.getByRole("button", { name: "Send", exact: true }).boundingBox();
  expect(sendBounds).not.toBeNull();
  expect(sendBounds.x).toBeGreaterThanOrEqual(0);
  expect(sendBounds.x + sendBounds.width).toBeLessThanOrEqual(393);
  await page.locator(`a[href="${allocated.url}"]`).first().click();
  await expect(page.getByText("Published body 2", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "← Documents", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Documents", exact: true })).toBeVisible();
  await page.getByLabel("Search results").fill("M5 revision 2");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await page.locator(`a[href="${allocated.url}"]`).click();
  await expect(page.getByText("Published body 2", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Saved", exact: true })).toHaveAttribute("aria-pressed", "true");
  const reopened = await (await context.request.get(`${base}/api/results/${allocated.public_id}`)).json();
  expect(reopened).toEqual(before); // Reopening did not run work or change content.
  await page.getByRole("link", { name: "← Documents", exact: true }).click();
  await page.getByRole("link", { name: "← Apps", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Maintained collections" })).toBeVisible();
  await expect(page.getByRole("button", { name: "New draft app" })).toHaveCount(0);
  await page.locator("article").filter({ has: page.getByRole("heading", { name: "Recipes", exact: true }) }).getByRole("link", { name: "Open collection", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Recipes", exact: true })).toBeVisible();
  await page.goto(`${base}/tasks`);
  await expect(page.getByRole("heading", { name: "Tasks", exact: true })).toBeVisible();
  await page.locator(`a[href="/tasks/${allocated.task_id}"]`).click();
  await expect(page.getByRole("link", { name: "Back to tasks" })).toBeVisible();
  await page.getByRole("link", { name: "Back to tasks" }).click();
  await expect(page.getByRole("heading", { name: "Tasks", exact: true })).toBeVisible();
  await context.clearCookies();
  const taskReturnPath = `/tasks/${allocated.task_id}?via=backlink#result`;
  await page.goto(`${base}${taskReturnPath}`);
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
  expect(new URL(page.url()).searchParams.get("next")).toBe(taskReturnPath);
  await page.getByPlaceholder("Password").fill("m5-browser-fixture");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect.poll(() => page.url()).toBe(`${base}${taskReturnPath}`);
  await expect(page.getByRole("link", { name: "Back to tasks" })).toBeVisible();
  // A backslash authority trick must not send a successful login off-origin.
  await page.goto(`${base}/login?next=${encodeURIComponent("/\\elsewhere.invalid")}`);
  // A retained session now returns directly without showing another login.
  await expect.poll(() => page.url()).toBe(`${base}/workspace/apps`);
  await context.clearCookies();
  await page.goto(`${base}/login?next=${encodeURIComponent("/\\elsewhere.invalid")}`);
  await page.getByPlaceholder("Password").fill("m5-browser-fixture");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect.poll(() => page.url()).toBe(`${base}/workspace/apps`);
  await expect(page.locator("script:visible")).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(errors).toEqual([]);
  await expect.poll(async () => {
    const stats = await (await context.request.get(`${base}/api/results/${allocated.public_id}/observability`)).json();
    return ["client_first_snapshot", "client_first_ready", "publication_to_ready_receipt", "client_sse_connect", "client_sse_reconnect", "sse_connection", "allocation", "publication"]
      .every(kind => stats.counts[kind] >= 1);
  }).toBe(true);
  const measured = await (await context.request.get(`${base}/api/results/${allocated.public_id}/observability`)).json();
  expect(measured.publication_outcomes.committed).toBe(2);
  expect(measured.counts.completion_to_notification_enqueue).toBe(2);
  expect(measured.sse_replay_events_emitted).toBeGreaterThan(0);
  expect(JSON.stringify(measured)).not.toContain("Published body");
  expect(JSON.stringify(measured)).not.toContain("Private failure diagnostics");
  console.log(JSON.stringify({ static_export: true, future_id: true, sse_without_reload: true, refresh: true,
    saved: true, copy: true, print: true, network_reconnect_with_cursor: true, same_url_revision: 2, failed_update_preserved: true, stale_attempt_fenced: true,
    login_next: true, task_login_next: true, invalid_password: true, safe_redirect: true, collection_task_backlinks: true, durable_metadata_observability: true,
    mobile_width: 393, admission_ms: Math.round(admissionMs), publication_visible_ms: Math.round(publicationVisibleMs) }));
} finally { await browser.close(); }
