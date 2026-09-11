import { Float32RingBuffer } from "./audio-ring-buffer";
import {
  JARVIS_WAKE_RING_SECONDS,
  JARVIS_WAKE_SAMPLE_RATE,
} from "./jarvis-wake-protocol";
import type { JarvisAudioChunk } from "./jarvis-activation-audio";

export interface JarvisWakeInferenceResult {
  speechProbability: number;
  wakeProbability: number;
}

export interface JarvisWakeInference {
  analyze?: (audio: Float32Array) => Promise<JarvisWakeInferenceResult>;
  observeSpeech?: (
    frame: Float32Array,
    range: { startSample: number; endSample: number },
  ) => Promise<JarvisSpeechObservation>;
  classifyWake?: (window: Float32Array) => Promise<{ wakeProbability: number }>;
  resetWakeState?: () => void;
  destroy?: () => Promise<void> | void;
}

export interface JarvisSpeechObservation {
  startSample: number;
  endSample: number;
  speechProbability: number;
  completedMonotonicMs: number;
}

export interface JarvisWakeCandidateEndpoint {
  sampleRate: number;
  totalSamples: number;
  trailingSilenceMs: number;
  speechEndSample?: number;
  observedAtSample?: number;
  trailingSilenceSamples?: number;
  processingLagMs?: number;
}

export interface JarvisWakeEngineOptions {
  inference: JarvisWakeInference;
  onDetected: (probability: number) => void;
  onUtterance: (samples: Float32Array) => void;
  onError: (cause: unknown) => void;
  /** Receives candidate audio after wake detection, beginning with bounded pre-roll. */
  onCandidateAudio?: (samples: Float32Array) => void;
  /** Phase-3 ordered candidate chunks. */
  onCandidateChunk?: (chunk: JarvisAudioChunk) => void;
  onCandidateStart?: (probability: number, preRoll: Float32Array) => void;
  onCandidateEndpoint?: (endpoint: JarvisWakeCandidateEndpoint) => void;
  onCandidateClose?: (reason: "audio_backlog_overflow" | "rearmed" | "sleep") => void;
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
  /** Silence required to close a candidate. Omit to retain the legacy count behavior. */
  endpointSilenceMs?: number;
  /** Audio retained before the detected speech window as handoff safety padding. */
  preRollPaddingMs?: number;
  batchSamples?: number;
  batchIntervalSamples?: number;
  maximumUtteranceSamples?: number;
  activationId?: string;
  getActivationId?: () => string | null;
  speechObserverMode?: "provider_owned" | "client_vad";
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
      | "onCandidateAudio" | "onCandidateChunk" | "onCandidateStart" | "onCandidateEndpoint"
      | "onCandidateClose" | "endpointSilenceMs" | "activationId" | "getActivationId"
      | "speechObserverMode"
  >> & Pick<
    JarvisWakeEngineOptions,
    "onDetected" | "onUtterance" | "onError" | "onSpeechStart" | "onSpeechEnd"
  > & Pick<JarvisWakeEngineOptions, "endpointSilenceMs" | "onCandidateAudio" | "onCandidateChunk"
    | "onCandidateStart" | "onCandidateEndpoint" | "onCandidateClose" | "activationId" | "getActivationId"
    | "speechObserverMode">;
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
  private readonly splitInference: boolean;
  private speechBuffer = new Float32Array(0);
  private speechBufferStartSample = 0;
  private captureSample = 0;
  private captureMonotonicMs = 0;
  private readonly speechFrames: Array<{
    frame: Float32Array;
    startSample: number;
    endSample: number;
    capturedEndMonotonicMs: number;
  }> = [];
  private speechDrainPromise: Promise<void> | null = null;
  private wakeWindow = new Float32RingBuffer(17_280);
  private queuedWakeWindow: Float32Array | null = null;
  private queuedWakeSpeechEligible = false;
  private wakeDrainPromise: Promise<void> | null = null;
  private splitDetected = false;
  private splitArmToken = 0;
  private splitCandidateSequence = 0;
  private splitCandidateSample = 0;
  private splitCandidateStartSourceSample = 0;
  private splitCandidatePending = new Float32Array(0);
  private splitCandidatePendingCapturedMs: number | null = null;
  private splitLastVoicedEndSample = 0;
  private splitEndpointEmitted = false;
  private splitBelowThresholdSamples = 0;
  private splitCandidateActive = false;
  private splitSpeechObserverOverflowed = false;

