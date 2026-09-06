import { describe, expect, it, vi } from "vitest";

import type { JarvisRealtimeCallResponse, JarvisSession } from "./api";
import {
  JarvisRealtimeController,
  encodeJarvisWakeAudio,
  summarizeJarvisRealtimeTrace,
  type JarvisRealtimeDependencies,
  type JarvisRealtimeSnapshot,
  type JarvisRealtimeTraceEntry,
} from "./jarvis-realtime";

class FakeTrack {
  stopped = false;
  stop() { this.stopped = true; }
}

class FakeStream {
  readonly track = new FakeTrack();
  getAudioTracks() { return [this.track] as unknown as MediaStreamTrack[]; }
  getTracks() { return [this.track] as unknown as MediaStreamTrack[]; }
}

class FakeSender {
  replacements: MediaStreamTrack[] = [];
  async replaceTrack(track: MediaStreamTrack | null) {
    if (track) this.replacements.push(track);
  }
}

class FakeChannel {
  closed = false;
  readyState: RTCDataChannelState = "open";
  sent: string[] = [];
  onmessage: ((event: MessageEvent) => void) | null = null;
  send(message: string) {
    this.sent.push(message);
    const event = JSON.parse(message) as { type?: string };
    if (event.type === "input_audio_buffer.commit") {
      queueMicrotask(() => this.onmessage?.({
        data: JSON.stringify({ type: "input_audio_buffer.committed", item_id: "wake-item" }),
      } as MessageEvent));
    }
  }
  close() { this.closed = true; }
}

class FakePeer {
  readonly channel = new FakeChannel();
  localDescription: RTCSessionDescription | null = null;
  remoteDescription: RTCSessionDescriptionInit | null = null;
  connectionState: RTCPeerConnectionState = "new";
  ontrack: ((event: RTCTrackEvent) => void) | null = null;
  onconnectionstatechange: (() => void) | null = null;
  closed = false;
  addedTracks = 0;
  offerCount = 0;
  readonly sender = new FakeSender();

  createDataChannel() { return this.channel as unknown as RTCDataChannel; }
  addTrack() { this.addedTracks += 1; return this.sender as unknown as RTCRtpSender; }
  async createOffer() {
    this.offerCount += 1;
    return { type: "offer", sdp: "browser-offer" } as RTCSessionDescriptionInit;
  }
  async setLocalDescription(description: RTCSessionDescriptionInit) {
    this.localDescription = description as RTCSessionDescription;
  }
  async setRemoteDescription(description: RTCSessionDescriptionInit) {
    this.remoteDescription = description;
  }
  close() { this.closed = true; }
}

function session(generation: number): JarvisSession {
  return {
    id: `session-${generation}`,
    conversation_id: "log-1",
    generation,
    provider_adapter: "openai",
    lifecycle_state: "ready",
    controller_id: "browser-1",
    floor_state: "listening",
    last_event_sequence: 2,
    created_at: "2026-08-28T00:00:00Z",
    ready_at: "2026-08-28T00:00:01Z",
    disconnected_at: null,
    client_time_zone: "Europe/Amsterdam",
    client_utc_offset_minutes: 120,
    client_observed_at: "2026-08-28T00:00:00Z",
  };
}

function response(generation: number, rotateAfterSeconds = 3_300): JarvisRealtimeCallResponse {
  return {
    role: "controller",
    session: session(generation),
    sdp_answer: `provider-answer-${generation}`,
    rotate_after_seconds: rotateAfterSeconds,
  };
}

function connectingResponse(generation: number): JarvisRealtimeCallResponse {
  return {
    ...response(generation),
    session: {
      ...session(generation),
      lifecycle_state: "connecting",
      floor_state: "connecting",
      ready_at: null,
    },
  };
}

function liveEvent(
  eventType: string,
  generation: number,
  payload: Record<string, unknown> = {},
) {
  return {
    id: `event-${eventType}-${generation}`,
    sequence: generation,
    event_type: eventType,
    source_domain: "session" as const,
    source_id: null,
    source_sequence: generation,
    payload: { session_id: `session-${generation}`, generation, ...payload },
    created_at: "2026-08-28T00:00:01Z",
  };
}

