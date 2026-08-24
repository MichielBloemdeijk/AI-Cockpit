import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const out = join(root, "public", "vad");
mkdirSync(out, { recursive: true });

const vadRoot = join(root, "node_modules", "@ricky0123", "vad-web", "dist");
const ortRoot = join(root, "node_modules", "onnxruntime-web", "dist");
const assets = [
  [join(vadRoot, "silero_vad_v5.onnx"), join(out, "silero_vad_v5.onnx")],
  [join(vadRoot, "vad.worklet.bundle.min.js"), join(out, "vad.worklet.bundle.min.js")],
  [join(ortRoot, "ort-wasm-simd-threaded.mjs"), join(out, "ort-wasm-simd-threaded.mjs")],
  [join(ortRoot, "ort-wasm-simd-threaded.wasm"), join(out, "ort-wasm-simd-threaded.wasm")],
];
for (const [source, destination] of assets) {
  if (existsSync(source)) copyFileSync(source, destination);
}
