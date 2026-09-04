import { Float32RingBuffer } from "./audio-ring-buffer";
import {
  JARVIS_WAKE_RING_SECONDS,
  JARVIS_WAKE_SAMPLE_RATE,
} from "./jarvis-wake-protocol";

export interface JarvisWakeInferenceResult {
  speechProbability: number;
  wakeProbability: number;
}

export interface JarvisWakeInference {
  analyze: (audio: Float32Array) => Promise<JarvisWakeInferenceResult>;
  destroy?: () => Promise<void> | void;
}

export interface JarvisWakeEngineOptions {
  inference: JarvisWakeInference;
  onDetected: (probability: number) => void;
  onUtterance: (samples: Float32Array) => void;
  onError: (cause: unknown) => void;
  onSpeechStart?: (probability: number) => void;
  onSpeechEnd?: (probability: number) => void;
  wakeThreshold?: number;
  candidateWakeThreshold?: number;
  candidateWakeHits?: number;
  speechLookbackMs?: number;
  candidateLookbackMs?: number;
  positiveVadThreshold?: number;
  negativeVadThreshold?: number;
  negativeVadCount?: number;
  batchSamples?: number;
  batchIntervalSamples?: number;
  maximumUtteranceSamples?: number;
}

/**
 * Worker-owned streaming engine. Audio is bounded before a wake and bounded
 * again while collecting the one-shot command that follows it.
 */
export class JarvisWakeEngine {
  private readonly inference: JarvisWakeInference;
  private readonly options: Required<Omit<
    JarvisWakeEngineOptions,
    "inference" | "onDetected" | "onUtterance" | "onError" | "onSpeechStart" | "onSpeechEnd"
  >> & Pick<
    JarvisWakeEngineOptions,
    "onDetected" | "onUtterance" | "onError" | "onSpeechStart" | "onSpeechEnd"
  >;
  private readonly history = new Float32RingBuffer(
    Math.round(JARVIS_WAKE_SAMPLE_RATE * JARVIS_WAKE_RING_SECONDS),
  );
  private readonly inferenceWindow: Float32RingBuffer;
  private intervalSamples = 0;
  private queuedWindow: Float32Array | null = null;
  private drainPromise: Promise<void> | null = null;
  private armed = true;
  private recording = false;
  private recordingChunks: Float32Array[] = [];
  private recordingSamples = 0;
  private negativeCount = 0;
  private recentSpeechWindows = 0;
  private candidateWindows = 0;
  private candidateHits = 0;
  private candidateProbability = 0;
  private speechActive = false;
  private destroyed = false;

  constructor(options: JarvisWakeEngineOptions) {
    this.inference = options.inference;
    this.options = {
      onDetected: options.onDetected,
      onUtterance: options.onUtterance,
      onError: options.onError,
      onSpeechStart: options.onSpeechStart,
      onSpeechEnd: options.onSpeechEnd,
      wakeThreshold: options.wakeThreshold ?? 0.45,
      candidateWakeThreshold: options.candidateWakeThreshold ?? 0.18,
      candidateWakeHits: options.candidateWakeHits ?? 2,
      speechLookbackMs: options.speechLookbackMs ?? 1_200,
      candidateLookbackMs: options.candidateLookbackMs ?? 960,
      positiveVadThreshold: options.positiveVadThreshold ?? 0.65,
      negativeVadThreshold: options.negativeVadThreshold ?? 0.4,
      negativeVadCount: options.negativeVadCount ?? 8,
      batchSamples: options.batchSamples ?? 17_280,
      batchIntervalSamples: options.batchIntervalSamples ?? 1_920,
      maximumUtteranceSamples: options.maximumUtteranceSamples ?? JARVIS_WAKE_SAMPLE_RATE * 30,
    };
    this.inferenceWindow = new Float32RingBuffer(this.options.batchSamples);
  }

  push(samples: Float32Array): void {
    if (this.destroyed || !this.armed || !samples.length) return;
    this.history.push(samples);
    this.inferenceWindow.push(samples);
    if (this.recording) {
      const copy = samples.slice();
      this.recordingChunks.push(copy);
      this.recordingSamples += copy.length;
      if (this.recordingSamples >= this.options.maximumUtteranceSamples) {
        this.finishUtterance();
        return;
      }
    }

    this.intervalSamples += samples.length;
    if (
      this.inferenceWindow.length < this.options.batchSamples
      || this.intervalSamples < this.options.batchIntervalSamples
    ) return;
    this.intervalSamples %= this.options.batchIntervalSamples;
    this.queuedWindow = this.inferenceWindow.snapshot();
    this.startDrain();
  }

  pause(): void {
    if (this.destroyed) return;
    this.armed = false;
    this.resetAudio();
  }

  resume(): void {
    if (this.destroyed) return;
    this.resetAudio();
    this.armed = true;
  }

