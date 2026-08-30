# Jarvis wake runtime provenance

The wake inference pipeline in `frontend/lib/jarvis-hey-buddy-inference.ts`
is an adaptation of **Hey Buddy** by Benjamin Paine, version `0.1.2`,
licensed under the Apache License 2.0:

- Source: https://github.com/painebenjamin/hey-buddy
- Package: https://www.npmjs.com/package/hey-buddy-onnx
- License: https://www.apache.org/licenses/LICENSE-2.0

The asset installer pins official Hey Buddy model repository revision
`68a13d58e216e8e4837559ae665ca3e9769b41c2` and verifies every shared model by
SHA-256 before writing it. The generated `models/manifest.json` records the
exact sources, hashes, byte sizes, and local Jarvis model provenance.

The official repository describes source and pretrained models as Apache-2.0
and precalculated datasets as CC-BY 4.0 with per-component licensing in the
dataset card. The Jarvis model's training run must retain its dataset manifest.
This repository intentionally does not silently use a Buddy model as a
substitute for the required exact-word Jarvis model.
