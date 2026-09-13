import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { extname, join, resolve } from "node:path";

const POSITIVE_CATEGORIES = ["normal", "quiet", "distant", "noisy"];

function mediaTypeFor(path) {
  const extension = extname(path).toLowerCase();
  if (extension === ".wav") return "audio/wav";
  if (extension === ".mp3") return "audio/mpeg";
  if (extension === ".m4a") return "audio/mp4";
  if (extension === ".ogg" || extension === ".opus") return "audio/ogg";
  return "audio/webm";
}

function newestCalibrationExport(home = homedir()) {
  const candidates = [];
  for (const directory of [join(home, "Downloads"), join(home, "Documents")]) {
    if (!existsSync(directory)) continue;
    for (const name of readdirSync(directory)) {
      if (!/^jarvis-calibration-.*\.json$/i.test(name)) continue;
      const path = join(directory, name);
      const stats = statSync(path);
      if (stats.isFile()) candidates.push({ path, modified: stats.mtimeMs });
    }
  }
  return candidates.sort((left, right) => right.modified - left.modified)[0]?.path ?? null;
}

function decodeCalibrationExport(path, options = {}) {
  let bundle;
  try {
    bundle = JSON.parse(readFileSync(path, "utf8"));
  } catch (cause) {
    throw new Error(`Unable to read Jarvis calibration export ${path}: ${cause instanceof Error ? cause.message : String(cause)}`);
  }
  if (bundle?.version !== 1 || !Array.isArray(bundle.samples)) {
    throw new Error(`Jarvis calibration export ${path} must use the version 1 samples format`);
  }

  const requestedCategory = options.category?.trim().toLowerCase() || "normal";
  const requestedId = options.sampleId?.trim() || "";
  if (!POSITIVE_CATEGORIES.includes(requestedCategory)) {
    throw new Error(`Smoke-test category must be one of ${POSITIVE_CATEGORIES.join(", ")}; hard negatives cannot start a voice chat`);
  }
  const eligible = bundle.samples.filter((sample) => (
    sample
    && typeof sample.id === "string"
    && typeof sample.audio_base64 === "string"
    && sample.audio_base64.length > 0
    && POSITIVE_CATEGORIES.includes(String(sample.category))
  ));
  const selected = requestedId
    ? eligible.find((sample) => sample.id === requestedId)
    : [...eligible].reverse().find((sample) => sample.category === requestedCategory);
  if (!selected) {
    throw new Error(requestedId
      ? `Calibration sample ${requestedId} is missing or is not a positive wake example`
      : `Calibration export ${path} has no positive ${requestedCategory} sample`);
  }
  const bytes = Buffer.from(selected.audio_base64, "base64");
  if (!bytes.length) throw new Error(`Calibration sample ${selected.id} contains no audio`);
  return {
    base64: bytes.toString("base64"),
    mimeType: typeof selected.mime_type === "string" && selected.mime_type
      ? selected.mime_type
      : "audio/webm",
    label: `${selected.category}:${selected.id}`,
    sourcePath: path,
  };
}

export function loadJarvisLiveSmokeFixture(options = {}) {
  const explicit = options.audioPath?.trim()
    || process.env.JARVIS_SMOKE_AUDIO?.trim()
    || newestCalibrationExport(options.home);
  if (!explicit) {
    throw new Error(
      "No Jarvis wake audio was found. Export a positive sample from Settings -> Wake calibration, "
      + "then run npm run test:live-smoke -- --audio <jarvis-calibration-....json>.",
    );
  }
  const path = resolve(options.cwd ?? process.cwd(), explicit);
  if (!existsSync(path)) throw new Error(`Jarvis smoke-test audio does not exist: ${path}`);
  if (extname(path).toLowerCase() === ".json") {
    return decodeCalibrationExport(path, {
      category: options.category ?? process.env.JARVIS_SMOKE_CATEGORY,
      sampleId: options.sampleId ?? process.env.JARVIS_SMOKE_SAMPLE_ID,
    });
  }
  const bytes = readFileSync(path);
  if (!bytes.length) throw new Error(`Jarvis smoke-test audio is empty: ${path}`);
  return {
    base64: bytes.toString("base64"),
    mimeType: mediaTypeFor(path),
    label: `audio:${path.split(/[\\/]/).pop()}`,
    sourcePath: path,
  };
}

export const jarvisLivePositiveCategories = Object.freeze([...POSITIVE_CATEGORIES]);
