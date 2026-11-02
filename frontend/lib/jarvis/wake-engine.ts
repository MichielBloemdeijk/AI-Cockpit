import { Float32RingBuffer } from "../audio-ring-buffer";
import {
  JARVIS_WAKE_RING_SECONDS,
  JARVIS_WAKE_SAMPLE_RATE,
} from "./wake-protocol";
import type { JarvisAudioChunk } from "./activation-audio";

/** Speech is observed on short frames; the wake word on a sliding 1.08 s window. */
export interface JarvisWakeInference {
  observeSpeech(
    frame: Float32Array,
    range: { startSample: number; endSample: number },
  ): Promise<JarvisSpeechObservation>;
  classifyWake(window: Float32Array): Promise<{ wakeProbability: number }>;
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
  onError: (cause: unknown) => void;
  /** Ordered candidate chunks, starting with the bounded pre-roll before the wake word. */
  onCandidateChunk?: (chunk: JarvisAudioChunk) => void;
  onCandidateStart?: (probability: number) => void;
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
  /** Negative VAD frames that close a candidate when `endpointSilenceMs` is not set. */
  negativeVadCount?: number;
  /** Silence required to close a candidate. */
  endpointSilenceMs?: number;
  /** Audio retained before the detected speech window as handoff safety padding. */
  preRollPaddingMs?: number;
  activationId?: string;
  getActivationId?: () => string | null;
  speechObserverMode?: "provider_owned" | "client_vad";
}

const SPEECH_FRAME_SAMPLES = 1_920;
const WAKE_WINDOW_SAMPLES = 17_280;
const CHUNK_SAMPLES = 320;

/**
 * Worker-owned streaming engine. Speech frames and wake windows run on separate
 * bounded queues; after a wake, the candidate turn (pre-roll first) streams out
 * as ordered chunks until local endpointing or the provider ends it.
 */
export class JarvisWakeEngine {
  private readonly inference: JarvisWakeInference;
  private readonly options: Required<Pick<
    JarvisWakeEngineOptions,
    "wakeThreshold" | "candidateWakeThreshold" | "candidateWakeHits" | "speechLookbackMs"
      | "candidateLookbackMs" | "positiveVadThreshold" | "negativeVadThreshold" | "negativeVadCount"
      | "preRollPaddingMs" | "speechObserverMode"
  >> & Omit<JarvisWakeEngineOptions, "inference">;
  private readonly history = new Float32RingBuffer(
    Math.round(JARVIS_WAKE_SAMPLE_RATE * JARVIS_WAKE_RING_SECONDS),
  );
  private armed = true;
  private destroyed = false;
  private candidateWindows = 0;
  private candidateHits = 0;
  private candidateProbability = 0;
  private speechBuffer = new Float32Array(0);
  private speechBufferStartSample = 0;
  private captureSample = 0;
  private readonly speechFrames: Array<{
    frame: Float32Array;
    startSample: number;
    endSample: number;
    capturedEndMonotonicMs: number;
  }> = [];
  private speechDrainPromise: Promise<void> | null = null;
  private wakeWindow = new Float32RingBuffer(WAKE_WINDOW_SAMPLES);
  private queuedWakeWindow: Float32Array | null = null;
  private queuedWakeSpeechEligible = false;
  private wakeDrainPromise: Promise<void> | null = null;
  private detected = false;
  private armToken = 0;
  private candidateSequence = 0;
  private candidateSample = 0;
  private candidateStartSourceSample = 0;
  private candidatePending = new Float32Array(0);
  private candidatePendingCapturedMs: number | null = null;
  private lastVoicedEndSample = 0;
  private endpointEmitted = false;
  private belowThresholdSamples = 0;
  private candidateActive = false;
  private speechObserverOverflowed = false;
  private speechActive = false;

