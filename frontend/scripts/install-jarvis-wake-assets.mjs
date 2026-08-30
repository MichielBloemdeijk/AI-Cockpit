import { createHash } from "node:crypto";
import { access, copyFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { basename, extname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HEY_BUDDY_MODEL_REVISION = "68a13d58e216e8e4837559ae665ca3e9769b41c2";
const SHARED_ASSETS = [
  {
    name: "silero-vad.onnx",
    sha256: "a35ebf52fd3ce5f1469b2a36158dba761bc47b973ea3382b3186ca15b1f5af28",
  },
  {
    name: "mel-spectrogram.onnx",
    sha256: "ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f",
  },
  {
    name: "speech-embedding.onnx",
    sha256: "70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f",
  },
];

const scriptDirectory = fileURLToPath(new URL(".", import.meta.url));
const modelDirectory = resolve(scriptDirectory, "../public/jarvis-wake/models");
const wakeArgument = process.argv.findIndex((value) => value === "--wake-model");
const wakeModelPath = wakeArgument >= 0 ? process.argv[wakeArgument + 1] : undefined;
if (!wakeModelPath) {
  throw new Error(
    "Usage: npm run install:wake-model -- --wake-model /absolute/path/to/jarvis.onnx",
  );
}

const resolvedWakeModel = resolve(wakeModelPath);
const wakeSourceName = basename(resolvedWakeModel);
const wakeExtension = extname(wakeSourceName);
if (wakeExtension.toLowerCase() !== ".onnx") {
  throw new Error(`Wake model must be an ONNX file: ${wakeSourceName}`);
}
let hasExternalData = false;
try {
  await access(`${resolvedWakeModel}.data`);
  hasExternalData = true;
} catch (cause) {
  if (cause instanceof Error && "code" in cause && cause.code !== "ENOENT") throw cause;
}
if (hasExternalData) {
  throw new Error(
    "Wake model uses an external .onnx.data file; consolidate it into one ONNX file before installation",
  );
}
const wakeBytes = await readFile(resolvedWakeModel);

await mkdir(modelDirectory, { recursive: true });
const manifestAssets = [];
for (const asset of SHARED_ASSETS) {
  const url = `https://huggingface.co/benjamin-paine/hey-buddy/resolve/${HEY_BUDDY_MODEL_REVISION}/pretrained/${asset.name}`;
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Unable to download ${asset.name}: HTTP ${response.status}`);
  const bytes = Buffer.from(await response.arrayBuffer());
  const actualHash = sha256(bytes);
  if (actualHash !== asset.sha256) {
    throw new Error(`${asset.name} failed SHA-256 verification: ${actualHash}`);
  }
  await writeFile(resolve(modelDirectory, asset.name), bytes);
  manifestAssets.push({ ...asset, source: url, bytes: bytes.length });
}
const installedWakeModel = resolve(modelDirectory, "jarvis.onnx");
await copyFile(resolvedWakeModel, installedWakeModel);
manifestAssets.push({
  name: "jarvis.onnx",
  sha256: sha256(wakeBytes),
  source: "local-training-artifact",
  source_name: wakeSourceName,
  bytes: wakeBytes.length,
});

await writeFile(
  resolve(modelDirectory, "manifest.json"),
  `${JSON.stringify({
    schema: "jarvis.wake_models.v1",
    installed_at: new Date().toISOString(),
    hey_buddy_model_revision: HEY_BUDDY_MODEL_REVISION,
    wake_model: {
      phrase: "jarvis",
      version: wakeSourceName.slice(0, -wakeExtension.length),
      training_record: "docs/JARVIS_WAKE_MODEL.md",
    },
    assets: manifestAssets,
  }, null, 2)}\n`,
);

console.log(`Installed and verified Jarvis wake assets in ${modelDirectory}`);
console.log("Set NEXT_PUBLIC_JARVIS_WAKE_MODEL_URL=/jarvis-wake/models/jarvis.onnx and rebuild.");

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}
