export interface JarvisAudioChunk {
  activationId: string;
  sequence: number;
  sampleRate: 16_000;
  startSample: number;
  endSample: number;
  capturedMonotonicMs: number;
  samples: Float32Array;
}

export interface JarvisLocalEndpoint {
  sampleRate: 16_000;
  speechEndSample: number;
  observedAtSample: number;
  trailingSilenceSamples: number;
  processingLagMs: number;
}

export type JarvisCandidateCloseReason =
  | "rejected"
  | "timed_out"
  | "stale_generation"
  | "disconnected"
  | "rearmed"
  | "sleep"
  | "audio_backlog_overflow";

export interface JarvisCandidateAudio {
  activationId: string;
  sampleRate: 16_000;
  readonly isClosed?: boolean;
  subscribe(listener: (chunk: JarvisAudioChunk) => void): () => void;
  observeEndpoint(): Promise<JarvisLocalEndpoint>;
  promoteToConversation(): void;
  close(reason: JarvisCandidateCloseReason): void;
  /** Compatibility-only fields used by the pre-phase-3 handoff adapter. */
  preRoll?: Float32Array;
  endpoint?: Promise<{ sampleRate: number; totalSamples: number; trailingSilenceMs: number }>;
}

export interface JarvisActivationAudioDiagnostics {
  kind: "soft_backlog" | "audio_backlog_overflow";
  activationId: string;
  retainedSamples: number;
  retainedMs: number;
}

export interface JarvisActivationAudioOptions {
  onDiagnostic?: (diagnostic: JarvisActivationAudioDiagnostics) => void;
  softBacklogMs?: number;
  hardBacklogMs?: number;
  chunkSamples?: number;
}

const SAMPLE_RATE = 16_000 as const;
const DEFAULT_CHUNK_SAMPLES = 320;
const DEFAULT_SOFT_BACKLOG_MS = 500;
const DEFAULT_HARD_BACKLOG_MS = 30_000;

function monotonicNow(): number {
  if (typeof performance !== "undefined" && typeof performance.now === "function") {
    return performance.now();
  }
  return Date.now();
}

/**
 * Owns one activation's ordered PCM lifetime. Browser capture code feeds this
 * object; transport code subscribes once and is never allowed to manufacture a
 * second copy of the microphone stream.
 */
export class JarvisActivationAudio implements JarvisCandidateAudio {
  readonly activationId: string;
  readonly sampleRate = SAMPLE_RATE;

  private readonly onDiagnostic?: (diagnostic: JarvisActivationAudioDiagnostics) => void;
  private readonly chunkSamples: number;
  private readonly softBacklogSamples: number;
  private readonly hardBacklogSamples: number;
  private readonly queued: JarvisAudioChunk[] = [];
  private readonly deliveredCandidateHistory: JarvisAudioChunk[] = [];
  private pendingSamples = new Float32Array(0);
  private pendingCapturedMonotonicMs: number | null = null;
  private subscriber: ((chunk: JarvisAudioChunk) => void) | null = null;
  private resolvedEndpoint: JarvisLocalEndpoint | null = null;
  private endpointResolve: ((endpoint: JarvisLocalEndpoint) => void) | null = null;
  private endpointReject: ((reason: unknown) => void) | null = null;
  private readonly endpointPromise: Promise<JarvisLocalEndpoint>;
  private nextSequence = 0;
  private nextSample = 0;
  private closed = false;
  private promoted = false;
  private softBacklogReported = false;

