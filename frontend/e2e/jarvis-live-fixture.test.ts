import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { loadJarvisLiveSmokeFixture } from "./jarvis-live-fixture.mjs";

function writeBundle(path: string): void {
  writeFileSync(path, JSON.stringify({
    version: 1,
    samples: [
      { id: "normal-old", category: "normal", mime_type: "audio/webm", audio_base64: Buffer.from("old").toString("base64") },
      { id: "negative", category: "hard_negative", mime_type: "audio/webm", audio_base64: Buffer.from("no").toString("base64") },
      { id: "normal-new", category: "normal", mime_type: "audio/ogg", audio_base64: Buffer.from("new").toString("base64") },
      { id: "quiet", category: "quiet", mime_type: "audio/webm", audio_base64: Buffer.from("quiet").toString("base64") },
    ],
  }));
}

describe("Jarvis Live smoke fixture selection", () => {
  beforeEach(() => {
    for (const name of ["JARVIS_SMOKE_AUDIO", "JARVIS_SMOKE_CATEGORY", "JARVIS_SMOKE_SAMPLE_ID"]) {
      vi.stubEnv(name, "");
    }
  });
  afterEach(() => vi.unstubAllEnvs());
  it("selects the newest requested positive calibration example", () => {
    const root = mkdtempSync(join(tmpdir(), "jarvis-smoke-"));
    const path = join(root, "calibration.json");
    writeBundle(path);

    const fixture = loadJarvisLiveSmokeFixture({ cwd: root, audioPath: path });

    expect(fixture.label).toBe("normal:normal-new");
    expect(fixture.mimeType).toBe("audio/ogg");
    expect(Buffer.from(fixture.base64, "base64").toString()).toBe("new");
  });

  it("pins a positive sample by id and refuses hard negatives", () => {
    const root = mkdtempSync(join(tmpdir(), "jarvis-smoke-"));
    const path = join(root, "calibration.json");
    writeBundle(path);

    expect(loadJarvisLiveSmokeFixture({ cwd: root, audioPath: path, sampleId: "quiet" }).label)
      .toBe("quiet:quiet");
    expect(() => loadJarvisLiveSmokeFixture({ cwd: root, audioPath: path, sampleId: "negative" }))
      .toThrow("missing or is not a positive wake example");
  });

  it("discovers the newest browser export in Downloads", () => {
    const home = mkdtempSync(join(tmpdir(), "jarvis-smoke-home-"));
    const downloads = join(home, "Downloads");
    mkdirSync(downloads);
    const path = join(downloads, "jarvis-calibration-123.json");
    writeBundle(path);

    expect(loadJarvisLiveSmokeFixture({ home }).sourcePath).toBe(path);
  });
});