  constructor(options: JarvisWakeEngineOptions) {
    this.inference = options.inference;
    this.options = {
      onDetected: options.onDetected,
      onUtterance: options.onUtterance,
      onError: options.onError,
      onCandidateAudio: options.onCandidateAudio,
      onCandidateChunk: options.onCandidateChunk,
      onCandidateStart: options.onCandidateStart,
      onCandidateEndpoint: options.onCandidateEndpoint,
      onCandidateClose: options.onCandidateClose,
      onSpeechStart: options.onSpeechStart,
      onSpeechEnd: options.onSpeechEnd,
      wakeThreshold: options.wakeThreshold ?? 0.45,
      candidateWakeThreshold: options.candidateWakeThreshold ?? 0.18,
      candidateWakeHits: options.candidateWakeHits ?? 2,
      speechLookbackMs: options.speechLookbackMs ?? 1_200,
      candidateLookbackMs: options.candidateLookbackMs ?? 960,
      endpointSilenceMs: options.endpointSilenceMs,
      preRollPaddingMs: options.preRollPaddingMs ?? 300,
      positiveVadThreshold: options.positiveVadThreshold ?? 0.65,
      negativeVadThreshold: options.negativeVadThreshold ?? 0.4,
      negativeVadCount: options.negativeVadCount ?? 4,
      batchSamples: options.batchSamples ?? 17_280,
      batchIntervalSamples: options.batchIntervalSamples ?? 1_920,
      maximumUtteranceSamples: options.maximumUtteranceSamples ?? JARVIS_WAKE_SAMPLE_RATE * 30,
      activationId: options.activationId,
      getActivationId: options.getActivationId,
      speechObserverMode: options.speechObserverMode ?? "provider_owned",
    };
    this.inferenceWindow = new Float32RingBuffer(this.options.batchSamples);
    this.splitInference = Boolean(
      this.inference.observeSpeech
      && this.inference.classifyWake,
    );
  }

