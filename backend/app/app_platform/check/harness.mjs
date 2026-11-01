// Headless UI check for a cockpit-app.v1 package (run by app_platform/check/__init__.py).
//
// Reads one JSON job from stdin and writes one JSON report to stdout. The app's
// UI is served exactly as the real host serves it (same CSP sandbox, same SDK
// path) and framed by a stand-in Cockpit page whose bridge keeps the data in
// memory. Each test starts from an empty store plus its own seed records.
//
// job: { playwrightDir, packageUi, entry, sdkFile, csp, screenshotsDir, slug, title, version, tests: [...] }
import { createRequire } from "node:module";
import { readFile } from "node:fs/promises";
import path from "node:path";

const job = JSON.parse(await new Promise((resolve) => {
  let input = "";
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", (chunk) => { input += chunk; });
  process.stdin.on("end", () => resolve(input));
}));
const { chromium } = createRequire(path.join(job.playwrightDir, "package.json"))("playwright");

const ORIGIN = "http://cockpit.test";
const STEP_TIMEOUT = 5000;
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
  ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".ico": "image/x-icon",
  ".woff": "font/woff", ".woff2": "font/woff2", ".txt": "text/plain" };

// Data placed inside the host page's inline script travels as base64, so a record
// containing "</script>" cannot end the script.
const scriptJson = (value) => `JSON.parse(new TextDecoder().decode(Uint8Array.from(atob("${
  Buffer.from(JSON.stringify(value), "utf8").toString("base64")}"), (c) => c.charCodeAt(0))))`;

function hostPage(test) {
  return `<!doctype html><html><body style="margin:0">
<iframe id="app" src="/app/${job.entry}" sandbox="allow-scripts allow-forms allow-popups allow-modals"
  style="border:0;width:100vw;height:100vh"></iframe>
<script>
  const store = new Map();
  for (const [collection, records] of Object.entries(${scriptJson(test.records || {})})) {
    for (const record of records) store.set(collection + "/" + record.key,
      { key: record.key, value: record.value, revision: 1, updated_at: new Date().toISOString() });
  }
  // The file store: name -> Blob. Seeds arrive as base64.
  const files = new Map();
  for (const [name, seed] of Object.entries(${scriptJson(test.files || {})})) {
    files.set(name, new Blob([Uint8Array.from(atob(seed.base64), (c) => c.charCodeAt(0))], { type: seed.content_type }));
  }
  const functionResults = ${scriptJson(test.functions || {})};
  const httpFunctions = ${scriptJson(job.httpFunctions || [])};
  const readOnly = ${scriptJson(job.readOnly || [])};
  window.__ready = false;
  window.__appErrors = [];
  window.__records = () => Array.from(store.entries()).map(([id, record]) =>
    ({ collection: id.slice(0, id.indexOf("/")), ...record }));
  window.__files = () => Array.from(files.keys());
  const frame = document.getElementById("app");
  function fail(status, error) { const e = new Error(error); e.status = status; throw e; }
  const fileMeta = (name, blob) => ({ name, size: blob.size, content_type: blob.type || "application/octet-stream",
    updated_at: new Date().toISOString() });
  function handle(method, params) {
    if (method === "files.list")
      return Array.from(files.entries()).filter(([name]) => name.startsWith(params.prefix || ""))
        .sort(([a], [b]) => (a < b ? -1 : 1)).map(([name, blob]) => fileMeta(name, blob));
    if (method === "files.put") {
      if (!(params.data instanceof Blob)) fail(400, "files.put needs a Blob, File or string");
      const blob = params.contentType ? new Blob([params.data], { type: params.contentType }) : params.data;
      files.set(String(params.name), blob);
      return fileMeta(String(params.name), blob);
    }
    if (method === "files.get") return files.get(String(params.name)) || null;
    if (method === "files.delete") return files.delete(String(params.name));
    if (method === "functions.call") {
      if (!httpFunctions.includes(params.name)) fail(403, "Function " + params.name + " has no http trigger");
      const result = functionResults[params.name];
      if (!result) fail(500, "The UI test gives no result for function " + params.name);
      if (result.error) fail(500, result.error);
      return structuredClone(result.output ?? null);
    }
    const id = params.collection + "/" + params.key;
    const current = store.get(id);
    const expected = params.expectedRevision;
    if ((method === "data.put" || method === "data.delete") && (readOnly.includes(params.collection)
        || String(params.collection).startsWith("system."))) fail(403, params.collection + " is read-only for this app; it needs grants.write");
    if (method === "data.get") return current ? structuredClone(current) : null;
    if (method === "data.put") {
      if (expected !== undefined && expected !== null && expected !== (current ? current.revision : 0))
        fail(409, "Expected revision " + expected + ", found " + (current ? current.revision : 0));
      const record = { key: params.key, value: structuredClone(params.value),
        revision: (current ? current.revision : 0) + 1, updated_at: new Date().toISOString() };
      store.set(id, record);
      return structuredClone(record);
    }
    if (method === "data.delete") {
      if (!current) return false;
      if (expected !== undefined && expected !== null && expected !== current.revision)
        fail(409, "Expected revision " + expected + ", found " + current.revision);
      store.delete(id);
      return true;
    }
    if (method === "data.query") {
      let records = window.__records().filter((record) => record.collection === params.collection)
        .map(({ collection, ...record }) => record);
      for (const [field, value] of Object.entries(params.where || {}))
        records = records.filter((record) => (record.value[field] ?? null) === value);
      const order = params.order || "-updated_at";
      const field = order.replace(/^-/, "");
      const pick = (record) => field === "updated_at" ? record.updated_at : field === "key" ? record.key : record.value[field];
      records.sort((a, b) => { const x = pick(a), y = pick(b); return x === y ? 0 : (x ?? "") < (y ?? "") ? -1 : 1; });
      if (order.startsWith("-")) records.reverse();
      return structuredClone(records.slice(0, Math.min(params.limit || 50, 200)));
    }
    fail(400, "Unknown method " + method);
  }
  window.addEventListener("message", (event) => {
    if (event.source !== frame.contentWindow || !event.data || typeof event.data !== "object") return;
    const message = event.data;
    const reply = (data) => frame.contentWindow.postMessage(data, "*");
    if (message.cockpit === "ready") {
      window.__ready = true;
      reply({ cockpit: "hello", slug: ${scriptJson(job.slug)}, title: ${scriptJson(job.title)}, version: ${job.version},
        target: ${scriptJson(test.open || null)} });
    } else if (message.cockpit === "error") {
      window.__appErrors.push(String(message.message || "") + (message.detail ? "\\n" + message.detail : ""));
    } else if (message.cockpit === "request") {
      try { reply({ cockpit: "response", id: message.id, ok: true, result: handle(message.method, message.params || {}) }); }
      catch (error) { reply({ cockpit: "response", id: message.id, ok: false, status: error.status || 0, error: error.message }); }
    }
  });
</script></body></html>`;
}

