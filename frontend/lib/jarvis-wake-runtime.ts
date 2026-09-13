import {
  JARVIS_WAKE_SAMPLE_RATE,
  type JarvisWakeModelConfiguration,
  type JarvisWakeWorkerCommand,
  type JarvisWakeWorkerEvent,
} from "./jarvis-wake-protocol";
import type { JarvisWakeCandidateEndpoint } from "./jarvis-wake-engine";
import {
  createJarvisActivationAudio,
  type JarvisActivationAudio,
  type JarvisCandidateAudio,
} from "./jarvis-activation-audio";

export type JarvisWakeRuntimeStatus =
  | "idle"
  | "starting"
  | "ready"
  | "triggered"
  | "failed";

export interface JarvisWakeRuntimeSnapshot {
  status: JarvisWakeRuntimeStatus;
  error: string | null;
}

export interface JarvisWakeHandoff {
  localStream: MediaStream;
  utterance: Promise<Float32Array>;
  sampleRate: number;
  wakeProbability: number | null;
  wakeDetectedMonotonicMs?: number;
  activationId?: string;
  /** Candidate audio starts with `preRoll`, then arrives through `subscribe`. */
  candidateAudio?: JarvisWakeCandidateStream | JarvisCandidateAudio;
}

export interface JarvisWakeCandidateStream {
  sampleRate: number;
  preRoll: Float32Array;
  endpoint: Promise<JarvisWakeCandidateEndpoint>;
  subscribe: (listener: (samples: Float32Array) => void) => () => void;
}

interface JarvisWakeWorker {
  onmessage: ((event: MessageEvent<JarvisWakeWorkerEvent>) => void) | null;
  onerror: ((event: ErrorEvent) => void) | null;
  postMessage: (command: JarvisWakeWorkerCommand, transfer?: Transferable[]) => void;
  terminate: () => void;
}

interface JarvisWakeAudioCapture {
  pause: () => void;
  resume: () => Promise<void>;
  destroy: () => Promise<void>;
}

export interface JarvisWakeRuntimeDependencies {
  getUserMedia: (constraints: MediaStreamConstraints) => Promise<MediaStream>;
  createWorker: () => JarvisWakeWorker;
  createAudioCapture: (
    stream: MediaStream,
    onSamples: (samples: Float32Array) => void,
  ) => Promise<JarvisWakeAudioCapture>;
}

export interface JarvisWakeRuntimeOptions {
  onSnapshot?: (snapshot: JarvisWakeRuntimeSnapshot) => void;
  onReady?: (stream: MediaStream) => void;
  onTrace?: (kind: string, detail?: Record<string, unknown>) => void;
  onWake: (handoff: JarvisWakeHandoff) => void;
}

const browserDependencies: JarvisWakeRuntimeDependencies = {
  getUserMedia: (constraints) => navigator.mediaDevices.getUserMedia(constraints),
  createWorker: () => new Worker(
    new URL("./jarvis-wake-worker.ts", import.meta.url),
    { type: "module", name: "jarvis-wake-inference" },
  ) as unknown as JarvisWakeWorker,
  createAudioCapture: createBrowserAudioCapture,
};

export class JarvisWakeRuntime {
  private readonly options: JarvisWakeRuntimeOptions;
  private readonly dependencies: JarvisWakeRuntimeDependencies;
  private worker: JarvisWakeWorker | null = null;
  private capture: JarvisWakeAudioCapture | null = null;
  private stream: MediaStream | null = null;
  private operation = 0;
  private utteranceResolve: ((samples: Float32Array) => void) | null = null;
  private utteranceReject: ((cause: unknown) => void) | null = null;
  private candidateController: CandidateStreamController | null = null;
  private activationAudio: JarvisActivationAudio | null = null;
  private activationId: string | null = null;
  private snapshot: JarvisWakeRuntimeSnapshot = { status: "idle", error: null };

  constructor(
    options: JarvisWakeRuntimeOptions,
    dependencies: JarvisWakeRuntimeDependencies = browserDependencies,
  ) {
    this.options = options;
    this.dependencies = dependencies;
  }

  current(): JarvisWakeRuntimeSnapshot {
    return this.snapshot;
  }