  push(samples: Float32Array, capturedMonotonicMs = this.now()): void {
    if (this.splitInference) {
      this.pushSplit(samples, capturedMonotonicMs);
      return;
    }
    if (this.destroyed || !this.armed || !samples.length) return;
    this.history.push(samples);
    this.inferenceWindow.push(samples);
    if (this.recording) {
      const copy = samples.slice();
      this.recordingChunks.push(copy);
      this.recordingSamples += copy.length;
      this.options.onCandidateAudio?.(samples.slice());
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
    if (this.splitInference && this.splitCandidateActive) {
      this.options.onCandidateClose?.("sleep");
    }
    this.armed = false;
    this.resetAudio();
  }

  resume(): void {
    if (this.destroyed) return;
    this.resetAudio();
    this.inference.resetWakeState?.();
    this.armed = true;
  }

  rearm(): void {
    this.resume();
  }

  async idle(): Promise<void> {
    while (this.drainPromise || this.speechDrainPromise || this.wakeDrainPromise) {
      await Promise.all([
        this.drainPromise,
        this.speechDrainPromise,
        this.wakeDrainPromise,
      ].filter((value): value is Promise<void> => Boolean(value)));
    }
  }

  async destroy(): Promise<void> {
    if (this.destroyed) return;
    if (this.splitInference && this.splitCandidateActive) {
      this.options.onCandidateClose?.("sleep");
    }
    this.destroyed = true;
    this.armed = false;
    this.queuedWindow?.fill(0);
    this.queuedWindow = null;
    await this.idle();
    this.resetAudio();
    this.history.destroy();
    this.inferenceWindow.destroy();
    this.wakeWindow.destroy();
    await this.inference.destroy?.();
  }

  private now(): number {
    return typeof performance !== "undefined" && typeof performance.now === "function"
      ? performance.now()
      : Date.now();
  }

  private pushSplit(samples: Float32Array, capturedMonotonicMs: number): void {
    if (this.destroyed || !this.armed || !samples.length) return;
    const startSample = this.captureSample;
    this.captureSample += samples.length;
    this.captureMonotonicMs = capturedMonotonicMs + samples.length / JARVIS_WAKE_SAMPLE_RATE * 1_000;
    this.history.push(samples);
    if (!this.splitDetected) this.wakeWindow.push(samples);
    if (this.splitCandidateActive) {
      this.emitCandidateSamples(samples, capturedMonotonicMs);
    }

    // Provider-owned turn detection may continue indefinitely after the wake
    // word. A duration limit here would conflate the global capture timeline
    // with the candidate-local timeline and truncate otherwise healthy turns.
    // Backpressure is enforced by the bounded observer and delivery queues.
    if (this.splitSpeechObserverOverflowed) return;

    const pending = this.speechBuffer;
    const combined = new Float32Array(pending.length + samples.length);
    combined.set(pending);
    combined.set(samples, pending.length);
    const combinedStart = pending.length
      ? this.speechBufferStartSample
      : startSample;
    let offset = 0;
    while (combined.length - offset >= 1_920) {
      const end = offset + 1_920;
      this.speechFrames.push({
        frame: combined.slice(offset, end),
        startSample: combinedStart + offset,
        endSample: combinedStart + end,
        capturedEndMonotonicMs: capturedMonotonicMs
          + (combinedStart + end - startSample) / JARVIS_WAKE_SAMPLE_RATE * 1_000,
      });
      offset = end;
    }
    this.speechBuffer = combined.slice(offset);
    this.speechBufferStartSample = combinedStart + offset;
    combined.fill(0);
    if (this.speechFrames.reduce((total, item) => total + item.frame.length, 0)
      > JARVIS_WAKE_SAMPLE_RATE * 30) {
      this.splitSpeechObserverOverflowed = true;
      for (const item of this.speechFrames) item.frame.fill(0);
      this.speechFrames.length = 0;
      if (this.options.speechObserverMode === "client_vad" || !this.splitDetected) {
        this.splitCandidateActive = false;
        if (this.splitDetected) this.options.onCandidateClose?.("audio_backlog_overflow");
        this.armed = false;
        this.options.onError(new Error("speech_observer_overflow"));
      }
      return;
    }
    this.startSpeechDrain();
  }

  private startSpeechDrain(): void {
    if (this.speechDrainPromise) return;
    this.speechDrainPromise = this.drainSpeechFrames().finally(() => {
      this.speechDrainPromise = null;
      if (this.speechFrames.length && this.armed && !this.destroyed) {
        this.startSpeechDrain();
      }
    });
  }

  private async drainSpeechFrames(): Promise<void> {
    const observeSpeech = this.inference.observeSpeech;
    if (!observeSpeech) return;
    while (this.speechFrames.length && this.armed && !this.destroyed) {
      const item = this.speechFrames.shift();
      if (!item) continue;
      try {
        const result = await observeSpeech.call(
          this.inference,
          item.frame,
          { startSample: item.startSample, endSample: item.endSample },
        );
        if (!this.armed || this.destroyed || this.splitSpeechObserverOverflowed) return;
        this.applySplitSpeech(result, item.capturedEndMonotonicMs);
        if (!this.splitDetected && this.wakeWindow.length >= 17_280) {
          this.queuedWakeWindow?.fill(0);
          this.queuedWakeWindow = this.wakeWindow.snapshot();
          this.queuedWakeSpeechEligible = this.splitSpeechActive || (
            this.splitLastVoicedEndSample > 0
            && result.endSample - this.splitLastVoicedEndSample
              <= Math.ceil(this.options.speechLookbackMs / 1_000 * JARVIS_WAKE_SAMPLE_RATE)
          );
          this.startWakeDrain(this.splitArmToken);
        }
      } catch (cause) {
        this.options.onError(cause);
        this.pause();
        return;
      } finally {
        item.frame.fill(0);
      }
    }
  }

  private startWakeDrain(armToken: number): void {
    if (this.wakeDrainPromise) return;
    this.wakeDrainPromise = this.drainWakeWindows(armToken).finally(() => {
      this.wakeDrainPromise = null;
      if (this.queuedWakeWindow && !this.splitDetected && this.armed && !this.destroyed) {
        this.startWakeDrain(this.splitArmToken);
      }
    });
  }

  private async drainWakeWindows(armToken: number): Promise<void> {
    const classifyWake = this.inference.classifyWake;
    if (!classifyWake) return;
    while (
      this.queuedWakeWindow
      && !this.splitDetected
      && this.armed
      && !this.destroyed
    ) {
      const window = this.queuedWakeWindow;
      this.queuedWakeWindow = null;
      const speechEligible = this.queuedWakeSpeechEligible;
      this.queuedWakeSpeechEligible = false;
      try {
        const result = await classifyWake.call(this.inference, window);
        this.advanceSplitRecallGate(result.wakeProbability);
        const strongWake = result.wakeProbability >= this.options.wakeThreshold;
        const repeatedCandidate = this.candidateHits >= this.options.candidateWakeHits;
        if (
          armToken === this.splitArmToken
          && !this.splitDetected
          && this.armed
          && speechEligible
          && (strongWake || repeatedCandidate)
        ) {
          this.beginSplitCandidate(Math.max(result.wakeProbability, this.candidateProbability));
        }
      } catch (cause) {
        this.options.onError(cause);
        this.pause();
        return;
      } finally {
        window.fill(0);
      }
    }
  }

  private splitSpeechActive = false;

  private advanceSplitRecallGate(wakeProbability: number): void {
    const intervalMs = 1_920 / JARVIS_WAKE_SAMPLE_RATE * 1_000;
    const candidateWindowCount = Math.max(
      1,
      Math.ceil(this.options.candidateLookbackMs / intervalMs),
    );
    if (this.candidateWindows > 0) {
      this.candidateWindows -= 1;
    } else {
      this.candidateHits = 0;
      this.candidateProbability = 0;
    }
    if (wakeProbability >= this.options.candidateWakeThreshold) {
      this.candidateHits += 1;
      this.candidateProbability = Math.max(this.candidateProbability, wakeProbability);
      this.candidateWindows = candidateWindowCount;
    }
  }

  private applySplitSpeech(
    result: JarvisSpeechObservation,
    capturedEndMonotonicMs: number,
  ): void {
    const positive = result.speechProbability >= this.options.positiveVadThreshold;
    const negative = result.speechProbability < this.options.negativeVadThreshold;
    if (!this.splitSpeechActive && positive) {
      this.splitSpeechActive = true;
      this.splitLastVoicedEndSample = result.endSample;
      this.splitBelowThresholdSamples = 0;
      this.options.onSpeechStart?.(result.speechProbability);
    } else if (this.splitSpeechActive && negative) {
      this.splitSpeechActive = false;
      this.options.onSpeechEnd?.(result.speechProbability);
    }
    if (positive) {
      this.splitLastVoicedEndSample = result.endSample;
      this.splitBelowThresholdSamples = 0;
    } else if (negative) {
      this.splitBelowThresholdSamples += Math.max(0, result.endSample - result.startSample);
    } else {
      // Hysteresis frames are neither speech nor silence. They deliberately
      // break a run of endpoint silence without toggling the speech boundary.
      this.splitBelowThresholdSamples = 0;
    }
    if (!this.splitDetected || this.splitEndpointEmitted || !negative) return;
    const endpointSamples = this.options.endpointSilenceMs === undefined
      ? this.options.negativeVadCount * 1_920
      : Math.max(
        1_920,
        Math.ceil(this.options.endpointSilenceMs / 1_000 * JARVIS_WAKE_SAMPLE_RATE),
      );
    if (this.splitBelowThresholdSamples < endpointSamples) return;
    this.splitEndpointEmitted = true;
    const observedAtSample = result.endSample;
    this.options.onCandidateEndpoint?.({
      sampleRate: JARVIS_WAKE_SAMPLE_RATE,
      totalSamples: this.splitCandidateSample,
      trailingSilenceMs: this.splitBelowThresholdSamples / JARVIS_WAKE_SAMPLE_RATE * 1_000,
      speechEndSample: Math.max(
        0,
        this.splitLastVoicedEndSample - this.splitCandidateStartSourceSample,
      ),
      observedAtSample: Math.max(
        0,
        observedAtSample - this.splitCandidateStartSourceSample,
      ),
      trailingSilenceSamples: this.splitBelowThresholdSamples,
      processingLagMs: Math.max(0, this.now() - capturedEndMonotonicMs),
    });
  }

  private beginSplitCandidate(probability: number): void {
    // The state transition happens before callbacks, preventing a callback
    // that synchronously pushes audio from re-arming classification.
    this.splitDetected = true;
    this.splitCandidateActive = true;
    this.splitArmToken += 1;
    this.splitEndpointEmitted = false;
    this.splitCandidateSequence = 0;
    this.splitCandidateSample = 0;
    this.splitCandidatePending.fill(0);
    this.splitCandidatePending = new Float32Array(0);
    this.splitCandidatePendingCapturedMs = null;
    this.resetRecallGate();
    const history = this.history.snapshot();
    const preRollLimit = Math.min(
      history.length,
      Math.round((this.options.speechLookbackMs + this.options.preRollPaddingMs)
        / 1_000 * JARVIS_WAKE_SAMPLE_RATE),
    );
    const preRoll = history.length > preRollLimit
      ? history.slice(history.length - preRollLimit)
      : history;
    this.splitCandidateStartSourceSample = Math.max(0, this.captureSample - preRoll.length);
    if (preRoll !== history) history.fill(0);
    this.options.onDetected(probability);
    const activationId = this.options.activationId
      ?? this.options.getActivationId?.()
      ?? "activation-pending";
    this.options.onCandidateStart?.(probability, preRoll.slice());
    this.emitCandidateSamples(preRoll, this.now() - preRoll.length / JARVIS_WAKE_SAMPLE_RATE * 1_000, activationId);
    preRoll.fill(0);
  }

  private emitCandidateSamples(
    samples: Float32Array,
    capturedMonotonicMs: number,
    activationId = this.options.activationId ?? this.options.getActivationId?.() ?? "activation-pending",
  ): void {
    if (!samples.length) return;
    this.options.onCandidateAudio?.(samples.slice());
    if (!this.options.onCandidateChunk) return;
    const pendingLength = this.splitCandidatePending.length;
    const combined = new Float32Array(pendingLength + samples.length);
    combined.set(this.splitCandidatePending);
    combined.set(samples, pendingLength);
    const combinedStartMs = this.splitCandidatePendingCapturedMs ?? capturedMonotonicMs;
    let offset = 0;
    while (combined.length - offset >= 320) {
      const chunkSamples = combined.slice(offset, offset + 320);
      const chunk: JarvisAudioChunk = {
        activationId,
        sequence: this.splitCandidateSequence,
        sampleRate: 16_000,
        startSample: this.splitCandidateSample,
        endSample: this.splitCandidateSample + chunkSamples.length,
        capturedMonotonicMs: combinedStartMs + offset / 16_000 * 1_000,
        samples: chunkSamples,
      };
      this.splitCandidateSequence += 1;
      this.splitCandidateSample = chunk.endSample;
      try {
        this.options.onCandidateChunk(chunk);
      } finally {
        chunk.samples.fill(0);
      }
      offset += 320;
    }
    this.splitCandidatePending = combined.slice(offset);
    this.splitCandidatePendingCapturedMs = this.splitCandidatePending.length
      ? combinedStartMs + offset / 16_000 * 1_000
      : null;
    combined.fill(0);
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
        const analyze = this.inference.analyze;
        if (!analyze) throw new Error("Wake inference does not expose the legacy analyze method");
        const result = await analyze.call(this.inference, window);
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
        const history = this.history.snapshot();
        const preRollLimit = Math.min(
          history.length,
          Math.round((this.options.speechLookbackMs + this.options.preRollPaddingMs)
            / 1_000 * JARVIS_WAKE_SAMPLE_RATE),
        );
        const preRoll = history.length > preRollLimit
          ? history.slice(history.length - preRollLimit)
          : history;
        if (preRoll !== history) history.fill(0);
        const recordingPreRoll = preRoll.slice();
        this.recordingChunks = [recordingPreRoll];
        this.recordingSamples = recordingPreRoll.length;
        this.negativeCount = 0;
        const probability = Math.max(result.wakeProbability, this.candidateProbability);
        this.options.onDetected(probability);
        this.options.onCandidateStart?.(probability, preRoll);
        this.resetRecallGate();
      }
      return;
    }