async function serveFile(route, file, headers = {}) {
  try {
    const body = await readFile(file);
    await route.fulfill({ status: 200, body, headers: { "Content-Type": TYPES[path.extname(file).toLowerCase()] || "application/octet-stream", ...headers } });
  } catch {
    await route.fulfill({ status: 404, body: "Not found" });
  }
}

async function poll(check, description) {
  const deadline = Date.now() + STEP_TIMEOUT;
  let last;
  while (Date.now() < deadline) {
    last = await check();
    if (last === true) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`${description}${typeof last === "string" ? ` (${last})` : ""}`);
}

async function runStep(page, frame, step) {
  const options = { timeout: STEP_TIMEOUT };
  if ("fill" in step) return frame.locator(step.fill).first().fill(String(step.value ?? ""), options);
  if ("click" in step) return frame.locator(step.click).first().click(options);
  if ("press" in step) return frame.locator(step.press).first().press(step.key, options);
  if ("select" in step) return frame.locator(step.select).first().selectOption(String(step.value), options);
  if ("check" in step) return frame.locator(step.check).first().setChecked(step.value !== false, options);
  if ("wait" in step) return new Promise((resolve) => setTimeout(resolve, Math.min(Number(step.wait) || 0, 2000)));
  if ("upload" in step) {
    const buffer = step.base64 !== undefined ? Buffer.from(step.base64, "base64") : Buffer.from(step.text ?? "", "utf8");
    return frame.locator(step.upload).first().setInputFiles(
      { name: step.name, mimeType: step.content_type || "text/plain", buffer }, options);
  }
  if ("expect_files" in step) {
    const { count, prefix = "" } = step.expect_files;
    return poll(async () => {
      const found = (await page.evaluate(() => window.__files())).filter((name) => name.startsWith(prefix));
      return found.length === count || `found ${found.length}: ${JSON.stringify(found.slice(0, 10))}`;
    }, `Expected ${count} files starting with ${JSON.stringify(prefix)}`);
  }
  if ("expect_text" in step) {
    const scope = frame.locator(step.selector || "body").first();
    return poll(async () => {
      const text = await scope.innerText({ timeout: 500 }).catch(() => "");
      return text.includes(step.expect_text) || `found: ${JSON.stringify(text.slice(0, 300))}`;
    }, `Expected text ${JSON.stringify(step.expect_text)} in ${step.selector || "body"}`);
  }
  if ("expect_count" in step) {
    return poll(async () => {
      const count = await frame.locator(step.expect_count).count();
      return count === step.count || `found ${count}`;
    }, `Expected ${step.count} elements matching ${step.expect_count}`);
  }
  if ("expect_records" in step) {
    const { collection, count, where = {} } = step.expect_records;
    return poll(async () => {
      const records = (await page.evaluate(() => window.__records())).filter((record) => record.collection === collection
        && Object.entries(where).every(([field, value]) => (record.value[field] ?? null) === value));
      return records.length === count || `found ${records.length}`;
    }, `Expected ${count} ${collection} records matching ${JSON.stringify(where)}`);
  }
  throw new Error(`Unknown step ${JSON.stringify(step)}`);
}