  constructor(activationId: string, options: JarvisActivationAudioOptions = {}) {
    if (!activationId.trim()) throw new Error("Activation audio requires an activation id");
    this.activationId = activationId;
    this.onDiagnostic = options.onDiagnostic;
    this.chunkSamples = Math.max(1, Math.floor(options.chunkSamples ?? DEFAULT_CHUNK_SAMPLES));
    this.softBacklogSamples = Math.max(
      this.chunkSamples,
      Math.floor((options.softBacklogMs ?? DEFAULT_SOFT_BACKLOG_MS) * SAMPLE_RATE / 1_000),
    );
    this.hardBacklogSamples = Math.max(
      this.chunkSamples,
      Math.floor((options.hardBacklogMs ?? DEFAULT_HARD_BACKLOG_MS) * SAMPLE_RATE / 1_000),
    );
    this.endpointPromise = new Promise<JarvisLocalEndpoint>((resolve, reject) => {
      this.endpointResolve = resolve;
      this.endpointReject = reject;
    });
    // A candidate may be closed during rearm before the transport has
    // subscribed. The caller can still observe the rejection, but it must not
    // become an unhandled browser promise rejection in that race.
    this.endpointPromise.catch(() => undefined);
  }

  get state(): "candidate" | "conversation" | "closed" {
    if (this.closed) return "closed";
    return this.promoted ? "conversation" : "candidate";
  }

  get retainedSamples(): number {
    return this.pendingSamples.length
      + this.queued.reduce((total, chunk) => total + chunk.samples.length, 0)
      + this.deliveredCandidateHistory.reduce((total, chunk) => total + chunk.samples.length, 0);
  }

  get retainedMs(): number {
    return this.retainedSamples / SAMPLE_RATE * 1_000;
  }

  get isClosed(): boolean {
    return this.closed;
  }

  /** Feed arbitrary capture blocks; they are split into 20 ms logical chunks. */
  append(samples: Float32Array, capturedMonotonicMs = monotonicNow()): void {
    if (this.closed || samples.length === 0) {
      samples.fill(0);
      return;
    }
    const pendingLength = this.pendingSamples.length;
    const combined = new Float32Array(pendingLength + samples.length);
    combined.set(this.pendingSamples);
    combined.set(samples, pendingLength);
    const combinedStartMs = this.pendingCapturedMonotonicMs ?? capturedMonotonicMs;
    let offset = 0;
    while (combined.length - offset >= this.chunkSamples) {
      const end = offset + this.chunkSamples;
      const chunkSamples = combined.slice(offset, end);
      this.pushChunk({
        activationId: this.activationId,
        sequence: this.nextSequence,
        sampleRate: SAMPLE_RATE,
        startSample: this.nextSample,
        endSample: this.nextSample + chunkSamples.length,
        capturedMonotonicMs: combinedStartMs + offset / SAMPLE_RATE * 1_000,
        samples: chunkSamples,
      });
      offset = end;
    }
    this.pendingSamples = combined.slice(offset);
    this.pendingCapturedMonotonicMs = this.pendingSamples.length
      ? combinedStartMs + offset / SAMPLE_RATE * 1_000
      : null;
    combined.fill(0);
    samples.fill(0);
    this.checkBacklog();
  }

  push(samples: Float32Array, capturedMonotonicMs = monotonicNow()): void {
    this.append(samples, capturedMonotonicMs);
  }

  pushChunk(chunk: JarvisAudioChunk): void {
    if (this.closed) {
      chunk.samples.fill(0);
      return;
    }
    if (
      chunk.activationId !== this.activationId
      || chunk.sampleRate !== SAMPLE_RATE
      || chunk.sequence !== this.nextSequence
      || chunk.startSample !== this.nextSample
      || chunk.endSample <= chunk.startSample
      || chunk.endSample - chunk.startSample !== chunk.samples.length
    ) {
      chunk.samples.fill(0);
      throw new Error("Activation audio chunk is not contiguous");
    }
    this.nextSequence += 1;
    this.nextSample = chunk.endSample;
    if (this.subscriber) {
      if (!this.promoted) this.retainDeliveredChunk(chunk);
      this.deliver(chunk);
      this.checkBacklog();
      return;
    }
    this.queued.push(chunk);
    this.checkBacklog();
  }

