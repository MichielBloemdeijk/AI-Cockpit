/**
 * Worker-side adaptation of Hey Buddy's Apache-2.0 ONNX pipeline.
 * See frontend/public/jarvis-wake/NOTICE.md for provenance.
 */
import * as ort from "onnxruntime-web";

import type {
  JarvisWakeInference,
  JarvisWakeInferenceResult,
} from "./jarvis-wake-engine";
import type { JarvisWakeModelConfiguration } from "./jarvis-wake-protocol";

const EMBEDDING_DIMENSION = 96;
const EMBEDDING_WINDOW_SIZE = 76;
const EMBEDDING_WINDOW_STRIDE = 8;
const WAKE_EMBEDDING_FRAMES = 16;
const VAD_INTERVAL_SAMPLES = 1_920;

export class HeyBuddyOnnxInference implements JarvisWakeInference {
  private vadStateH: ort.Tensor = new ort.Tensor("float32", new Float32Array(128), [2, 1, 64]);
  private vadStateC: ort.Tensor = new ort.Tensor("float32", new Float32Array(128), [2, 1, 64]);
  private readonly sampleRateTensor: ort.Tensor = new ort.Tensor(
    "int64",
    BigInt64Array.from([BigInt(16_000)]),
    [1],
  );
  private embeddingHistory = new Float32Array(0);

  private constructor(
    private readonly vad: ort.InferenceSession,
    private readonly spectrogram: ort.InferenceSession,
    private readonly embedding: ort.InferenceSession,
    private readonly wake: ort.InferenceSession,
  ) {}

  static async create(configuration: JarvisWakeModelConfiguration): Promise<HeyBuddyOnnxInference> {
    ort.env.wasm.wasmPaths = configuration.wasmBaseUrl;
    ort.env.wasm.numThreads = 1;
    const options: ort.InferenceSession.SessionOptions = { executionProviders: ["wasm"] };
    const [vad, spectrogram, embedding, wake] = await Promise.all([
      ort.InferenceSession.create(configuration.vadModelUrl, options),
      ort.InferenceSession.create(configuration.spectrogramModelUrl, options),
      ort.InferenceSession.create(configuration.embeddingModelUrl, options),
      ort.InferenceSession.create(configuration.wakeModelUrl, options),
    ]);
    return new HeyBuddyOnnxInference(vad, spectrogram, embedding, wake);
  }

  async analyze(audio: Float32Array): Promise<JarvisWakeInferenceResult> {
    const vadInput = audio.subarray(Math.max(0, audio.length - VAD_INTERVAL_SAMPLES));
    const vadOutput = await this.vad.run({
      input: new ort.Tensor("float32", vadInput, [1, vadInput.length]),
      h: this.vadStateH,
      c: this.vadStateC,
      sr: this.sampleRateTensor,
    });
    this.vadStateH = vadOutput.hn as ort.Tensor;
    this.vadStateC = vadOutput.cn as ort.Tensor;
    const speechProbability = Number(vadOutput.output.data[0]);

    const melOutput = await this.spectrogram.run({
      input: new ort.Tensor("float32", audio, [1, audio.length]),
    });
    const rawMel = melOutput.output as ort.Tensor;
    const melBins = Number(rawMel.dims.at(-1));
    const frameCount = Number(rawMel.dims.at(-2));
    if (!melBins || !frameCount) throw new Error("Hey Buddy spectrogram output is invalid");
    const normalizedMel = Float32Array.from(
      rawMel.data as Float32Array,
      (value) => Number(value) / 10 + 2,
    );

    const embeddings = await this.runEmbedding(normalizedMel, frameCount, melBins);
    this.appendEmbeddingHistory(embeddings);
    let wakeProbability = 0;
    if (this.embeddingHistory.length === WAKE_EMBEDDING_FRAMES * EMBEDDING_DIMENSION) {
      const output = await this.wake.run({
        input: new ort.Tensor(
          "float32",
          this.embeddingHistory,
          [1, WAKE_EMBEDDING_FRAMES, EMBEDDING_DIMENSION],
        ),
      });
      wakeProbability = Number(output.output.data[0]);
    }
    return { speechProbability, wakeProbability };
  }

  async destroy(): Promise<void> {
    this.embeddingHistory.fill(0);
    this.embeddingHistory = new Float32Array(0);
    this.vadStateH.dispose();
    this.vadStateC.dispose();
    this.sampleRateTensor.dispose();
    await Promise.all([
      this.vad.release(),
      this.spectrogram.release(),
      this.embedding.release(),
      this.wake.release(),
    ]);
  }

  private async runEmbedding(
    mel: Float32Array,
    frameCount: number,
    melBins: number,
  ): Promise<Float32Array> {
    if (frameCount < EMBEDDING_WINDOW_SIZE) {
      throw new Error(`Hey Buddy requires ${EMBEDDING_WINDOW_SIZE} mel frames`);
    }
    const truncatedFrames = frameCount
      - ((frameCount - EMBEDDING_WINDOW_SIZE) % EMBEDDING_WINDOW_STRIDE);
    const batchCount = (truncatedFrames - EMBEDDING_WINDOW_SIZE)
      / EMBEDDING_WINDOW_STRIDE + 1;
    const stacked = new Float32Array(
      batchCount * EMBEDDING_WINDOW_SIZE * melBins,
    );
    for (let batch = 0; batch < batchCount; batch += 1) {
      const sourceStart = batch * EMBEDDING_WINDOW_STRIDE * melBins;
      const sourceEnd = sourceStart + EMBEDDING_WINDOW_SIZE * melBins;
      stacked.set(
        mel.subarray(sourceStart, sourceEnd),
        batch * EMBEDDING_WINDOW_SIZE * melBins,
      );
    }
    const output = await this.embedding.run({
      input_1: new ort.Tensor(
        "float32",
        stacked,
        [batchCount, EMBEDDING_WINDOW_SIZE, melBins, 1],
      ),
    });
    const tensor = output.conv2d_19 as ort.Tensor;
    return Float32Array.from(tensor.data as Float32Array);
  }

  private appendEmbeddingHistory(next: Float32Array): void {
    const maximum = WAKE_EMBEDDING_FRAMES * EMBEDDING_DIMENSION;
    const combined = new Float32Array(Math.min(maximum, this.embeddingHistory.length + next.length));
    const nextTail = next.subarray(Math.max(0, next.length - maximum));
    const retained = Math.max(0, combined.length - nextTail.length);
    if (retained) {
      combined.set(this.embeddingHistory.subarray(this.embeddingHistory.length - retained));
    }
    combined.set(nextTail, retained);
    this.embeddingHistory.fill(0);
    this.embeddingHistory = combined;
  }
}