  constructor(options: JarvisWakeEngineOptions) {
    this.inference = options.inference;
    this.options = {
      ...options,
      wakeThreshold: options.wakeThreshold ?? 0.45,
      candidateWakeThreshold: options.candidateWakeThreshold ?? 0.18,
      candidateWakeHits: options.candidateWakeHits ?? 2,
      speechLookbackMs: options.speechLookbackMs ?? 1_200,
      candidateLookbackMs: options.candidateLookbackMs ?? 960,
      preRollPaddingMs: options.preRollPaddingMs ?? 300,
      positiveVadThreshold: options.positiveVadThreshold ?? 0.65,
      negativeVadThreshold: options.negativeVadThreshold ?? 0.4,
      negativeVadCount: options.negativeVadCount ?? 4,
      speechObserverMode: options.speechObserverMode ?? "provider_owned",
    };
  }

  push(samples: Float32Array, capturedMonotonicMs = this.now()): void {
    if (this.destroyed || !this.armed || !samples.length) return;
    const startSample = this.captureSample;
    this.captureSample += samples.length;
    this.history.push(samples);
    if (!this.detected) this.wakeWindow.push(samples);
    if (this.candidateActive) this.emitCandidateSamples(samples, capturedMonotonicMs);

    // Provider-owned turn detection may continue indefinitely after the wake
    // word. A duration limit here would conflate the global capture timeline
    // with the candidate-local timeline and truncate otherwise healthy turns.
    // Backpressure is enforced by the bounded observer and delivery queues.
    if (this.speechObserverOverflowed) return;

    const pending = this.speechBuffer;
    const combined = new Float32Array(pending.length + samples.length);
    combined.set(pending);
    combined.set(samples, pending.length);
    const combinedStart = pending.length ? this.speechBufferStartSample : startSample;
    let offset = 0;
    while (combined.length - offset >= SPEECH_FRAME_SAMPLES) {
      const end = offset + SPEECH_FRAME_SAMPLES;
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
    if (this.speechFrames.reduce((total, item) => total + item.frame.length, 0) > JARVIS_WAKE_SAMPLE_RATE * 30) {
      this.speechObserverOverflowed = true;
      for (const item of this.speechFrames) item.frame.fill(0);
      this.speechFrames.length = 0;
      if (this.options.speechObserverMode === "client_vad" || !this.detected) {
        this.candidateActive = false;
        if (this.detected) this.options.onCandidateClose?.("audio_backlog_overflow");
        this.armed = false;
        this.options.onError(new Error("speech_observer_overflow"));
      }
      return;
    }
    this.startSpeechDrain();
  }

  pause(): void {
    if (this.destroyed) return;
    if (this.candidateActive) this.options.onCandidateClose?.("sleep");
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
    while (this.speechDrainPromise || this.wakeDrainPromise) {
      await Promise.all([this.speechDrainPromise, this.wakeDrainPromise]
        .filter((value): value is Promise<void> => Boolean(value)));
    }
  }

  async destroy(): Promise<void> {
    if (this.destroyed) return;
    if (this.candidateActive) this.options.onCandidateClose?.("sleep");
    this.destroyed = true;
    this.armed = false;
    await this.idle();
    this.resetAudio();
    this.history.destroy();
    this.wakeWindow.destroy();
    await this.inference.destroy?.();
  }

  private now(): number {
    return typeof performance !== "undefined" && typeof performance.now === "function"
      ? performance.now()
      : Date.now();
  }

  private startSpeechDrain(): void {
    if (this.speechDrainPromise) return;
    this.speechDrainPromise = this.drainSpeechFrames().finally(() => {
      this.speechDrainPromise = null;
      if (this.speechFrames.length && this.armed && !this.destroyed) this.startSpeechDrain();
    });
  }

  private async drainSpeechFrames(): Promise<void> {
    while (this.speechFrames.length && this.armed && !this.destroyed) {
      const item = this.speechFrames.shift();
      if (!item) continue;
      try {
        const result = await this.inference.observeSpeech(
          item.frame,
          { startSample: item.startSample, endSample: item.endSample },
        );
        if (!this.armed || this.destroyed || this.speechObserverOverflowed) return;
        this.applySpeech(result, item.capturedEndMonotonicMs);
        if (!this.detected && this.wakeWindow.length >= WAKE_WINDOW_SAMPLES) {
          this.queuedWakeWindow?.fill(0);
          this.queuedWakeWindow = this.wakeWindow.snapshot();
          this.queuedWakeSpeechEligible = this.speechActive || (
            this.lastVoicedEndSample > 0
            && result.endSample - this.lastVoicedEndSample
              <= Math.ceil(this.options.speechLookbackMs / 1_000 * JARVIS_WAKE_SAMPLE_RATE)
          );
          this.startWakeDrain(this.armToken);
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
      if (this.queuedWakeWindow && !this.detected && this.armed && !this.destroyed) {
        this.startWakeDrain(this.armToken);
      }
    });
  }

  private async drainWakeWindows(armToken: number): Promise<void> {
    while (this.queuedWakeWindow && !this.detected && this.armed && !this.destroyed) {
      const window = this.queuedWakeWindow;
      this.queuedWakeWindow = null;
      const speechEligible = this.queuedWakeSpeechEligible;
      this.queuedWakeSpeechEligible = false;
      try {
        const result = await this.inference.classifyWake(window);
        this.advanceRecallGate(result.wakeProbability);
        const strongWake = result.wakeProbability >= this.options.wakeThreshold;
        const repeatedCandidate = this.candidateHits >= this.options.candidateWakeHits;
        if (
          armToken === this.armToken
          && !this.detected
          && this.armed
          && speechEligible
          && (strongWake || repeatedCandidate)
        ) {
          this.beginCandidate(Math.max(result.wakeProbability, this.candidateProbability));
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

  private advanceRecallGate(wakeProbability: number): void {
    const intervalMs = SPEECH_FRAME_SAMPLES / JARVIS_WAKE_SAMPLE_RATE * 1_000;
    const candidateWindowCount = Math.max(1, Math.ceil(this.options.candidateLookbackMs / intervalMs));
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

  private applySpeech(result: JarvisSpeechObservation, capturedEndMonotonicMs: number): void {
    const positive = result.speechProbability >= this.options.positiveVadThreshold;
    const negative = result.speechProbability < this.options.negativeVadThreshold;
    if (!this.speechActive && positive) {
      this.speechActive = true;
      this.lastVoicedEndSample = result.endSample;
      this.belowThresholdSamples = 0;
      this.options.onSpeechStart?.(result.speechProbability);
    } else if (this.speechActive && negative) {
      this.speechActive = false;
      this.options.onSpeechEnd?.(result.speechProbability);
    }
    if (positive) {
      this.lastVoicedEndSample = result.endSample;
      this.belowThresholdSamples = 0;
    } else if (negative) {
      this.belowThresholdSamples += Math.max(0, result.endSample - result.startSample);
    } else {
      // Hysteresis frames are neither speech nor silence. They deliberately
      // break a run of endpoint silence without toggling the speech boundary.
      this.belowThresholdSamples = 0;
    }
    if (!this.detected || this.endpointEmitted || !negative) return;
    const endpointSamples = this.options.endpointSilenceMs === undefined
      ? this.options.negativeVadCount * SPEECH_FRAME_SAMPLES
      : Math.max(SPEECH_FRAME_SAMPLES, Math.ceil(this.options.endpointSilenceMs / 1_000 * JARVIS_WAKE_SAMPLE_RATE));
    if (this.belowThresholdSamples < endpointSamples) return;
    this.endpointEmitted = true;
    this.options.onCandidateEndpoint?.({
      sampleRate: JARVIS_WAKE_SAMPLE_RATE,
      totalSamples: this.candidateSample,
      trailingSilenceMs: this.belowThresholdSamples / JARVIS_WAKE_SAMPLE_RATE * 1_000,
      speechEndSample: Math.max(0, this.lastVoicedEndSample - this.candidateStartSourceSample),
      observedAtSample: Math.max(0, result.endSample - this.candidateStartSourceSample),
      trailingSilenceSamples: this.belowThresholdSamples,
      processingLagMs: Math.max(0, this.now() - capturedEndMonotonicMs),
    });
  }

  private beginCandidate(probability: number): void {
    // The state transition happens before callbacks, preventing a callback
    // that synchronously pushes audio from re-arming classification.
    this.detected = true;
    this.candidateActive = true;
    this.armToken += 1;
    this.endpointEmitted = false;
    this.candidateSequence = 0;
    this.candidateSample = 0;
    this.candidatePending.fill(0);
    this.candidatePending = new Float32Array(0);
    this.candidatePendingCapturedMs = null;
    this.resetRecallGate();
    const history = this.history.snapshot();
    const preRollLimit = Math.min(
      history.length,
      Math.round((this.options.speechLookbackMs + this.options.preRollPaddingMs) / 1_000 * JARVIS_WAKE_SAMPLE_RATE),
    );
    const preRoll = history.length > preRollLimit ? history.slice(history.length - preRollLimit) : history;
    this.candidateStartSourceSample = Math.max(0, this.captureSample - preRoll.length);
    if (preRoll !== history) history.fill(0);
    this.options.onDetected(probability);
    const activationId = this.options.activationId ?? this.options.getActivationId?.() ?? "activation-pending";
    this.options.onCandidateStart?.(probability);
    this.emitCandidateSamples(preRoll, this.now() - preRoll.length / JARVIS_WAKE_SAMPLE_RATE * 1_000, activationId);
    preRoll.fill(0);
  }

  private emitCandidateSamples(
    samples: Float32Array,
    capturedMonotonicMs: number,
    activationId = this.options.activationId ?? this.options.getActivationId?.() ?? "activation-pending",
  ): void {
    if (!samples.length || !this.options.onCandidateChunk) return;
    const pendingLength = this.candidatePending.length;
    const combined = new Float32Array(pendingLength + samples.length);
    combined.set(this.candidatePending);
    combined.set(samples, pendingLength);
    const combinedStartMs = this.candidatePendingCapturedMs ?? capturedMonotonicMs;
    let offset = 0;
    while (combined.length - offset >= CHUNK_SAMPLES) {
      const chunkSamples = combined.slice(offset, offset + CHUNK_SAMPLES);
      const chunk: JarvisAudioChunk = {
        activationId,
        sequence: this.candidateSequence,
        sampleRate: 16_000,
        startSample: this.candidateSample,
        endSample: this.candidateSample + chunkSamples.length,
        capturedMonotonicMs: combinedStartMs + offset / 16_000 * 1_000,
        samples: chunkSamples,
      };
      this.candidateSequence += 1;
      this.candidateSample = chunk.endSample;
      try {
        this.options.onCandidateChunk(chunk);
      } finally {
        chunk.samples.fill(0);
      }
      offset += CHUNK_SAMPLES;
    }
    this.candidatePending = combined.slice(offset);
    this.candidatePendingCapturedMs = this.candidatePending.length
      ? combinedStartMs + offset / 16_000 * 1_000
      : null;
    combined.fill(0);
  }

  private resetRecallGate(): void {
    this.candidateWindows = 0;
    this.candidateHits = 0;
    this.candidateProbability = 0;
  }

  private resetAudio(): void {
    this.history.clear();
    this.resetRecallGate();
    this.speechBuffer.fill(0);
    this.speechBuffer = new Float32Array(0);
    for (const item of this.speechFrames) item.frame.fill(0);
    this.speechFrames.length = 0;
    this.queuedWakeWindow?.fill(0);
    this.queuedWakeWindow = null;
    this.queuedWakeSpeechEligible = false;
    this.detected = false;
    this.candidateActive = false;
    this.endpointEmitted = false;
    this.candidateSequence = 0;
    this.candidateSample = 0;
    this.candidatePending.fill(0);
    this.candidatePending = new Float32Array(0);
    this.candidatePendingCapturedMs = null;
    this.belowThresholdSamples = 0;
    this.lastVoicedEndSample = 0;
    this.speechActive = false;
    this.captureSample = 0;
    this.speechBufferStartSample = 0;
    this.speechObserverOverflowed = false;
    if (!this.destroyed) this.wakeWindow.clear();
    this.armToken += 1;
  }
}
