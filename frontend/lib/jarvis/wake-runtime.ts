import {
  JARVIS_WAKE_SAMPLE_RATE,
  type JarvisWakeModelConfiguration,
  type JarvisWakeWorkerCommand,
  type JarvisWakeWorkerEvent,
} from "./wake-protocol";
import {
  createJarvisActivationAudio,
  type JarvisActivationAudio,
  type JarvisCandidateAudio,
} from "./activation-audio";

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
  sampleRate: number;
  wakeProbability: number | null;
  wakeDetectedMonotonicMs?: number;
  activationId?: string;
  /** The wake turn's audio, pre-roll first, as ordered chunks; absent for a manual Arm. */
  candidateAudio?: JarvisCandidateAudio;
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
    new URL("./wake-worker.ts", import.meta.url),
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
      if (this.snapshot.status !== "ready" || !this.stream || this.activationAudio) return;
      const activationAudio = createJarvisActivationAudio(event.activationId, {
        onDiagnostic: (diagnostic) => {
          this.options.onTrace?.(`wake_${diagnostic.kind}`, {
            activation_id: diagnostic.activationId,
            retained_ms: diagnostic.retainedMs,
          });
        },
      });
      this.activationAudio = activationAudio;
      this.activationId = event.activationId;
      this.options.onTrace?.("wake_detected", {
        probability: event.probability,
        activation_id: event.activationId,
      });
      this.publish({ status: "triggered", error: null });
      this.options.onWake({
        localStream: this.stream,
        sampleRate: JARVIS_WAKE_SAMPLE_RATE,
        wakeProbability: event.probability,
        wakeDetectedMonotonicMs,
        activationId: event.activationId,
        candidateAudio: activationAudio,
      });
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
      this.activationAudio?.resolveEndpoint({
        sampleRate: 16_000,
        speechEndSample: event.speechEndSample ?? event.totalSamples,
        observedAtSample: event.observedAtSample ?? event.totalSamples,
        trailingSilenceSamples: event.trailingSilenceSamples
          ?? Math.round(event.trailingSilenceMs / 1_000 * 16_000),
        processingLagMs: event.processingLagMs ?? 0,
      });
      return;
    }
    if (event.type === "candidate_close") {
      this.options.onTrace?.(`wake_${event.reason}`, {
        activation_id: this.activationAudio?.activationId ?? null,
      });
      this.activationAudio?.close(event.reason);
      return;
    }
    void this.fail(new Error(event.message), operation);
  }

  private async fail(cause: unknown, operation: number): Promise<void> {
    if (operation !== this.operation) return;
    const failed = ++this.operation;
    const error = cause instanceof Error ? cause.message : "Wake detection failed";
    this.options.onTrace?.("wake_runtime_failed", { error });
    await this.releaseResources(true);
    // A restart during cleanup owns the state now.
    if (failed !== this.operation) return;
    this.publish({ status: "failed", error });
  }

  private async releaseResources(stopStream: boolean): Promise<void> {
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

  private publish(snapshot: JarvisWakeRuntimeSnapshot): void {
    this.snapshot = snapshot;
    this.options.onSnapshot?.(snapshot);
  }
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