function dependencies(overrides: Partial<JarvisRealtimeDependencies> = {}) {
  const peers: FakePeer[] = [];
  const streams: FakeStream[] = [];
  const timers: Array<() => void> = [];
  const createCall = vi.fn().mockResolvedValue(response(1));
  const closeSession = vi.fn().mockResolvedValue(undefined);
  const wait = vi.fn().mockResolvedValue(undefined);
  const detector = { destroy: vi.fn() };
  const handoffTransports: Array<{
    stream: FakeStream;
    connectLive: ReturnType<typeof vi.fn>;
    destroy: ReturnType<typeof vi.fn>;
  }> = [];
  let speechStart: (() => void) | null = null;
  const value: JarvisRealtimeDependencies = {
    createPeer: () => {
      const peer = new FakePeer();
      peers.push(peer);
      return peer as unknown as RTCPeerConnection;
    },
    getUserMedia: async () => {
      const stream = new FakeStream();
      streams.push(stream);
      return stream as unknown as MediaStream;
    },
    createCall,
    closeSession,
    wait,
    setTimer: (callback) => {
      timers.push(callback);
      return timers.length;
    },
    clearTimer: vi.fn(),
    now: vi.fn().mockReturnValue(1_000),
    wallNow: vi.fn().mockReturnValue("2026-08-28T00:00:00.000Z"),
    createBargeInDetector: vi.fn(async (_stream, onSpeechStart) => {
      speechStart = onSpeechStart;
      return detector;
    }),
    createHandoffTransport: vi.fn(async () => {
      const output = {
        stream: new FakeStream(),
        connectLive: vi.fn().mockResolvedValue(undefined),
        destroy: vi.fn().mockResolvedValue(undefined),
      };
      handoffTransports.push(output);
      return output as unknown as Awaited<ReturnType<JarvisRealtimeDependencies["createHandoffTransport"]>>;
    }),
    ...overrides,
  };
  return {
    value,
    peers,
    streams,
    timers,
    createCall,
    closeSession,
    wait,
    detector,
    handoffTransports,
    triggerSpeechStart: () => speechStart?.(),
  };
}