async function runTest(browser, test, index) {
  const context = await browser.newContext({ viewport: { width: 420, height: 800 } });
  const page = await context.newPage();
  const problems = [];
  page.on("console", (message) => { if (message.type() === "error") problems.push(`console: ${message.text()}`); });
  page.on("pageerror", (error) => problems.push(`uncaught: ${error.message}`));
  // Tests act as a user who confirms: confirm() and alert() dialogs are accepted.
  page.on("dialog", (dialog) => dialog.accept().catch(() => undefined));
  await context.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== ORIGIN) {
      problems.push(`blocked request to ${url.origin}: apps cannot use the network`);
      return route.abort();
    }
    if (url.pathname === "/host.html") return route.fulfill({ status: 200, contentType: "text/html", body: hostPage(test) });
    if (url.pathname === "/api/app-host/_sdk/cockpit-app.js") return serveFile(route, job.sdkFile);
    if (url.pathname.startsWith("/app/")) {
      const relative = decodeURIComponent(url.pathname.slice("/app/".length)) || "index.html";
      const file = path.resolve(job.packageUi, relative);
      if (!file.startsWith(path.resolve(job.packageUi) + path.sep)) return route.fulfill({ status: 404, body: "Not found" });
      return serveFile(route, file, { "Content-Security-Policy": job.csp });
    }
    problems.push(`request for a file the package does not serve: ${url.pathname}`);
    return route.fulfill({ status: 404, body: "Not found" });
  });
  const result = { name: test.name, ok: false, error: null, problems, text: "", records: [], screenshot: null };
  try {
    await page.goto(`${ORIGIN}/host.html`);
    await poll(() => page.evaluate(() => window.__ready), "The app never loaded the SDK (cockpit-app.js) and announced itself");
    const frame = page.frameLocator("#app");
    await page.waitForTimeout(300);
    for (const [stepIndex, step] of (test.steps || []).entries()) {
      try { await runStep(page, frame, step); }
      catch (error) { throw new Error(`step ${stepIndex + 1} ${JSON.stringify(step)}: ${error.message.split("\n")[0]}`); }
    }
    await page.waitForTimeout(300);
    result.ok = true;
  } catch (error) {
    result.error = error.message.split("\n").slice(0, 3).join(" ");
  }
  try {
    result.text = (await page.frameLocator("#app").locator("body").innerText({ timeout: 1000 })).slice(0, 2000);
    result.records = await page.evaluate(() => window.__records());
    problems.push(...(await page.evaluate(() => window.__appErrors)).map((error) => `reported: ${error}`));
    if (job.screenshotsDir) {
      result.screenshot = path.join(job.screenshotsDir, `${String(index).padStart(2, "0")}.png`);
      await page.screenshot({ path: result.screenshot });
    }
  } catch { /* the failure above already explains it */ }
  result.ok = result.ok && problems.length === 0;
  await context.close();
  return result;
}

const browser = await chromium.launch();
const results = [];
try {
  for (const [index, test] of [{ name: "loads", steps: [] }, ...job.tests].entries()) {
    results.push(await runTest(browser, test, index));
  }
} finally {
  await browser.close();
}
process.stdout.write(JSON.stringify({ tests: results }));
