import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { JarvisLiveCallResponse } from "./api";
import { JarvisLiveController } from "./jarvis-live";
import type { JarvisAudioChunk, JarvisCandidateAudio } from "./jarvis-activation-audio";

function fakeCall(): JarvisLiveCallResponse {
  return {
    role: "controller",
    session: { id: "session-1", generation: 1 } as JarvisLiveCallResponse["session"],
    sdp_answer: "answer",
    provider_capabilities: { continuous_conversation: true },
    server_received_at: new Date().toISOString(),
    server_responded_at: new Date().toISOString(),
  };
}

function fakeBackendState(lifecycleState = "ready") {
  return {
    session: {
      id: "session-1",
      generation: 1,
      lifecycle_state: lifecycleState,
    },
  } as Awaited<ReturnType<NonNullable<import("./jarvis-live").JarvisLiveDependencies["getBackendState"]>>>;
}

function fakeMediaStream(): MediaStream {
  const track = { stop: vi.fn() } as unknown as MediaStreamTrack;
  return {
    getAudioTracks: () => [track],
    getTracks: () => [track],
  } as unknown as MediaStream;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function fakePc(options: { autoConnect?: boolean; remoteDescription?: Promise<void> } = {}) {
  const listeners = new Map<string, Set<() => void>>();
  const channel = {
    readyState: "connecting",
    send: vi.fn(),
    close: vi.fn(),
    onopen: null as (() => void) | null,
    onclose: null as (() => void) | null,
    onmessage: null as ((event: MessageEvent) => void) | null,
    onerror: null as (() => void) | null,
  } as unknown as RTCDataChannel;
  const pc = {
    iceGatheringState: "complete",
    iceConnectionState: "new",
    connectionState: "new",
    localDescription: null,
    createDataChannel: vi.fn(() => channel),
    createOffer: vi.fn(async () => ({ type: "offer", sdp: "offer" } as RTCSessionDescriptionInit)),
    setLocalDescription: vi.fn(async () => undefined),
    setRemoteDescription: vi.fn(async () => {
      await options.remoteDescription;
      if (options.autoConnect === false) return;
      pc.connectionState = "connected";
      pc.iceConnectionState = "connected";
      channel.readyState = "open";
      listeners.get("connectionstatechange")?.forEach((listener) => listener());
      listeners.get("iceconnectionstatechange")?.forEach((listener) => listener());
      channel.onopen?.();
    }),
    addEventListener: vi.fn((type: string, listener: () => void) => {
      const entries = listeners.get(type) ?? new Set();
      entries.add(listener);
      listeners.set(type, entries);
    }),
    removeEventListener: vi.fn((type: string, listener: () => void) => listeners.get(type)?.delete(listener)),
    addTrack: vi.fn(),
    close: vi.fn(),
    ontrack: null,
  } as unknown as RTCPeerConnection;
  return {
    channel,
    pc,
    connectTransport: () => {
      (pc as unknown as { connectionState: string }).connectionState = "connected";
      (pc as unknown as { iceConnectionState: string }).iceConnectionState = "connected";
      (channel as unknown as { readyState: string }).readyState = "open";
      listeners.get("connectionstatechange")?.forEach((listener) => listener());
      listeners.get("iceconnectionstatechange")?.forEach((listener) => listener());
      channel.onopen?.();
    },
    failTransport: () => {
      (pc as unknown as { connectionState: string }).connectionState = "failed";
      listeners.get("connectionstatechange")?.forEach((listener) => listener());
    },
  };
}

function fakeAudioContext() {
  const starts: number[] = [];
  const buffers: number[][] = [];
  const destination = { stream: fakeMediaStream() };
  const context = {
    currentTime: 1,
    state: "running",
    createMediaStreamDestination: () => destination,
    createMediaStreamSource: () => ({ connect: vi.fn(), disconnect: vi.fn() }),
    createBuffer: (_channels: number, length: number) => {
      const values = new Float32Array(length);
      buffers.push(values as unknown as number[]);
      return { duration: length / 16_000, getChannelData: () => values };
    },
    createBufferSource: () => ({
      buffer: null,
      onended: null,
      connect: vi.fn(),
      disconnect: vi.fn(),
      start: (at: number) => starts.push(at),
      stop: vi.fn(),
    }),
    resume: vi.fn(async () => undefined),
    close: vi.fn(async () => undefined),
  } as unknown as AudioContext;
  return { context, starts, buffers };
}

function candidateAudio(initial: Float32Array) {
  let listener: ((chunk: JarvisAudioChunk) => void) | null = null;
  const unsubscribe = vi.fn();
  const candidate: JarvisCandidateAudio = {
    activationId: "activation-1",
    sampleRate: 16_000,
    subscribe: (next) => {
      listener = next;
      next({
        activationId: "activation-1", sequence: 0, sampleRate: 16_000,
        startSample: 0, endSample: initial.length, capturedMonotonicMs: 0, samples: initial,
      });
      return unsubscribe;
    },
    observeEndpoint: vi.fn(() => new Promise(() => undefined)),
    promoteToConversation: vi.fn(),
    close: vi.fn(),
  };
  return {
    candidate,
    unsubscribe,
    push: (samples: Float32Array, sequence = 1) => listener?.({
      activationId: "activation-1", sequence, sampleRate: 16_000,
      startSample: sequence * samples.length,
      endSample: (sequence + 1) * samples.length,
      capturedMonotonicMs: sequence * 20,
      samples,
    }),
  };
}

describe("JarvisLiveController", () => {
  beforeEach(() => {
    vi.stubGlobal("AudioContext", vi.fn(() => fakeAudioContext().context));
  });

  afterEach(() => vi.unstubAllGlobals());
  it("retains the native browser receiver for startup timers", async () => {
    const { pc } = fakePc();
    const nativeSetTimeout = globalThis.setTimeout;
    const nativeClearTimeout = globalThis.clearTimeout;
    const strictSetTimeout = function (
      this: typeof globalThis,
      callback: TimerHandler,
      delay?: number,
      ...args: unknown[]
    ) {
      if (this !== globalThis) throw new TypeError("Illegal invocation");
      return nativeSetTimeout(callback, delay, ...args);
    } as typeof globalThis.setTimeout;
    const strictClearTimeout = function (
      this: typeof globalThis,
      timer?: ReturnType<typeof globalThis.setTimeout>,
    ) {
      if (this !== globalThis) throw new TypeError("Illegal invocation");
      return nativeClearTimeout(timer);
    } as typeof globalThis.clearTimeout;
    vi.stubGlobal("setTimeout", strictSetTimeout);
    vi.stubGlobal("clearTimeout", strictClearTimeout);
    const controller = new JarvisLiveController("c", {}, {
      peerConnection: () => pc,
      getUserMedia: async () => fakeMediaStream(),
      getBackendState: async () => fakeBackendState(),
      createCall: async () => fakeCall(),
    });
    vi.stubGlobal("setTimeout", nativeSetTimeout);
    vi.stubGlobal("clearTimeout", nativeClearTimeout);

    await expect(controller.start()).resolves.toMatchObject({ status: "connected" });
    vi.unstubAllGlobals();
  });

  it("connects when backend readiness precedes SDP and transport without a browser session.started", async () => {
    const remote = deferred<void>();
    const { pc } = fakePc({ remoteDescription: remote.promise });
    const diagnostics: string[] = [];
    const controller = new JarvisLiveController("c", {
      onDiagnostic: (event) => diagnostics.push(event.event),
    }, {
      peerConnection: () => pc,
      getUserMedia: async () => fakeMediaStream(),
      getBackendState: vi.fn(async () => fakeBackendState()),
      createCall: async () => fakeCall(),
    });
    controller.recordLiveStreamState("opened");
    const pending = controller.start();
    await vi.waitFor(() => expect((pc.createOffer as ReturnType<typeof vi.fn>)).toHaveBeenCalled());
    controller.recordBackendEvent({
      source_domain: "session", source_id: "session-1", event_type: "provider.ready",
      payload: { session_id: "session-1", generation: 1 }, sequence: 1,
    } as never);
    await vi.waitFor(() => expect(diagnostics).toContain("backend_ready"));
    expect(controller.current().status).toBe("connecting");
    remote.resolve();
    await pending;

    expect(controller.current().status).toBe("connected");
    expect(diagnostics).toContain("sdp_applied");
    expect(diagnostics).toContain("data_channel_open");
    expect(diagnostics).not.toContain("browser_session_started");
  });

  it("prepares the audio graph, data channel, offer, and ICE before creating a billable session", async () => {
    const stream = fakeMediaStream();
    const { pc } = fakePc();
    const audio = fakeAudioContext();
    const createCall = vi.fn(async () => fakeCall());
    const diagnostics: string[] = [];
    const controller = new JarvisLiveController("c", {
      onDiagnostic: (diagnostic) => diagnostics.push(diagnostic.event),
    }, {
      peerConnection: () => pc,
      audioContext: () => audio.context,
      getBackendState: async () => fakeBackendState(),
      createCall,
    });

    await controller.prepare(stream);
    expect(pc.createDataChannel).toHaveBeenCalledWith("oai-events");
    expect(pc.createOffer).toHaveBeenCalledOnce();
    expect(pc.setLocalDescription).toHaveBeenCalledOnce();
    expect(createCall).not.toHaveBeenCalled();
    expect(diagnostics).toContain("transport_preparation_ready");

    await controller.startWithOptions({ stream });
    expect(pc.createOffer).toHaveBeenCalledOnce();
    expect(createCall).toHaveBeenCalledOnce();
    expect(diagnostics).toContain("transport_prepared_reused");
  });

  it("refreshes an unclaimed preparation after a network change", async () => {
    const stream = fakeMediaStream();
    const first = fakePc();
    const second = fakePc();
    const peers = [first.pc, second.pc];
    const controller = new JarvisLiveController("c", {}, {
      peerConnection: () => peers.shift()!,
      audioContext: () => fakeAudioContext().context,
      getBackendState: async () => fakeBackendState(),
      createCall: async () => fakeCall(),
    });

    await controller.prepare(stream);
    await controller.refreshPreparation("network_change");
    expect(first.pc.close).toHaveBeenCalledOnce();
    expect(second.pc.createOffer).toHaveBeenCalledOnce();
    await controller.stop();
  });

  it("negotiates oai-events before the offer and accepts backend readiness after transport", async () => {
    const { pc, channel } = fakePc();
    const backend = deferred<void>();
    const createCall = vi.fn(async () => fakeCall());
    const controller = new JarvisLiveController("c", {}, {
      peerConnection: () => pc,
      getUserMedia: async () => fakeMediaStream(),
      getBackendState: async () => { await backend.promise; return fakeBackendState(); },
      createCall,
    });
    const pending = controller.start();
    await vi.waitFor(() => expect(pc.createOffer).toHaveBeenCalled());
    expect(pc.createDataChannel).toHaveBeenCalledWith("oai-events");
    expect(controller.current().status).toBe("connecting");
    channel.onmessage?.({ data: JSON.stringify({ type: "session.started" }) } as MessageEvent);
    backend.resolve();
    await pending;
    expect(controller.current().status).toBe("connected");
    expect(createCall).toHaveBeenCalledWith("c", "controller", "openai_live", "offer", undefined);
  });

  it("fails with the missing transport conditions and closes the accepted backend session", async () => {
    const { pc } = fakePc({ autoConnect: false });
    const closeCall = vi.fn(async () => undefined);
    const setTimeout = vi.fn((callback: () => void) => {
      queueMicrotask(callback);
      return 1 as unknown as ReturnType<typeof globalThis.setTimeout>;
    }) as typeof globalThis.setTimeout;
    const controller = new JarvisLiveController("c", {}, {
      peerConnection: () => pc,
      getUserMedia: async () => fakeMediaStream(),
      getBackendState: async () => fakeBackendState(),
      createCall: async () => fakeCall(),
      closeCall,
      setTimeout,
      clearTimeout: vi.fn() as typeof globalThis.clearTimeout,
    });

    await expect(controller.start()).rejects.toThrow("WebRTC connected, oai-events open");
    expect(closeCall).toHaveBeenCalledWith("session-1", 1);
  });

  it("fails immediately when the backend sideband fails", async () => {
    const { pc } = fakePc();
    const closeCall = vi.fn(async () => undefined);
    const controller = new JarvisLiveController("c", {}, {
      peerConnection: () => pc,
      getUserMedia: async () => fakeMediaStream(),
      getBackendState: async () => fakeBackendState("disconnected"),
      createCall: async () => fakeCall(),
      closeCall,
    });

    await expect(controller.start()).rejects.toThrow("sideband disconnected");
    expect(closeCall).toHaveBeenCalledWith("session-1", 1);
  });

  it("continuously observes sideband health without a final snapshot request", async () => {
    const { pc } = fakePc();
    const closeCall = vi.fn(async () => undefined);
    const getBackendState = vi.fn(async () => fakeBackendState());
    const controller = new JarvisLiveController("c", {}, {
      peerConnection: () => pc,
      getUserMedia: async () => fakeMediaStream(),
      getBackendState,
      createCall: async () => fakeCall(),
      closeCall,
    });
    controller.recordLiveStreamState("opened");
    const pending = controller.start();
    await vi.waitFor(() => expect((pc.createOffer as ReturnType<typeof vi.fn>)).toHaveBeenCalled());
    controller.recordBackendEvent({
      source_domain: "session", source_id: "session-1", event_type: "provider.ready",
      payload: { session_id: "session-1", generation: 1 }, sequence: 1,
    } as never);
    await pending;
    expect(getBackendState).not.toHaveBeenCalled();
    controller.recordBackendEvent({
      source_domain: "session", source_id: "session-1", event_type: "provider.disconnected",
      payload: { session_id: "session-1", generation: 1 }, sequence: 2,
    } as never);
    await vi.waitFor(() => expect(controller.current().status).toBe("error"));
    expect(closeCall).toHaveBeenCalledWith("session-1", 1);
  });

  it("closes an accepted call that arrives after startup cancellation", async () => {
    const { pc, channel } = fakePc();
    const response = deferred<JarvisLiveCallResponse>();
    const closeCall = vi.fn(async () => undefined);
    const controller = new JarvisLiveController("c", {}, {
      peerConnection: () => pc,
      getUserMedia: async () => fakeMediaStream(),
      getBackendState: async () => fakeBackendState(),
      createCall: () => response.promise,
      closeCall,
    });
    const pending = controller.start();
    await vi.waitFor(() => expect(pc.createOffer).toHaveBeenCalled());
    await controller.stop();
    response.resolve(fakeCall());

    await expect(pending).rejects.toThrow("cancelled");
    expect(channel.close).toHaveBeenCalled();
    expect(closeCall).toHaveBeenCalledTimes(1);
    expect(closeCall).toHaveBeenCalledWith("session-1", 1);
    expect(controller.current().status).toBe("closed");
  });

  it("ignores stale transport and provider callbacks after cancellation", async () => {
    const first = fakePc({ autoConnect: false });
    const second = fakePc();
    const firstResponse = deferred<JarvisLiveCallResponse>();
    const controllers = [first.pc, second.pc];
    const controller = new JarvisLiveController("c", {}, {
      peerConnection: () => controllers.shift()!,
      getUserMedia: async () => fakeMediaStream(),
      getBackendState: async () => fakeBackendState(),
      createCall: vi.fn()
        .mockImplementationOnce(() => firstResponse.promise)
        .mockImplementationOnce(async () => fakeCall()),
    });
    const staleOnMessage = await (async () => {
      const pending = controller.start();
      await vi.waitFor(() => expect(first.channel.onmessage).toBeTypeOf("function"));
      const handler = first.channel.onmessage;
      await controller.stop();
      firstResponse.resolve(fakeCall());
      await expect(pending).rejects.toThrow("cancelled");
      return handler;
    })();

    const next = controller.start();
    staleOnMessage?.({ data: JSON.stringify({ type: "session.started" }) } as MessageEvent);
    await next;
    expect(controller.current().status).toBe("connected");
  });

  it("does not send raw audio or legacy cancellation events", async () => {
    const { pc, channel } = fakePc();
    const controller = new JarvisLiveController("c", {}, {
      peerConnection: () => pc,
      getUserMedia: async () => fakeMediaStream(),
      getBackendState: async () => fakeBackendState(),
      createCall: async () => fakeCall(),
    });
    const pending = controller.start();
    await vi.waitFor(() => expect(channel.onmessage).toBeTypeOf("function"));
    channel.onmessage?.({ data: JSON.stringify({ type: "session.started" }) } as MessageEvent);
    await pending;
    controller.interrupt();
    const sentTypes = (channel.send as ReturnType<typeof vi.fn>).mock.calls.map(([value]) => (
      JSON.parse(String(value)) as { type?: string }
    ).type);
    expect(sentTypes).not.toContain("response.cancel");
    expect(sentTypes).not.toContain("session.input_audio.append");
  });

  it("streams queued and incoming activation audio without waiting for the endpoint", async () => {
    const { pc, channel } = fakePc();
    const audio = fakeAudioContext();
    const activation = candidateAudio(new Float32Array([0.1, 0.2]));
    const controller = new JarvisLiveController("c", {}, {
      peerConnection: () => pc,
      getUserMedia: async () => fakeMediaStream(),
      getBackendState: async () => fakeBackendState(),
      createCall: async () => fakeCall(),
      audioContext: () => audio.context,
    });
    const pending = controller.startWithOptions({ candidateAudio: activation.candidate });
    await vi.waitFor(() => expect(channel.onmessage).toBeTypeOf("function"));
    channel.onmessage?.({ data: JSON.stringify({ type: "session.started" }) } as MessageEvent);
    await pending;

    expect(activation.candidate.observeEndpoint).not.toHaveBeenCalled();
    expect(activation.candidate.promoteToConversation).toHaveBeenCalledOnce();
    expect(audio.starts).toHaveLength(1);
    activation.push(new Float32Array([0.3, 0.4]));
    expect(audio.starts).toHaveLength(2);
    expect(audio.starts[1]).toBeGreaterThan(audio.starts[0]);
    expect(activation.unsubscribe).not.toHaveBeenCalled();
  });

  it("catches up only through confidently silent activation audio", async () => {
    const { pc, channel } = fakePc();
    const audio = fakeAudioContext();
    const activation = candidateAudio(new Float32Array(16_000));
    const controller = new JarvisLiveController("c", {}, {
      peerConnection: () => pc,
      getUserMedia: async () => fakeMediaStream(),
      getBackendState: async () => fakeBackendState(),
      createCall: async () => fakeCall(),
      audioContext: () => audio.context,
    });
    const pending = controller.startWithOptions({ candidateAudio: activation.candidate });
    await vi.waitFor(() => expect(channel.onmessage).toBeTypeOf("function"));
    channel.onmessage?.({ data: JSON.stringify({ type: "session.started" }) } as MessageEvent);
    await pending;

    expect(audio.buffers.reduce((total, samples) => total + samples.length, 0)).toBe(8_000);
  });

  it("preserves uninterrupted quiet speech instead of forcing catch-up", async () => {
    const { pc, channel } = fakePc();
    const audio = fakeAudioContext();
    const quietSpeech = new Float32Array(16_000).fill(0.002);
    const activation = candidateAudio(quietSpeech);
    const controller = new JarvisLiveController("c", {}, {
      peerConnection: () => pc,
      getUserMedia: async () => fakeMediaStream(),
      getBackendState: async () => fakeBackendState(),
      createCall: async () => fakeCall(),
      audioContext: () => audio.context,
    });
    const pending = controller.startWithOptions({ candidateAudio: activation.candidate });
    await vi.waitFor(() => expect(channel.onmessage).toBeTypeOf("function"));
    channel.onmessage?.({ data: JSON.stringify({ type: "session.started" }) } as MessageEvent);
    await pending;

    expect(audio.buffers.reduce((total, samples) => total + samples.length, 0)).toBe(16_000);
  });

  it("does not extend idle life for silent or housekeeping events", async () => {
    const { pc, channel } = fakePc();
    const scheduled: Array<{ callback: () => void; delay: number }> = [];
    const setTimeout = vi.fn((callback: () => void, delay = 0) => {
      scheduled.push({ callback, delay });
      return scheduled.length as unknown as ReturnType<typeof globalThis.setTimeout>;
    }) as typeof globalThis.setTimeout;
    const clearTimeout = vi.fn() as typeof globalThis.clearTimeout;
    const controller = new JarvisLiveController("c", {}, {
      peerConnection: () => pc,
      getUserMedia: async () => fakeMediaStream(),
      getBackendState: async () => fakeBackendState(),
      createCall: async () => fakeCall(),
      setTimeout,
      clearTimeout,
    });
    const pending = controller.start();
    await vi.waitFor(() => expect(channel.onmessage).toBeTypeOf("function"));
    channel.onmessage?.({ data: JSON.stringify({ type: "session.started" }) } as MessageEvent);
    await pending;
    const afterConnect = setTimeout.mock.calls.length;
    channel.onmessage?.({ data: JSON.stringify({ type: "session.input_audio.activity", level: 0 }) } as MessageEvent);
    expect(setTimeout).toHaveBeenCalledTimes(afterConnect);
    channel.onmessage?.({
      data: JSON.stringify({ type: "session.input_transcript.delta", delta: "hello", start_ms: 1, end_ms: 2 }),
    } as MessageEvent);
    expect(setTimeout.mock.calls.length).toBeGreaterThan(afterConnect);
  });
});
