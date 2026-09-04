import {
  JARVIS_WAKE_SAMPLE_RATE,
  type JarvisWakeModelConfiguration,
  type JarvisWakeWorkerCommand,
  type JarvisWakeWorkerEvent,
} from "./jarvis-wake-protocol";

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
        worker.postMessage({ type: "audio", samples }, [samples.buffer]);
      });
      if (operation !== this.operation) {
        await capture.destroy();
        worker.terminate();
        for (const track of stream.getTracks()) track.stop();
        return;
      }
      this.capture = capture;
      await capture.resume();
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
    this.worker.postMessage({ type: "resume" });
    await this.capture.resume();
    this.publish({ status: "ready", error: null });
    this.options.onReady?.(this.stream);
  }

  manualHandoff(): JarvisWakeHandoff {
    if (this.snapshot.status !== "ready" || !this.worker || !this.capture || !this.stream) {
      throw new Error("Wake runtime is not ready for a manual handoff");
    }
    this.worker.postMessage({ type: "pause" });
    this.capture.pause();
    this.publish({ status: "triggered", error: null });
    return {
      localStream: this.stream,
      utterance: Promise.resolve(new Float32Array(0)),
      sampleRate: JARVIS_WAKE_SAMPLE_RATE,
      wakeProbability: null,
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
      this.publish({ status: "ready", error: null });
      if (this.stream) this.options.onReady?.(this.stream);
      return;
    }
    if (event.type === "detected") {
      if (this.snapshot.status !== "ready" || !this.stream || this.utteranceResolve) return;
      let resolve!: (samples: Float32Array) => void;
      let reject!: (cause: unknown) => void;
      const utterance = new Promise<Float32Array>((promiseResolve, promiseReject) => {
        resolve = promiseResolve;
        reject = promiseReject;
      });
      this.utteranceResolve = resolve;
      this.utteranceReject = reject;
      this.publish({ status: "triggered", error: null });
      this.options.onWake({
        localStream: this.stream,
        utterance,
        sampleRate: JARVIS_WAKE_SAMPLE_RATE,
        wakeProbability: event.probability,
      });
      return;
    }
    if (event.type === "utterance") {
      this.capture?.pause();
      const resolve = this.utteranceResolve;
      this.utteranceResolve = null;
      this.utteranceReject = null;
      resolve?.(event.samples);
      return;
    }
    void this.fail(new Error(event.message), operation);
  }

  private async fail(cause: unknown, operation: number): Promise<void> {
    if (operation !== this.operation) return;
    ++this.operation;
    const error = cause instanceof Error ? cause.message : "Wake detection failed";
    await this.releaseResources(true);
    this.publish({ status: "failed", error });
  }

  private async releaseResources(stopStream: boolean): Promise<void> {
    this.rejectPendingUtterance(new Error("Wake runtime stopped"));
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