  async start(configuration: JarvisWakeModelConfiguration): Promise<void> {
    const operation = ++this.operation;
    await this.releaseResources(true);
    this.options.onTrace?.("wake_runtime_starting");
    this.publish({ status: "starting", error: null });
    try {
      const stream = await this.dependencies.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          autoGainControl: true,
          noiseSuppression: true,
        },
      });
      this.options.onTrace?.("wake_microphone_ready");
      if (operation !== this.operation) {
        for (const track of stream.getTracks()) track.stop();
        return;
      }
      this.stream = stream;
      const worker = this.dependencies.createWorker();
      this.worker = worker;
      worker.onmessage = (event) => this.handleWorkerEvent(worker, event.data, operation);
      worker.onerror = (event) => this.fail(new Error(event.message || "Wake worker failed"), operation);
      const capture = await this.dependencies.createAudioCapture(stream, (samples) => {
        if (operation !== this.operation || this.worker !== worker) return;
        const observedMonotonicMs = typeof performance !== "undefined" ? performance.now() : Date.now();
        worker.postMessage(
          {
            type: "audio",
            samples,
            // The capture callback observes a completed block. The stream
            // contract timestamps its first sample, not its delivery time.
            capturedMonotonicMs: observedMonotonicMs - samples.length / 16_000 * 1_000,
          },
          [samples.buffer],
        );
      });
      if (operation !== this.operation) {
        await capture.destroy();
        worker.terminate();
        for (const track of stream.getTracks()) track.stop();
        return;
      }
      this.capture = capture;
      await capture.resume();
      this.options.onTrace?.("wake_capture_ready");
      if (operation !== this.operation) return;
      worker.postMessage({ type: "init", configuration });
    } catch (cause) {
      await this.fail(cause, operation);
    }
  }

  async rearm(): Promise<void> {
    if (!this.worker || !this.capture || !this.stream) {
      throw new Error("Wake runtime is not available to re-arm");
    }
    this.rejectPendingUtterance(new Error("Wake utterance was reset"));
    this.activationAudio?.close("rearmed");
    this.activationAudio = null;
    this.activationId = null;
    this.worker.postMessage({ type: "resume" });
    await this.capture.resume();
    this.options.onTrace?.("wake_runtime_rearmed");
    this.publish({ status: "ready", error: null });
    this.options.onReady?.(this.stream);
  }

  manualHandoff(): JarvisWakeHandoff {
    if (this.snapshot.status !== "ready" || !this.worker || !this.capture || !this.stream) {
      throw new Error("Wake runtime is not ready for a manual handoff");
    }
    this.worker.postMessage({ type: "pause" });
    this.capture.pause();
    this.options.onTrace?.("wake_manual_handoff");
    this.publish({ status: "triggered", error: null });
    return {
      localStream: this.stream,
      utterance: Promise.resolve(new Float32Array(0)),
      sampleRate: JARVIS_WAKE_SAMPLE_RATE,
      wakeProbability: null,
      wakeDetectedMonotonicMs: typeof performance !== "undefined" ? performance.now() : Date.now(),
    };
  }

  async stop(): Promise<void> {
    ++this.operation;
    await this.releaseResources(true);
    this.publish({ status: "idle", error: null });
  }

  private handleWorkerEvent(
    worker: JarvisWakeWorker,
    event: JarvisWakeWorkerEvent,
    operation: number,
  ): void {
    if (operation !== this.operation || worker !== this.worker) return;
    if (event.type === "ready") {
      this.options.onTrace?.("wake_model_ready");
      this.publish({ status: "ready", error: null });
      if (this.stream) this.options.onReady?.(this.stream);
      return;
    }
    if (event.type === "vad_boundary") {
      this.options.onTrace?.(`wake_local_${event.state}`, {
        probability: event.probability,
      });
      return;
    }
    if (event.type === "detected") {
      const wakeDetectedMonotonicMs = typeof performance !== "undefined" ? performance.now() : Date.now();
      const splitActivationId = event.activationId;
      if (
        this.snapshot.status !== "ready"
        || !this.stream
        || (splitActivationId ? this.activationAudio : this.utteranceResolve)
      ) {
        event.preRoll?.fill(0);
        return;
      }
      if (splitActivationId) {
        event.preRoll?.fill(0);
        const activationAudio = createJarvisActivationAudio(splitActivationId, {
          onDiagnostic: (diagnostic) => {
            this.options.onTrace?.(`wake_${diagnostic.kind}`, {
              activation_id: diagnostic.activationId,
              retained_ms: diagnostic.retainedMs,
            });
          },
        });
        this.activationAudio = activationAudio;
        this.activationId = splitActivationId;
        this.options.onTrace?.("wake_detected", {
          probability: event.probability,
          activation_id: splitActivationId,
        });
        this.publish({ status: "triggered", error: null });
        this.options.onWake({
          localStream: this.stream,
          utterance: Promise.resolve(new Float32Array(0)),
          sampleRate: JARVIS_WAKE_SAMPLE_RATE,
          wakeProbability: event.probability,
          wakeDetectedMonotonicMs,
          activationId: splitActivationId,
          candidateAudio: activationAudio,
        });
        return;
      }
      let resolve!: (samples: Float32Array) => void;
      let reject!: (cause: unknown) => void;
      const utterance = new Promise<Float32Array>((promiseResolve, promiseReject) => {
        resolve = promiseResolve;
        reject = promiseReject;
      });
      this.utteranceResolve = resolve;
      this.utteranceReject = reject;
      const candidate = createCandidateStream(
        event.preRoll ?? new Float32Array(0),
        event.sampleRate ?? JARVIS_WAKE_SAMPLE_RATE,
      );
      this.candidateController = candidate.controller;
      this.options.onTrace?.("wake_detected", { probability: event.probability });
      this.publish({ status: "triggered", error: null });
      this.options.onWake({
        localStream: this.stream,
        utterance,
        sampleRate: JARVIS_WAKE_SAMPLE_RATE,
        wakeProbability: event.probability,
        wakeDetectedMonotonicMs,
        candidateAudio: candidate.stream,
      });
      return;
    }
    if (event.type === "candidate_audio") {
      this.candidateController?.push(event.samples);
      return;
    }
    if (event.type === "candidate_chunk") {
      const candidate = this.activationAudio;
      if (!candidate || candidate.activationId !== event.activationId) {
        event.samples.fill(0);
        return;
      }
      candidate.pushChunk({
        activationId: event.activationId,
        sequence: event.sequence,
        sampleRate: event.sampleRate,
        startSample: event.startSample,
        endSample: event.endSample,
        capturedMonotonicMs: event.capturedMonotonicMs,
        samples: event.samples,
      });
      return;
    }
    if (event.type === "candidate_endpoint") {
      this.options.onTrace?.("wake_local_endpoint", {
        samples: event.totalSamples,
        silence_ms: event.trailingSilenceMs,
      });
      if (this.activationAudio) {
        this.activationAudio.resolveEndpoint({
          sampleRate: 16_000,
          speechEndSample: event.speechEndSample ?? event.totalSamples,
          observedAtSample: event.observedAtSample ?? event.totalSamples,
          trailingSilenceSamples: event.trailingSilenceSamples
            ?? Math.round(event.trailingSilenceMs / 1_000 * 16_000),
          processingLagMs: event.processingLagMs ?? 0,
        });
      } else {
        this.candidateController?.end(event);
      }
      return;
    }
    if (event.type === "candidate_close") {
      this.options.onTrace?.(`wake_${event.reason}`, {
        activation_id: this.activationAudio?.activationId ?? null,
      });
      this.activationAudio?.close(event.reason);
      return;
    }
    if (event.type === "utterance") {
      this.capture?.pause();
      this.options.onTrace?.("wake_utterance_ready", {
        samples: event.samples.length,
        duration_ms: Math.round(event.samples.length / event.sampleRate * 1_000),
      });
      const resolve = this.utteranceResolve;
      this.utteranceResolve = null;
      this.utteranceReject = null;
      resolve?.(event.samples);
      this.candidateController = null;
      return;
    }
    void this.fail(new Error(event.message), operation);
  }

  private async fail(cause: unknown, operation: number): Promise<void> {
    if (operation !== this.operation) return;
    ++this.operation;
    const error = cause instanceof Error ? cause.message : "Wake detection failed";
    this.options.onTrace?.("wake_runtime_failed", { error });
    await this.releaseResources(true);
    this.publish({ status: "failed", error });
  }

  private async releaseResources(stopStream: boolean): Promise<void> {
    this.rejectPendingUtterance(new Error("Wake runtime stopped"));
    this.activationAudio?.close(stopStream ? "sleep" : "disconnected");
    this.activationAudio = null;
    this.activationId = null;
    const worker = this.worker;
    const capture = this.capture;
    const stream = this.stream;
    this.worker = null;
    this.capture = null;
    this.stream = null;
    if (worker) {
      worker.onmessage = null;
      worker.onerror = null;
      worker.postMessage({ type: "destroy" });
      worker.terminate();
    }
    await capture?.destroy().catch(() => undefined);
    if (stopStream && stream) {
      for (const track of stream.getTracks()) track.stop();
    }
  }

  private rejectPendingUtterance(cause: unknown): void {
    const reject = this.utteranceReject;
    this.utteranceResolve = null;
    this.utteranceReject = null;
    reject?.(cause);
    this.candidateController?.fail(cause);
    this.candidateController = null;
  }

  private publish(snapshot: JarvisWakeRuntimeSnapshot): void {
    this.snapshot = snapshot;
    this.options.onSnapshot?.(snapshot);
  }
}