  async idle(): Promise<void> {
    while (this.drainPromise) await this.drainPromise;
  }

  async destroy(): Promise<void> {
    if (this.destroyed) return;
    this.destroyed = true;
    this.armed = false;
    this.queuedWindow?.fill(0);
    this.queuedWindow = null;
    await this.idle();
    this.resetAudio();
    this.history.destroy();
    this.inferenceWindow.destroy();
    await this.inference.destroy?.();
  }

  private startDrain(): void {
    if (this.drainPromise) return;
    this.drainPromise = this.drain().finally(() => {
      this.drainPromise = null;
      if (this.queuedWindow && this.armed && !this.destroyed) this.startDrain();
    });
  }

  private async drain(): Promise<void> {
    while (this.queuedWindow && this.armed && !this.destroyed) {
      const window = this.queuedWindow;
      this.queuedWindow = null;
      try {
        const result = await this.inference.analyze(window);
        if (!this.armed || this.destroyed) return;
        this.applyResult(result);
      } catch (cause) {
        this.pause();
        this.options.onError(cause);
        return;
      } finally {
        window.fill(0);
      }
    }
  }

  private applyResult(result: JarvisWakeInferenceResult): void {
    this.observeSpeechBoundary(result.speechProbability);
    if (!this.recording) {
      this.advanceRecallGate(result);
      const strongWake = result.wakeProbability >= this.options.wakeThreshold;
      const repeatedCandidate = this.candidateHits >= this.options.candidateWakeHits;
      if (this.recentSpeechWindows > 0 && (strongWake || repeatedCandidate)) {
        this.recording = true;
        const preRoll = this.history.snapshot();
        this.recordingChunks = [preRoll];
        this.recordingSamples = preRoll.length;
        this.negativeCount = 0;
        this.options.onDetected(Math.max(result.wakeProbability, this.candidateProbability));
        this.resetRecallGate();
      }
      return;
    }

    if (result.speechProbability < this.options.negativeVadThreshold) {
      this.negativeCount += 1;
    } else if (result.speechProbability >= this.options.positiveVadThreshold) {
      this.negativeCount = 0;
    }
    if (this.negativeCount > this.options.negativeVadCount) this.finishUtterance();
  }

  private observeSpeechBoundary(probability: number): void {
    if (!this.speechActive && probability >= this.options.positiveVadThreshold) {
      this.speechActive = true;
      this.options.onSpeechStart?.(probability);
      return;
    }
    if (this.speechActive && probability < this.options.negativeVadThreshold) {
      this.speechActive = false;
      this.options.onSpeechEnd?.(probability);
    }
  }

  private advanceRecallGate(result: JarvisWakeInferenceResult): void {
    const intervalMs = this.options.batchIntervalSamples / JARVIS_WAKE_SAMPLE_RATE * 1_000;
    const speechWindowCount = Math.max(1, Math.ceil(this.options.speechLookbackMs / intervalMs));
    const candidateWindowCount = Math.max(1, Math.ceil(this.options.candidateLookbackMs / intervalMs));

    if (result.speechProbability >= this.options.positiveVadThreshold) {
      this.recentSpeechWindows = speechWindowCount;
    } else if (this.recentSpeechWindows > 0) {
      this.recentSpeechWindows -= 1;
    }

    if (this.candidateWindows > 0) {
      this.candidateWindows -= 1;
    } else {
      this.candidateHits = 0;
      this.candidateProbability = 0;
    }
    if (result.wakeProbability >= this.options.candidateWakeThreshold) {
      this.candidateHits += 1;
      this.candidateProbability = Math.max(this.candidateProbability, result.wakeProbability);
      this.candidateWindows = candidateWindowCount;
    }
  }

  private resetRecallGate(): void {
    this.recentSpeechWindows = 0;
    this.candidateWindows = 0;
    this.candidateHits = 0;
    this.candidateProbability = 0;
  }

  private finishUtterance(): void {
    if (!this.recording) return;
    const utterance = new Float32Array(this.recordingSamples);
    let offset = 0;
    for (const chunk of this.recordingChunks) {
      utterance.set(chunk, offset);
      offset += chunk.length;
      chunk.fill(0);
    }
    this.recordingChunks = [];
    this.recordingSamples = 0;
    this.recording = false;
    this.armed = false;
    this.queuedWindow?.fill(0);
    this.queuedWindow = null;
    this.history.clear();
    this.inferenceWindow.clear();
    this.options.onUtterance(utterance);
  }

  private resetAudio(): void {
    this.queuedWindow?.fill(0);
    this.queuedWindow = null;
    this.history.clear();
    this.inferenceWindow.clear();
    for (const chunk of this.recordingChunks) chunk.fill(0);
    this.recordingChunks = [];
    this.recordingSamples = 0;
    this.recording = false;
    this.negativeCount = 0;
    this.intervalSamples = 0;
    this.speechActive = false;
    this.resetRecallGate();
  }
}
