import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";

import { loadJarvisLiveSmokeFixture } from "../e2e/jarvis-live-fixture.mjs";

const args = process.argv.slice(2);
const passthrough = [];
const env = { ...process.env };

function takeValue(index, flag) {
  const value = args[index + 1];
  if (!value || value.startsWith("--")) throw new Error(`${flag} requires a value`);
  return value;
}

try {
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === "--audio") env.JARVIS_SMOKE_AUDIO = takeValue(index++, arg);
    else if (arg === "--category") env.JARVIS_SMOKE_CATEGORY = takeValue(index++, arg);
    else if (arg === "--sample-id") env.JARVIS_SMOKE_SAMPLE_ID = takeValue(index++, arg);
    else if (arg === "--help") {
      process.stdout.write([
        "Usage: npm run test:live-smoke -- [--audio PATH] [--category normal] [--sample-id ID] [--headed]",
        "",
        "PATH may be a Wake calibration JSON export or a browser-decodable audio file.",
        "Without --audio, the newest jarvis-calibration-*.json in Downloads or Documents is used.",
        "Set JARVIS_SMOKE_PASSWORD when local authentication is enabled.",
        "",
      ].join("\n"));
      process.exit(0);
    } else passthrough.push(arg);
  }
  const fixture = loadJarvisLiveSmokeFixture({
    cwd: process.cwd(),
    audioPath: env.JARVIS_SMOKE_AUDIO,
    category: env.JARVIS_SMOKE_CATEGORY,
    sampleId: env.JARVIS_SMOKE_SAMPLE_ID,
  });
  process.stdout.write(`Jarvis Live smoke input: ${fixture.label} (${fixture.sourcePath})\n`);
} catch (cause) {
  process.stderr.write(`${cause instanceof Error ? cause.message : String(cause)}\n`);
  process.exit(2);
}

const executable = resolve(
  process.cwd(),
  "node_modules",
  ".bin",
  process.platform === "win32" ? "playwright.cmd" : "playwright",
);
const runDirectory = mkdtempSync(join(tmpdir(), "ai-cockpit-live-smoke-"));
env.JARVIS_SMOKE_RUN_DIR = runDirectory;
let status = 1;
try {
  const result = spawnSync(
    executable,
    ["test", "--config=playwright.live.config.ts", ...passthrough],
    { cwd: process.cwd(), env, stdio: "inherit" },
  );
  if (result.error) process.stderr.write(`${result.error.message}\n`);
  status = result.error ? 1 : result.status ?? 1;
} finally {
  const resolvedRunDirectory = resolve(runDirectory);
  const resolvedTempDirectory = resolve(tmpdir());
  if (
    dirname(resolvedRunDirectory) === resolvedTempDirectory
    && basename(resolvedRunDirectory).startsWith("ai-cockpit-live-smoke-")
  ) {
    try {
      rmSync(resolvedRunDirectory, { force: true, recursive: true, maxRetries: 3, retryDelay: 100 });
    } catch (cause) {
      process.stderr.write(`Unable to remove isolated smoke-test data: ${cause instanceof Error ? cause.message : String(cause)}\n`);
    }
  }
}
process.exit(status);