describe("JarvisRealtimeController", () => {
  it("creates WebRTC media through the backend and tears everything down", async () => {
    const deps = dependencies();
    const snapshots: JarvisRealtimeSnapshot[] = [];
    const controller = new JarvisRealtimeController(
      { controllerId: "browser-1", onSnapshot: (snapshot) => snapshots.push(snapshot) },
      deps.value,
    );

    const connected = await controller.start();

    expect(connected.status).toBe("connected");
    expect(deps.createCall).toHaveBeenCalledWith(
      "browser-1",
      "controller",
      "openai",
      "browser-offer",
      undefined,
      undefined,
    );
    expect(deps.peers[0].addedTracks).toBe(1);
    expect(deps.peers[0].remoteDescription).toEqual({ type: "answer", sdp: "provider-answer-1" });
    expect(snapshots.map((snapshot) => snapshot.status)).toEqual(["connecting", "connected"]);

    await controller.stop();
    expect(deps.peers[0].closed).toBe(true);
    expect(deps.peers[0].channel.closed).toBe(true);
    expect(deps.streams[0].track.stopped).toBe(true);
    expect(deps.detector.destroy).toHaveBeenCalledOnce();
    expect(deps.closeSession).toHaveBeenCalledWith("session-1", 1);
    expect(controller.current().status).toBe("idle");
  });

  it("prepares the local WebRTC offer while armed and reuses it after wake", async () => {
    const deps = dependencies();
    const wakeStream = new FakeStream();
    const controller = new JarvisRealtimeController({ controllerId: "browser-1" }, deps.value);

    await controller.prepare(wakeStream as unknown as MediaStream);
    expect(deps.peers).toHaveLength(1);
    expect(deps.peers[0].offerCount).toBe(1);
    expect(deps.createCall).not.toHaveBeenCalled();

    await controller.start({
      audioHandoff: {
        localStream: wakeStream as unknown as MediaStream,
        utterance: Promise.resolve(new Float32Array([0.1, 0.2])),
        sampleRate: 16_000,
      },
      activationContext: { source: "wake_word", wake_score: 0.22 },
    });

    expect(deps.peers).toHaveLength(1);
    expect(deps.peers[0].offerCount).toBe(1);
    expect(deps.peers[0].sender.replacements).toHaveLength(1);
    expect(deps.createCall.mock.calls[0][5]).toMatchObject({
      source: "wake_word",
      wake_score: 0.22,
      wake_detected_at: "2026-08-28T00:00:00.000Z",
    });
    expect(deps.createCall.mock.calls[0][5].activation_id).toBeTruthy();
    await controller.stop();
  });

  it("keeps wake audio and the live mic gated until backend control is ready", async () => {
    const createCall = vi.fn().mockResolvedValue(connectingResponse(1));
    const deps = dependencies({ createCall });
    const wakeStream = new FakeStream();
    const controller = new JarvisRealtimeController({ controllerId: "browser-1" }, deps.value);

    const started = controller.start({
      audioHandoff: {
        localStream: wakeStream as unknown as MediaStream,
        utterance: Promise.resolve(new Float32Array([0.1, 0.2])),
        sampleRate: 16_000,
      },
    });
    await vi.waitFor(() => expect(deps.peers[0].remoteDescription).not.toBeNull());

    expect(deps.peers[0].channel.sent).toEqual([]);
    expect(deps.handoffTransports[0].connectLive).not.toHaveBeenCalled();
    controller.recordBackendEvent(liveEvent("provider.ready", 1));
    await started;

    expect(deps.peers[0].channel.sent.map((message) => JSON.parse(message).type)).toEqual([
      "input_audio_buffer.append",
      "input_audio_buffer.commit",
    ]);
    expect(deps.handoffTransports[0].connectLive).toHaveBeenCalledOnce();
    await controller.stop();
  });

  it("uses a provider-ready event that arrives before the call response", async () => {
    let resolveCall: ((value: JarvisRealtimeCallResponse) => void) | null = null;
    const createCall = vi.fn(() => new Promise<JarvisRealtimeCallResponse>((resolve) => {
      resolveCall = resolve;
    }));
    const deps = dependencies({ createCall });
    const controller = new JarvisRealtimeController({ controllerId: "browser-1" }, deps.value);

    const started = controller.start();
    await vi.waitFor(() => expect(createCall).toHaveBeenCalledOnce());
    controller.recordBackendEvent(liveEvent("provider.ready", 1));
    resolveCall?.(connectingResponse(1));
    await started;

    expect(deps.handoffTransports[0].connectLive).toHaveBeenCalledOnce();
    await controller.stop();
  });

  it("retries without opening the mic when sideband control fails after SDP", async () => {
    const createCall = vi.fn()
      .mockResolvedValueOnce(connectingResponse(1))
      .mockResolvedValueOnce(response(2));
    const deps = dependencies({ createCall });
    const controller = new JarvisRealtimeController({ controllerId: "browser-1" }, deps.value);

    const started = controller.start();
    await vi.waitFor(() => expect(deps.peers[0].remoteDescription).not.toBeNull());
    controller.recordBackendEvent(liveEvent("provider.provider_error", 1, { reason: "sideband unavailable" }));
    await started;

    expect(createCall).toHaveBeenCalledTimes(2);
    expect(deps.handoffTransports[0].connectLive).not.toHaveBeenCalled();
    expect(deps.handoffTransports[0].destroy).toHaveBeenCalledOnce();
    expect(deps.handoffTransports[1].connectLive).toHaveBeenCalledOnce();
    await controller.stop();
  });

  it("uploads metadata-only timing traces in a batch", async () => {
    const sendTelemetry = vi.fn().mockResolvedValue(undefined);
    const deps = dependencies({ sendTelemetry });
    const controller = new JarvisRealtimeController({ controllerId: "browser-1" }, deps.value);

    await controller.start();
    controller.recordClientSignal("wake_candidate", { probability: 0.8 });
    await controller.stop();

    expect(sendTelemetry).toHaveBeenCalled();
    const events = sendTelemetry.mock.calls.flatMap(([batch]) => batch);
    expect(events.some((event) => event.event_type === "wake_candidate")).toBe(true);
    expect(events.every((event) => !("audio" in (event.detail ?? {})))).toBe(true);
  });

  it("retries at 0.5, 1, and 2 seconds before entering typed fallback", async () => {
    const createCall = vi.fn().mockRejectedValue(new Error("provider unavailable"));
    const deps = dependencies({ createCall });
    const controller = new JarvisRealtimeController({ controllerId: "browser-1" }, deps.value);

    await expect(controller.start()).rejects.toThrow("provider unavailable");

    expect(createCall).toHaveBeenCalledTimes(4);
    expect(deps.wait.mock.calls.map(([delay]) => delay)).toEqual([500, 1_000, 2_000]);
    expect(deps.streams.every((stream) => stream.track.stopped)).toBe(true);
    expect(controller.current()).toMatchObject({ status: "text_fallback", attempt: 4 });
  });

  it("uses normal controller supersession for scheduled rotation", async () => {
    const createCall = vi
      .fn()
      .mockResolvedValueOnce(response(1, 1))
      .mockResolvedValueOnce(response(2, 1));
    const deps = dependencies({ createCall });
    const controller = new JarvisRealtimeController({ controllerId: "browser-1" }, deps.value);

    await controller.start();
    expect(deps.timers).toHaveLength(1);
    deps.timers[0]();
    await vi.waitFor(() => expect(controller.current().session?.generation).toBe(2));

    expect(createCall).toHaveBeenCalledTimes(2);
    expect(deps.peers[0].closed).toBe(true);
    expect(deps.streams).toHaveLength(1);
    expect(deps.streams[0].track.stopped).toBe(false);
    expect(deps.closeSession).not.toHaveBeenCalledWith("session-1", 1);
    await controller.stop();
    expect(deps.streams[0].track.stopped).toBe(true);
  });

  it("keeps the active mic alive when the first rotation attempt fails", async () => {
    const createCall = vi
      .fn()
      .mockResolvedValueOnce(response(1, 1))
      .mockRejectedValueOnce(new Error("transient rotation failure"))
      .mockResolvedValueOnce(response(2, 1));
    const deps = dependencies({ createCall });
    const controller = new JarvisRealtimeController({ controllerId: "browser-1" }, deps.value);

    await controller.start();
    deps.timers[0]();
    await vi.waitFor(() => expect(controller.current().session?.generation).toBe(2));

    expect(deps.streams).toHaveLength(1);
    expect(deps.streams[0].track.stopped).toBe(false);
    expect(createCall).toHaveBeenCalledTimes(3);
    await controller.stop();
    expect(deps.streams[0].track.stopped).toBe(true);
  });

  it("reconnects after a failed peer connection", async () => {
    const createCall = vi
      .fn()
      .mockResolvedValueOnce(response(1))
      .mockResolvedValueOnce(response(2));
    const deps = dependencies({ createCall });
    const controller = new JarvisRealtimeController({ controllerId: "browser-1" }, deps.value);

    await controller.start();
    deps.peers[0].connectionState = "failed";
    deps.peers[0].onconnectionstatechange?.();
    await vi.waitFor(() => expect(controller.current().session?.generation).toBe(2));

    expect(createCall).toHaveBeenCalledTimes(2);
    await controller.stop();
  });

  it("reconnects when a peer remains disconnected beyond the grace period", async () => {
    const createCall = vi
      .fn()
      .mockResolvedValueOnce(response(1))
      .mockResolvedValueOnce(response(2));
    const deps = dependencies({ createCall });
    const controller = new JarvisRealtimeController({ controllerId: "browser-1" }, deps.value);

    await controller.start();
    deps.peers[0].connectionState = "disconnected";
    deps.peers[0].onconnectionstatechange?.();
    expect(deps.timers).toHaveLength(2);
    deps.timers[1]();
    await vi.waitFor(() => expect(controller.current().session?.generation).toBe(2));

    expect(createCall).toHaveBeenCalledTimes(2);
    await controller.stop();
  });

  it("mutes immediately and explicitly cancels after confirmed local barge-in", async () => {
    const deps = dependencies();
    const muted: boolean[] = [];
    const controller = new JarvisRealtimeController(
      { controllerId: "browser-1", onPlaybackMuted: (value) => muted.push(value) },
      deps.value,
    );

    await controller.start();
    deps.peers[0].channel.onmessage?.({
      data: JSON.stringify({ type: "output_audio_buffer.started", response_id: "resp-1" }),
    } as MessageEvent);
    deps.triggerSpeechStart();

    expect(muted).toEqual([true]);
    expect(deps.peers[0].channel.sent).toEqual([]);
    deps.timers.at(-1)?.();
    expect(deps.peers[0].channel.sent).toHaveLength(2);
    expect(JSON.parse(deps.peers[0].channel.sent[0])).toMatchObject({
      type: "response.cancel",
      response_id: "resp-1",
    });
    expect(JSON.parse(deps.peers[0].channel.sent[1])).toMatchObject({
      type: "output_audio_buffer.clear",
    });
    expect(controller.traceSnapshot().map((entry) => entry.kind)).toContain(
      "output_audio_buffer.clear.sent",
    );

    deps.peers[0].channel.onmessage?.({
      data: JSON.stringify({ type: "output_audio_buffer.cleared", response_id: "resp-1" }),
    } as MessageEvent);
    expect(muted).toEqual([true, false]);
    await controller.stop();
  });

  it("uses provider speech-start as the explicit cancellation fallback", async () => {
    const deps = dependencies();
    const muted: boolean[] = [];
    const controller = new JarvisRealtimeController(
      { controllerId: "browser-1", onPlaybackMuted: (value) => muted.push(value) },
      deps.value,
    );

    await controller.start();
    deps.peers[0].channel.onmessage?.({
      data: JSON.stringify({ type: "output_audio_buffer.started", response_id: "resp-2" }),
    } as MessageEvent);
    deps.peers[0].channel.onmessage?.({
      data: JSON.stringify({ type: "input_audio_buffer.speech_started" }),
    } as MessageEvent);

    expect(muted).toEqual([true]);
    expect(deps.peers[0].channel.sent.map((message) => JSON.parse(message).type)).toEqual([
      "response.cancel",
      "output_audio_buffer.clear",
    ]);
    await controller.stop();
  });

  it("can cancel a response that is generating before audio starts", async () => {
    const deps = dependencies();
    const controller = new JarvisRealtimeController({ controllerId: "browser-1" }, deps.value);
    await controller.start();
    deps.peers[0].channel.onmessage?.({
      data: JSON.stringify({ type: "response.created", response: { id: "resp-early" } }),
    } as MessageEvent);

    deps.triggerSpeechStart();
    deps.timers.at(-1)?.();

    expect(deps.peers[0].channel.sent.map((message) => JSON.parse(message).type)).toEqual([
      "response.cancel",
      "output_audio_buffer.clear",
    ]);
    await controller.stop();
  });

  it("bulk-uploads a wake utterance before enabling live audio without taking ownership of the wake mic", async () => {
    const deps = dependencies();
    const wakeStream = new FakeStream();
    const samples = new Float32Array([0.1, 0.2]);
    const controller = new JarvisRealtimeController({ controllerId: "browser-1" }, deps.value);

    await controller.start({
      audioHandoff: {
        localStream: wakeStream as unknown as MediaStream,
        utterance: Promise.resolve(samples),
        sampleRate: 16_000,
      },
    });

    expect(deps.streams).toHaveLength(0);
    expect(deps.handoffTransports).toHaveLength(1);
    expect(deps.handoffTransports[0].connectLive).toHaveBeenCalledOnce();
    expect(deps.peers[0].channel.sent.map((message) => JSON.parse(message).type)).toEqual([
      "input_audio_buffer.append",
      "input_audio_buffer.commit",
    ]);
    expect([...samples]).toEqual([0, 0]);
    expect(deps.peers[0].addedTracks).toBe(1);
    await controller.stop();
    expect(wakeStream.track.stopped).toBe(false);
    expect(deps.handoffTransports[0].destroy).toHaveBeenCalledOnce();
  });

  it("encodes wake audio as bounded 24 kHz PCM16 chunks", () => {
    const samples = new Float32Array(16_000 * 10);
    samples[0] = -1;
    samples[1] = 1;

    const encoded = encodeJarvisWakeAudio(samples, 16_000);

    expect(encoded.targetSampleRate).toBe(24_000);
    expect(encoded.byteLength).toBe(24_000 * 10 * 2);
    expect(encoded.durationMs).toBe(10_000);
    expect(encoded.chunks).toHaveLength(20);
    expect(encoded.chunks.every((chunk) => globalThis.atob(chunk).length <= 24_000)).toBe(true);
    const first = Uint8Array.from(globalThis.atob(encoded.chunks[0]), (value) => value.charCodeAt(0));
    const pcm = new DataView(first.buffer);
    expect(pcm.getInt16(0, true)).toBe(-32_768);
    expect(pcm.getInt16(2, true)).toBeLessThan(32_767);
  });

  it("connects the live mic directly when the captured wake utterance is empty", async () => {
    const deps = dependencies();
    const wakeStream = new FakeStream();
    const controller = new JarvisRealtimeController({ controllerId: "browser-1" }, deps.value);

    await controller.start({
      audioHandoff: {
        localStream: wakeStream as unknown as MediaStream,
        utterance: Promise.resolve(new Float32Array()),
        sampleRate: 16_000,
      },
    });

    expect(deps.peers[0].channel.sent).toEqual([]);
    expect(deps.handoffTransports[0].connectLive).toHaveBeenCalledOnce();
    await controller.stop();
  });

  it("retains encoded wake audio across a failed connection attempt", async () => {
    const createCall = vi.fn()
      .mockRejectedValueOnce(new Error("temporary provider failure"))
      .mockResolvedValueOnce(response(2));
    const deps = dependencies({ createCall });
    const wakeStream = new FakeStream();
    const samples = new Float32Array([0.1, 0.2, 0.3]);
    const controller = new JarvisRealtimeController({ controllerId: "browser-1" }, deps.value);

    await controller.start({
      audioHandoff: {
        localStream: wakeStream as unknown as MediaStream,
        utterance: Promise.resolve(samples),
        sampleRate: 16_000,
      },
    });

    expect(createCall).toHaveBeenCalledTimes(2);
    expect(deps.peers.flatMap((peer) => peer.channel.sent).filter(
      (message) => JSON.parse(message).type === "input_audio_buffer.append",
    )).toHaveLength(1);
    expect([...samples]).toEqual([0, 0, 0]);
    await controller.stop();
  });

  it("summarizes connection and turn latency without discarding the raw trace", () => {
    const entry = (
      kind: string,
      monotonic_ms: number,
      extra: Partial<JarvisRealtimeTraceEntry> = {},
    ): JarvisRealtimeTraceEntry => ({
      kind,
      monotonic_ms,
      wall_time: "2026-08-28T00:00:00.000Z",
      ...extra,
    });
    const entries = [
      entry("wake_detected", 100),
      entry("realtime_start_requested", 105),
      entry("realtime_connection_attempt_started", 110, { attempt: 1 }),
      entry("realtime_offer_started", 120),
      entry("realtime_offer_ready", 170),
      entry("realtime_backend_call_started", 175),
      entry("backend.provider.ready", 250, {
        timing_ms: { provider_call: 55, sideband_connect: 15 },
      }),
      entry("realtime_backend_call_completed", 300),
      entry("realtime_remote_description_set", 310),
      entry("realtime_connection_ready", 320),
      entry("wake_local_speech_stopped", 400),
      entry("wake_utterance_ready", 1_450),
      entry("input_audio_buffer.speech_stopped", 1_600),
      entry("backend.response.arbiter.started", 1_680, {
        timing_ms: { total_to_provider_submit: 42 },
      }),
      entry("response.created", 1_700, { response_id: "response-1" }),
      entry("response.output_audio.delta", 1_900, { response_id: "response-1" }),
      entry("output_audio_buffer.started", 2_000, { response_id: "response-1" }),
    ];

    expect(summarizeJarvisRealtimeTrace(entries)).toEqual({
      schema: "jarvis.realtime_timing.v1",
      connections: [{
        attempt: 1,
        total_ms: 215,
        offer_ms: 50,
        backend_call_ms: 125,
        remote_description_ms: 10,
        wake_detect_to_connected_ms: 220,
        provider_call_ms: 55,
        sideband_connect_ms: 15,
      }],
      turns: [{
        turn: 1,
        kind: "wake",
        response_id: "response-1",
        local_speech_stop_to_provider_stop_ms: 1_200,
        wake_handoff_tail_ms: 1_050,
        provider_stop_to_response_created_ms: 100,
        response_created_to_first_audio_ms: 200,
        provider_stop_to_playback_started_ms: 400,
        local_speech_stop_to_playback_started_ms: 1_600,
        backend_arbiter_ms: 42,
      }],
    });
  });
});
