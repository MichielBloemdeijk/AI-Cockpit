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

  async observeSpeech(
    frame: Float32Array,
    range: { startSample: number; endSample: number },
  ): Promise<{ startSample: number; endSample: number; speechProbability: number; completedMonotonicMs: number }> {
    if (frame.length !== VAD_INTERVAL_SAMPLES) {
      throw new Error(`Hey Buddy VAD requires ${VAD_INTERVAL_SAMPLES} samples`);
    }
    const input = new ort.Tensor("float32", frame, [1, frame.length]);
    const previousH = this.vadStateH;
    const previousC = this.vadStateC;
    let vadOutput: ort.InferenceSession.OnnxValueMapType;
    try {
      vadOutput = await this.vad.run({ input, h: previousH, c: previousC, sr: this.sampleRateTensor });
    } finally {
      input.dispose();
    }
    const nextH = vadOutput.hn as ort.Tensor;
    const nextC = vadOutput.cn as ort.Tensor;
    const probabilityTensor = vadOutput.output as ort.Tensor;
    const speechProbability = Number(probabilityTensor.data[0]);
    this.vadStateH = nextH;
    this.vadStateC = nextC;
    if (previousH !== nextH) previousH.dispose();
    if (previousC !== nextC) previousC.dispose();
    probabilityTensor.dispose();
    return {
      startSample: range.startSample,
      endSample: range.endSample,
      speechProbability,
      completedMonotonicMs: typeof performance !== "undefined" ? performance.now() : Date.now(),
    };
  }

  async classifyWake(audio: Float32Array): Promise<{ wakeProbability: number }> {
    if (audio.length < 17_280) return { wakeProbability: 0 };

    const spectrogramInput = new ort.Tensor("float32", audio, [1, audio.length]);
    let melOutput: ort.InferenceSession.OnnxValueMapType;
    try {
      melOutput = await this.spectrogram.run({ input: spectrogramInput });
    } finally {
      spectrogramInput.dispose();
    }
    const rawMel = melOutput.output as ort.Tensor;
    const melBins = Number(rawMel.dims.at(-1));
    const frameCount = Number(rawMel.dims.at(-2));
    if (!melBins || !frameCount) throw new Error("Hey Buddy spectrogram output is invalid");
    const normalizedMel = Float32Array.from(
      rawMel.data as Float32Array,
      (value) => Number(value) / 10 + 2,
    );
    rawMel.dispose();

    const embeddings = await this.runEmbedding(normalizedMel, frameCount, melBins);
    this.appendEmbeddingHistory(embeddings);
    let wakeProbability = 0;
    if (this.embeddingHistory.length === WAKE_EMBEDDING_FRAMES * EMBEDDING_DIMENSION) {
      const wakeInput = new ort.Tensor(
        "float32",
        this.embeddingHistory,
        [1, WAKE_EMBEDDING_FRAMES, EMBEDDING_DIMENSION],
      );
      let output: ort.InferenceSession.OnnxValueMapType;
      try {
        output = await this.wake.run({ input: wakeInput });
      } finally {
        wakeInput.dispose();
      }
      const probability = output.output as ort.Tensor;
      wakeProbability = Number(probability.data[0]);
      probability.dispose();
    }
    return { wakeProbability };
  }

  resetWakeState(): void {
    this.embeddingHistory.fill(0);
    this.embeddingHistory = new Float32Array(0);
  }

  async analyze(audio: Float32Array): Promise<JarvisWakeInferenceResult> {
    const vadInput = audio.subarray(Math.max(0, audio.length - VAD_INTERVAL_SAMPLES));
    const speech = await this.observeSpeech(
      vadInput,
      { startSample: Math.max(0, audio.length - vadInput.length), endSample: audio.length },
    );
    const wake = await this.classifyWake(audio);
    return { speechProbability: speech.speechProbability, wakeProbability: wake.wakeProbability };
  }

  async destroy(): Promise<void> {
    this.resetWakeState();
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
    const input = new ort.Tensor(
      "float32",
      stacked,
      [batchCount, EMBEDDING_WINDOW_SIZE, melBins, 1],
    );
    let output: ort.InferenceSession.OnnxValueMapType;
    try {
      output = await this.embedding.run({ input_1: input });
    } finally {
      input.dispose();
    }
    const tensor = output.conv2d_19 as ort.Tensor;
    const result = Float32Array.from(tensor.data as Float32Array);
    tensor.dispose();
    stacked.fill(0);
    return result;
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