interface CandidateStreamController {
  push: (samples: Float32Array) => void;
  end: (endpoint: JarvisWakeCandidateEndpoint) => void;
  fail: (cause: unknown) => void;
}

function createCandidateStream(
  preRoll: Float32Array,
  sampleRate: number,
): { stream: JarvisWakeCandidateStream; controller: CandidateStreamController } {
  let listener: ((samples: Float32Array) => void) | null = null;
  let pending: Float32Array[] = [];
  let endpointResolve!: (endpoint: JarvisWakeCandidateEndpoint) => void;
  let endpointReject!: (cause: unknown) => void;
  const endpoint = new Promise<JarvisWakeCandidateEndpoint>((resolve, reject) => {
    endpointResolve = resolve;
    endpointReject = reject;
  });
  const stream: JarvisWakeCandidateStream = {
    sampleRate,
    preRoll,
    endpoint,
    subscribe: (next) => {
      listener = next;
      const queued = pending;
      pending = [];
      for (const samples of queued) next(samples);
      return () => {
        if (listener === next) listener = null;
      };
    },
  };
  let closed = false;
  const controller: CandidateStreamController = {
    push: (samples) => {
      if (closed || !samples.length) {
        samples.fill(0);
        return;
      }
      if (listener) listener(samples);
      else pending.push(samples);
    },
    end: (value) => {
      if (closed) return;
      closed = true;
      // Keep queued chunks until the consumer subscribes. The controller may
      // still be registering the activation when local endpointing fires.
      endpointResolve(value);
    },
    fail: (cause) => {
      if (closed) return;
      closed = true;
      preRoll.fill(0);
      for (const samples of pending) samples.fill(0);
      pending = [];
      endpointReject(cause);
    },
  };
  return { stream, controller };
}

async function createBrowserAudioCapture(
  stream: MediaStream,
  onSamples: (samples: Float32Array) => void,
): Promise<JarvisWakeAudioCapture> {
  const context = new AudioContext();
  try {
    await context.audioWorklet.addModule("/jarvis-wake/audio-capture.worklet.js");
    const source = context.createMediaStreamSource(stream);
    const worklet = new AudioWorkletNode(context, "jarvis-wake-audio-capture", {
      processorOptions: { targetSampleRate: JARVIS_WAKE_SAMPLE_RATE },
    });
    const silent = context.createGain();
    silent.gain.value = 0;
    source.connect(worklet);
    worklet.connect(silent);
    silent.connect(context.destination);
    worklet.port.onmessage = (event: MessageEvent<Float32Array>) => onSamples(event.data);
    return {
      pause: () => worklet.port.postMessage({ type: "pause" }),
      resume: async () => {
        worklet.port.postMessage({ type: "resume" });
        if (context.state === "suspended") await context.resume();
      },
      destroy: async () => {
        worklet.port.onmessage = null;
        source.disconnect();
        worklet.disconnect();
        silent.disconnect();
        await context.close();
      },
    };
  } catch (cause) {
    await context.close().catch(() => undefined);
    throw cause;
  }
}