    if (result.speechProbability < this.options.negativeVadThreshold) {
      this.negativeCount += 1;
    } else if (result.speechProbability >= this.options.positiveVadThreshold) {
      this.negativeCount = 0;
    }
    const intervalMs = this.options.batchIntervalSamples / JARVIS_WAKE_SAMPLE_RATE * 1_000;
    const silenceWindows = this.options.endpointSilenceMs === undefined
      ? this.negativeCount > this.options.negativeVadCount
      : this.negativeCount >= Math.max(
        1,
        Math.ceil(this.options.endpointSilenceMs / intervalMs),
      );
    if (silenceWindows) this.finishUtterance();
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
    this.options.onCandidateEndpoint?.({
      sampleRate: JARVIS_WAKE_SAMPLE_RATE,
      totalSamples: utterance.length,
      trailingSilenceMs: this.options.endpointSilenceMs ?? 0,
    });
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
    this.speechBuffer.fill(0);
    this.speechBuffer = new Float32Array(0);
    for (const item of this.speechFrames) item.frame.fill(0);
    this.speechFrames.length = 0;
    this.queuedWakeWindow?.fill(0);
    this.queuedWakeWindow = null;
    this.queuedWakeSpeechEligible = false;
    this.splitDetected = false;
    this.splitCandidateActive = false;
    this.splitEndpointEmitted = false;
    this.splitCandidateSequence = 0;
    this.splitCandidateSample = 0;
    this.splitCandidatePending.fill(0);
    this.splitCandidatePending = new Float32Array(0);
    this.splitCandidatePendingCapturedMs = null;
    this.splitBelowThresholdSamples = 0;
    this.splitLastVoicedEndSample = 0;
    this.splitSpeechActive = false;
    this.captureSample = 0;
    this.captureMonotonicMs = 0;
    this.speechBufferStartSample = 0;
    this.splitSpeechObserverOverflowed = false;
    if (!this.destroyed && this.splitInference) this.wakeWindow.clear();
    this.splitArmToken += 1;
  }
}
