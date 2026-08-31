import { describe, expect, it, vi } from "vitest";

import type {
  JarvisWakeWorkerCommand,
  JarvisWakeWorkerEvent,
} from "./jarvis-wake-protocol";
import {
  JarvisWakeRuntime,
  type JarvisWakeHandoff,
  type JarvisWakeRuntimeDependencies,
  type JarvisWakeRuntimeSnapshot,
} from "./jarvis-wake-runtime";

class FakeTrack {
  stopped = false;
  stop() { this.stopped = true; }
}

class FakeStream {
  readonly track = new FakeTrack();
  getTracks() { return [this.track] as unknown as MediaStreamTrack[]; }
}

class FakeWorker {
  onmessage: ((event: MessageEvent<JarvisWakeWorkerEvent>) => void) | null = null;
  onerror: ((event: ErrorEvent) => void) | null = null;
  messages: JarvisWakeWorkerCommand[] = [];
  transfers: Transferable[][] = [];
  terminated = false;
  postMessage(command: JarvisWakeWorkerCommand, transfer: Transferable[] = []) {
    this.messages.push(command);
    this.transfers.push(transfer);
  }
  terminate() { this.terminated = true; }
  emit(event: JarvisWakeWorkerEvent) {
    this.onmessage?.({ data: event } as MessageEvent<JarvisWakeWorkerEvent>);
  }
}

function harness() {
  const stream = new FakeStream();
  const worker = new FakeWorker();
  let onSamples: ((samples: Float32Array) => void) | null = null;
  const capture = {
    pause: vi.fn(),
    resume: vi.fn().mockResolvedValue(undefined),
    destroy: vi.fn().mockResolvedValue(undefined),
  };
  const dependencies: JarvisWakeRuntimeDependencies = {
    getUserMedia: vi.fn(async () => stream as unknown as MediaStream),
    createWorker: () => worker,
    createAudioCapture: vi.fn(async (_stream, callback) => {
      onSamples = callback;
      return capture;
    }),
  };
  const snapshots: JarvisWakeRuntimeSnapshot[] = [];
  const handoffs: JarvisWakeHandoff[] = [];
  const runtime = new JarvisWakeRuntime(
    {
      onSnapshot: (snapshot) => snapshots.push(snapshot),
      onWake: (handoff) => handoffs.push(handoff),
    },
    dependencies,
  );
  return {
    runtime,
    dependencies,
    stream,
    worker,
    capture,
    snapshots,
    handoffs,
    pushAudio: (samples: Float32Array) => {
      if (!onSamples) throw new Error("capture not ready");
      onSamples(samples);
    },
  };
}

const configuration = {
  vadModelUrl: "/vad.onnx",
  spectrogramModelUrl: "/mel.onnx",
  embeddingModelUrl: "/embedding.onnx",
  wakeModelUrl: "/jarvis.onnx",
  wasmBaseUrl: "/wasm/",
};

describe("JarvisWakeRuntime", () => {
  it("owns one mic stream and transfers captured audio to the worker", async () => {
    const value = harness();
    await value.runtime.start(configuration);
    value.worker.emit({ type: "ready" });
    const samples = new Float32Array([1, 2]);
    value.pushAudio(samples);

    expect(value.snapshots.map((snapshot) => snapshot.status)).toEqual(["starting", "ready"]);
    expect(value.capture.resume).toHaveBeenCalledOnce();
    expect(value.worker.messages[0]).toEqual({ type: "init", configuration });
    expect(value.worker.messages[1]).toMatchObject({ type: "audio" });
    expect(value.worker.transfers[1]).toEqual([samples.buffer]);
  });

  it("emits exactly one handoff and resolves its one-shot utterance", async () => {
    const value = harness();
    await value.runtime.start(configuration);
    value.worker.emit({ type: "ready" });
    value.worker.emit({ type: "detected", probability: 0.91 });
    value.worker.emit({ type: "detected", probability: 0.92 });

    expect(value.handoffs).toHaveLength(1);
    expect(value.handoffs[0].localStream).toBe(value.stream);
    expect(value.handoffs[0].wakeProbability).toBe(0.91);
    const samples = new Float32Array([3, 4, 5]);
    value.worker.emit({ type: "utterance", samples, sampleRate: 16_000 });
    await expect(value.handoffs[0].utterance).resolves.toBe(samples);
    expect(value.capture.pause).toHaveBeenCalledOnce();
  });

  it("re-arms without reacquiring the microphone and releases it on stop", async () => {
    const value = harness();
    await value.runtime.start(configuration);
    value.worker.emit({ type: "ready" });
    value.worker.emit({ type: "detected", probability: 0.9 });
    value.worker.emit({ type: "utterance", samples: new Float32Array([1]), sampleRate: 16_000 });
    await value.runtime.rearm();

    expect(value.worker.messages.at(-1)).toEqual({ type: "resume" });
    expect(value.capture.resume).toHaveBeenCalledTimes(2);
    await value.runtime.stop();
    expect(value.stream.track.stopped).toBe(true);
    expect(value.worker.terminated).toBe(true);
    expect(value.capture.destroy).toHaveBeenCalledOnce();
  });

  it("uses the existing stream for a functional manual Arm handoff", async () => {
    const value = harness();
    await value.runtime.start(configuration);
    value.worker.emit({ type: "ready" });
    const handoff = value.runtime.manualHandoff();

    expect(handoff.localStream).toBe(value.stream);
    expect(handoff.wakeProbability).toBeNull();
    await expect(handoff.utterance).resolves.toHaveLength(0);
    expect(value.worker.messages.at(-1)).toEqual({ type: "pause" });
    expect(value.capture.pause).toHaveBeenCalledOnce();
    expect(value.runtime.current().status).toBe("triggered");
  });

  it("fails closed and stops the mic when worker initialization fails", async () => {
    const value = harness();
    await value.runtime.start(configuration);
    value.worker.emit({ type: "error", message: "model missing" });
    await vi.waitFor(() => expect(value.runtime.current().status).toBe("failed"));
    expect(value.runtime.current().error).toBe("model missing");
    expect(value.stream.track.stopped).toBe(true);
  });

  it("fails closed when the browser will not start audio capture", async () => {
    const value = harness();
    value.capture.resume.mockRejectedValueOnce(new Error("AudioContext was blocked"));

    await value.runtime.start(configuration);

    expect(value.runtime.current()).toEqual({
      status: "failed",
      error: "AudioContext was blocked",
    });
    expect(value.worker.messages).toContainEqual({ type: "destroy" });
    expect(value.stream.track.stopped).toBe(true);
  });

  it("releases the worker and mic when AudioWorklet creation fails", async () => {
    const value = harness();
    value.dependencies.createAudioCapture = vi.fn(async () => {
      throw new Error("AudioWorklet module failed");
    });

    await value.runtime.start(configuration);

    expect(value.runtime.current()).toEqual({
      status: "failed",
      error: "AudioWorklet module failed",
    });
    expect(value.worker.terminated).toBe(true);
    expect(value.stream.track.stopped).toBe(true);
  });
});