  subscribe(listener: (chunk: JarvisAudioChunk) => void): () => void {
    if (this.subscriber) throw new Error("Activation audio permits one subscriber");
    if (this.closed) return () => undefined;
    this.subscriber = listener;
    // Until provider commit/promotion, a replacement connection must be able
    // to replay every candidate range. The history owns separate buffers, so
    // transport callbacks can still be zeroed immediately.
    for (const retained of this.deliveredCandidateHistory) {
      if (this.closed || this.subscriber !== listener) break;
      this.deliver({ ...retained, samples: retained.samples.slice() });
    }
    while (!this.closed && this.queued.length) {
      const chunk = this.queued.shift();
      if (chunk) {
        if (!this.promoted) this.retainDeliveredChunk(chunk);
        this.deliver(chunk);
      }
    }
    this.softBacklogReported = false;
    return () => {
      if (this.subscriber === listener) this.subscriber = null;
    };
  }

  observeEndpoint(): Promise<JarvisLocalEndpoint> {
    return this.endpointPromise;
  }

  resolveEndpoint(endpoint: JarvisLocalEndpoint): void {
    if (this.resolvedEndpoint || this.closed) return;
    if (
      endpoint.sampleRate !== SAMPLE_RATE
      || endpoint.speechEndSample < 0
      || endpoint.observedAtSample < endpoint.speechEndSample
      || endpoint.trailingSilenceSamples < 0
      || endpoint.processingLagMs < 0
    ) throw new Error("Invalid local endpoint");
    this.resolvedEndpoint = endpoint;
    this.endpointResolve?.(endpoint);
    this.endpointResolve = null;
    this.endpointReject = null;
  }

  /** Alias used by capture runtimes that describe this as an observation. */
  observeEndpointResult(endpoint: JarvisLocalEndpoint): void {
    this.resolveEndpoint(endpoint);
  }

  promoteToConversation(): void {
    if (this.closed) return;
    this.promoted = true;
    this.clearDeliveredHistory();
  }

  close(reason: JarvisCandidateCloseReason): void {
    if (this.closed) return;
    this.closed = true;
    for (const chunk of this.queued) chunk.samples.fill(0);
    this.queued.length = 0;
    this.clearDeliveredHistory();
    this.pendingSamples.fill(0);
    this.pendingSamples = new Float32Array(0);
    this.pendingCapturedMonotonicMs = null;
    this.subscriber = null;
    if (!this.resolvedEndpoint) {
      this.endpointReject?.(new Error(`Activation audio closed: ${reason}`));
      this.endpointResolve = null;
      this.endpointReject = null;
    }
  }

  private deliver(chunk: JarvisAudioChunk): void {
    const listener = this.subscriber;
    if (!listener) {
      this.queued.push(chunk);
      this.checkBacklog();
      return;
    }
    try {
      listener(chunk);
    } finally {
      // The transport encoder consumes the view synchronously. Retaining a
      // reference after delivery must never retain microphone PCM.
      chunk.samples.fill(0);
    }
  }

  private retainDeliveredChunk(chunk: JarvisAudioChunk): void {
    this.deliveredCandidateHistory.push({ ...chunk, samples: chunk.samples.slice() });
  }

  private clearDeliveredHistory(): void {
    for (const chunk of this.deliveredCandidateHistory) chunk.samples.fill(0);
    this.deliveredCandidateHistory.length = 0;
  }

  private checkBacklog(): void {
    const retainedSamples = this.retainedSamples;
    const retainedMs = retainedSamples / SAMPLE_RATE * 1_000;
    if (retainedSamples > this.hardBacklogSamples) {
      this.onDiagnostic?.({
        kind: "audio_backlog_overflow",
        activationId: this.activationId,
        retainedSamples,
        retainedMs,
      });
      this.close("audio_backlog_overflow");
      return;
    }
    if (retainedSamples > this.softBacklogSamples && !this.softBacklogReported) {
      this.softBacklogReported = true;
      this.onDiagnostic?.({
        kind: "soft_backlog",
        activationId: this.activationId,
        retainedSamples,
        retainedMs,
      });
    }
  }
}

export function createJarvisActivationAudio(
  activationId: string,
  options?: JarvisActivationAudioOptions,
): JarvisActivationAudio {
  return new JarvisActivationAudio(activationId, options);
}
