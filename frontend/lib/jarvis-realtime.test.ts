import { describe, expect, it, vi } from "vitest";

import type { JarvisRealtimeCallResponse, JarvisSession } from "./api";
import {
  JarvisRealtimeController,
  encodeJarvisWakeAudio,
  estimateRealtimeClockAlignment,
  summarizeJarvisRealtimeTrace,
  type JarvisRealtimeDependencies,
  type JarvisRealtimeSnapshot,
  type JarvisRealtimeTraceEntry,
} from "./jarvis-realtime";

class FakeTrack {
  stopped = false;
  enabled = true;
  muted = false;
  readyState: MediaStreamTrackState = "live";
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
  iceConnectionState: RTCIceConnectionState = "new";
  iceGatheringState: RTCIceGatheringState = "complete";
  signalingState: RTCSignalingState = "stable";
  sctp = { state: "connected" };
  ontrack: ((event: RTCTrackEvent) => void) | null = null;
  onconnectionstatechange: (() => void) | null = null;
  closed = false;
  addedTracks = 0;
  offerCount = 0;
  inboundBytesReceived = 0;
  inboundPacketsReceived = 0;
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
  async getStats() {
    return new Map([
      ["transport", { id: "transport", type: "transport", selectedCandidatePairId: "pair" }],
      ["pair", {
        id: "pair",
        type: "candidate-pair",
        selected: true,
        nominated: true,
        localCandidateId: "local",
        remoteCandidateId: "remote",
        currentRoundTripTime: 0.012,
        bytesSent: 120,
        bytesReceived: 340,
      }],
      ["local", { id: "local", type: "local-candidate", candidateType: "host" }],
      ["remote", { id: "remote", type: "remote-candidate", candidateType: "relay" }],
    ]) as unknown as RTCStatsReport;
  }
  getReceivers() {
    return [{
      track: { kind: "audio" },
      getStats: async () => new Map([["inbound", {
        id: "inbound",
        type: "inbound-rtp",
        kind: "audio",
        timestamp: 1_000,
        bytesReceived: this.inboundBytesReceived,
        packetsReceived: this.inboundPacketsReceived,
        totalSamplesReceived: this.inboundBytesReceived / 2,
      }]]) as unknown as RTCStatsReport,
    }] as unknown as RTCRtpReceiver[];
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
    server_received_at: "2026-08-28T00:00:00.100Z",
    server_responded_at: "2026-08-28T00:00:00.200Z",
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
  const telemetryTimers: Array<() => void> = [];
  const telemetryTimerDelays: number[] = [];
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
    setTelemetryTimer: (callback, milliseconds) => {
      telemetryTimers.push(callback);
      telemetryTimerDelays.push(milliseconds);
      return telemetryTimers.length;
    },
    clearTelemetryTimer: vi.fn(),
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
    telemetryTimers,
    telemetryTimerDelays,
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

  it("records one sanitized result with transport and selected-candidate statistics", async () => {
    const deps = dependencies();
    const controller = new JarvisRealtimeController({ controllerId: "browser-1" }, deps.value);

    await controller.start();
    await vi.waitFor(() => expect(controller.traceSnapshot().some(
      (event) => event.kind === "connection.attempt.succeeded",
    )).toBe(true));

    const result = controller.traceSnapshot().find(
      (event) => event.kind === "connection.attempt.succeeded",
    );
    expect(result?.attempt).toBe(1);
    expect(result?.timing_ms).toMatchObject({
      media: 0,
      offer: 0,
      backend_call: 0,
      remote_description: 0,
      backend_control: 0,
      microphone_activation: 0,
      total: 0,
    });
    expect(JSON.parse(result?.detail ?? "{}")).toMatchObject({
      result: "succeeded",
      session_id: "session-1",
      generation: 1,
      transport: {
        peer: "new",
        ice: "new",
        ice_gathering: "complete",
        signaling: "stable",
        sctp: "connected",
        data_channel: "open",
        microphone_enabled: true,
        microphone_muted: false,
        microphone_ready_state: "live",
        wake_audio_retained: false,
      },
      network: {
        local_candidate_type: "host",
        remote_candidate_type: "relay",
        rtt_ms: 12,
        bytes_sent: 120,
        bytes_received: 340,
      },
    });
    await controller.stop();
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
    const readiness = controller.traceSnapshot().find(
      (event) => event.kind === "realtime_backend_control_ready",
    );
    expect(JSON.parse(readiness?.detail ?? "{}")).toMatchObject({
      source: "cached_event",
      session_id: "session-1",
      generation: 1,
    });
    await controller.stop();
  });

  it("records live readiness correlation and event delivery timing", async () => {
    const createCall = vi.fn().mockResolvedValue(connectingResponse(1));
    const now = vi.fn()
      .mockReturnValueOnce(1_000)
      .mockReturnValueOnce(1_010)
      .mockReturnValue(1_125);
    const deps = dependencies({
      createCall,
      now,
      wallNow: vi.fn().mockReturnValue("2026-08-28T00:00:01.125Z"),
    });
    const controller = new JarvisRealtimeController({ controllerId: "browser-1" }, deps.value);

    const started = controller.start();
    await vi.waitFor(() => expect(deps.peers[0].remoteDescription).not.toBeNull());
    controller.recordBackendEvent(liveEvent("provider.ready", 1));
    await started;

    const received = controller.traceSnapshot().find(
      (event) => event.kind === "backend.readiness_event_received",
    );
    const resolved = controller.traceSnapshot().find(
      (event) => event.kind === "realtime_backend_control_ready",
    );
    expect(received).toMatchObject({
      sequence: 1,
      server_time: "2026-08-28T00:00:01Z",
      timing_ms: { uncalibrated_delivery: 125 },
    });
    expect(JSON.parse(resolved?.detail ?? "{}")).toMatchObject({
      source: "live_event",
      observed_event_type: "provider.ready",
    });
    await controller.stop();
  });

  it("coalesces repeated live-stream errors and reports reconnection", () => {
    const now = vi.fn().mockReturnValue(2_500)
      .mockReturnValueOnce(1_000)
      .mockReturnValueOnce(1_500)
      .mockReturnValueOnce(2_000)
      .mockReturnValueOnce(2_500);
    const deps = dependencies({ now });
    const controller = new JarvisRealtimeController({ controllerId: "browser-1" }, deps.value);

    controller.recordLiveStreamState("opened", 10, 1);
    controller.recordLiveStreamState("errored", 11, 0);
    controller.recordLiveStreamState("errored", 11, 0);
    controller.recordLiveStreamState("opened", 12, 1);

    const streamEvents = controller.traceSnapshot().filter(
      (event) => event.kind.startsWith("live_stream."),
    );
    expect(streamEvents.map((event) => event.kind)).toEqual([
      "live_stream.opened",
      "live_stream.errored",
      "live_stream.reconnected",
    ]);
    expect(JSON.parse(streamEvents[2].detail ?? "{}")).toMatchObject({
      cursor: 12,
      error_count: 2,
    });
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
    const failed = controller.traceSnapshot().filter(
      (event) => event.kind === "connection.attempt.failed" && event.attempt === 1,
    );
    const cleanup = controller.traceSnapshot().filter(
      (event) => event.kind === "connection.attempt.cleanup" && event.attempt === 1,
    );
    expect(failed).toHaveLength(1);
    expect(cleanup).toHaveLength(1);
    expect(failed[0].timing_ms).toHaveProperty("backend_control");
    expect(JSON.parse(failed[0].detail ?? "{}")).toMatchObject({
      failed_stage: "backend_control",
      error_code: "backend_control_unavailable",
      session_id: "session-1",
      generation: 1,
      transport: { wake_audio_retained: false },
    });
    expect(failed[0].detail).not.toContain("sideband unavailable");
    expect(cleanup[0].timing_ms).toHaveProperty("cleanup");
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

  it("defers telemetry until after the connection critical path", async () => {
    const sendTelemetry = vi.fn().mockResolvedValue(undefined);
    const deps = dependencies({ sendTelemetry });
    const controller = new JarvisRealtimeController({ controllerId: "browser-1" }, deps.value);

    await controller.start();

    expect(sendTelemetry).not.toHaveBeenCalled();
    expect(deps.telemetryTimers).toHaveLength(1);
    deps.telemetryTimers[0]();
    await vi.waitFor(() => expect(sendTelemetry).toHaveBeenCalledOnce());
    await controller.stop();
  });

  it("does not copy noisy or stale backend events into the timing trace", async () => {
    const sendTelemetry = vi.fn().mockResolvedValue(undefined);
    const deps = dependencies({ sendTelemetry });
    const controller = new JarvisRealtimeController({ controllerId: "browser-1" }, deps.value);
    await controller.start();

    controller.recordBackendEvent(liveEvent("provider.assistant_transcript_delta", 1));
    controller.recordBackendEvent(liveEvent("provider.ready", 2));
    await controller.stop();

    const eventTypes = controller.traceSnapshot().map((event) => event.kind);
    expect(eventTypes).not.toContain("backend.provider.assistant_transcript_delta");
    expect(eventTypes).not.toContain("backend.provider.ready");
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

  it("records first playable media from bounded receiver samples and one turn result", async () => {
    let now = 1_000;
    const deps = dependencies({ now: () => now });
    const controller = new JarvisRealtimeController({ controllerId: "browser-1" }, deps.value);
    await controller.start();
    controller.recordPlaybackSignal("audio_element_playing");

    now = 1_050;
    controller.recordClientSignal("local_speech_stopped");
    now = 1_100;
    deps.peers[0].channel.onmessage?.({
      data: JSON.stringify({ type: "input_audio_buffer.speech_stopped", item_id: "user-1" }),
    } as MessageEvent);
    now = 1_200;
    deps.peers[0].channel.onmessage?.({
      data: JSON.stringify({
        type: "conversation.item.input_audio_transcription.completed",
        item_id: "user-1",
      }),
    } as MessageEvent);
    controller.recordBackendEvent(liveEvent("provider.user_transcript", 1, {
      timing_ms: { provider_boundary_to_transcript: 100 },
    }));
    now = 1_250;
    controller.recordBackendEvent(liveEvent("response.arbiter.started", 1, {
      timing_ms: { total_to_provider_submit: 25 },
    }));
    now = 1_300;
    deps.peers[0].channel.onmessage?.({
      data: JSON.stringify({ type: "response.created", response: { id: "response-media" } }),
    } as MessageEvent);
    await vi.waitFor(() => expect(controller.traceSnapshot().filter(
      (event) => event.kind === "webrtc.receiver_sample",
    )).toHaveLength(1));

    now = 1_450;
    deps.peers[0].channel.onmessage?.({
      data: JSON.stringify({ type: "response.output_audio.delta", response_id: "response-media" }),
    } as MessageEvent);
    deps.peers[0].inboundBytesReceived = 512;
    deps.peers[0].inboundPacketsReceived = 2;
    now = 1_500;
    deps.peers[0].channel.onmessage?.({
      data: JSON.stringify({ type: "output_audio_buffer.started", response_id: "response-media" }),
    } as MessageEvent);
    await vi.waitFor(() => expect(controller.traceSnapshot().some(
      (event) => event.kind === "first_playable_media" && event.response_id === "response-media",
    )).toBe(true));

    now = 1_600;
    deps.peers[0].channel.onmessage?.({
      data: JSON.stringify({ type: "response.done", response: { id: "response-media" } }),
    } as MessageEvent);
    const settleIndex = deps.telemetryTimerDelays.lastIndexOf(150);
    expect(settleIndex).toBeGreaterThanOrEqual(0);
    deps.telemetryTimers[settleIndex]();

    const results = controller.traceSnapshot().filter((event) => event.kind === "turn.result");
    expect(results).toHaveLength(1);
    expect(results[0].response_id).toBe("response-media");
    expect(results[0].timing_ms).toMatchObject({
      transcription: 100,
      arbitration: 50,
      generation: 200,
      media_arrival: 200,
      overall: 450,
    });
    expect(JSON.parse(results[0].detail ?? "{}")).toMatchObject({
      turn: 1,
      outcome: "complete",
    });

    const followUpStatsIndex = deps.telemetryTimerDelays.lastIndexOf(100);
    deps.telemetryTimers[followUpStatsIndex]();
    await vi.waitFor(() => expect(controller.traceSnapshot().filter(
      (event) => event.kind === "webrtc.receiver_sample",
    )).toHaveLength(3));
    expect(controller.traceSnapshot().filter((event) => event.kind === "turn.result")).toHaveLength(1);
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
    const failure = controller.traceSnapshot().find(
      (event) => event.kind === "connection.attempt.failed" && event.attempt === 1,
    );
    const success = controller.traceSnapshot().find(
      (event) => event.kind === "connection.attempt.succeeded" && event.attempt === 2,
    );
    expect(JSON.parse(failure?.detail ?? "{}").transport.wake_audio_retained).toBe(true);
    expect(JSON.parse(success?.detail ?? "{}").transport.wake_audio_retained).toBe(false);
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
      entry("backend.provider.user_transcript", 1_640, {
        timing_ms: { provider_boundary_to_transcript: 35 },
      }),
      entry("backend.response.arbiter.started", 1_680, {
        timing_ms: { total_to_provider_submit: 42 },
      }),
      entry("backend.provider.assistant_generating", 1_695, {
        timing_ms: { response_create_sent_to_created: 15 },
      }),
      entry("response.created", 1_700, { response_id: "response-1" }),
      entry("backend.provider.assistant_speaking", 1_880, {
        timing_ms: { response_created_to_first_audio: 180 },
      }),
      entry("response.output_audio.delta", 1_900, { response_id: "response-1" }),
      entry("first_playable_media", 2_000, { response_id: "response-1" }),
    ];

    expect(summarizeJarvisRealtimeTrace(entries)).toEqual({
      schema: "jarvis.realtime_timing.v2",
      connections: [{
        attempt: 1,
        total_ms: 215,
        offer_ms: 50,
        backend_call_ms: 125,
        remote_description_ms: 10,
        wake_detect_to_connected_ms: 220,
        provider_call_ms: 55,
        sideband_connect_ms: 15,
        backend_control_ready_ms: undefined,
        data_channel_ready_ms: undefined,
        wake_detect_to_backend_call_ms: 75,
        wake_detect_to_data_channel_ms: undefined,
      }],
      turns: [{
        turn: 1,
        kind: "wake",
        input_item_id: undefined,
        boundary_monotonic_ms: 1_600,
        response_id: "response-1",
        local_speech_stop_to_provider_stop_ms: 1_200,
        wake_handoff_tail_ms: 1_050,
        provider_stop_to_response_created_ms: 100,
        response_created_to_first_audio_ms: 200,
        provider_stop_to_first_playable_media_ms: 400,
        local_speech_stop_to_first_playable_media_ms: 1_600,
        backend_arbiter_ms: 42,
        provider_boundary_to_transcript_ms: 35,
        transcript_to_arbiter_ms: 40,
        arbiter_to_response_created_ms: 20,
        response_create_sent_to_created_ms: 15,
        response_created_to_first_audio_provider_ms: 180,
        first_audio_to_first_playable_media_ms: 100,
        response_created_to_first_playable_media_ms: 300,
        wake_audio_upload_ms: undefined,
        wake_commit_ack_ms: undefined,
        local_speech_stop_to_commit_ms: undefined,
        provider_connection_ms: undefined,
        transcription_ms: 40,
        response_arbitration_ms: 40,
        generation_ms: 220,
        outcome: "complete",
        bottleneck: "endpointing",
        bottleneck_ms: 1_050,
      }],
    });
  });

  it("attributes a bulk-wake latency outlier to its slowest stage", () => {
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
    const summary = summarizeJarvisRealtimeTrace([
      entry("wake_detected", 0),
      entry("realtime_start_requested", 5),
      entry("realtime_connection_attempt_started", 10, { attempt: 1 }),
      entry("realtime_backend_call_started", 20),
      entry("realtime_backend_call_completed", 500),
      entry("realtime_remote_description_set", 520),
      entry("backend.provider.ready", 700, { timing_ms: { provider_call: 480, sideband_connect: 670 } }),
      entry("realtime_backend_control_ready", 710),
      entry("realtime_data_channel_open", 715),
      entry("wake_local_speech_stopped", 100),
      entry("wake_utterance_ready", 700),
      entry("wake_audio_upload_started", 720),
      entry("wake_audio_upload_completed", 735),
      entry("wake_audio_commit_sent", 736),
      entry("input_audio_buffer.committed", 780, { item_id: "wake-item" }),
      entry("backend.provider.user_transcript", 820, {
        timing_ms: { provider_boundary_to_transcript: 35 },
      }),
      entry("backend.response.arbiter.started", 850, { timing_ms: { total_to_provider_submit: 20 } }),
      entry("response.created", 870, { response_id: "response-1" }),
      entry("response.output_audio.delta", 1_050, { response_id: "response-1" }),
      entry("first_playable_media", 1_100, { response_id: "response-1" }),
      entry("realtime_connection_ready", 1_110),
    ]);

    expect(summary.connections[0]).toMatchObject({
      backend_control_ready_ms: 690,
      data_channel_ready_ms: 195,
      wake_detect_to_backend_call_ms: 20,
      wake_detect_to_data_channel_ms: 715,
    });
    expect(summary.turns[0]).toMatchObject({
      kind: "wake",
      wake_audio_upload_ms: 15,
      wake_commit_ack_ms: 44,
      local_speech_stop_to_commit_ms: 636,
      outcome: "complete",
      bottleneck: "provider_connection",
      bottleneck_ms: 695,
    });
  });

  it("labels a transcribed turn that never reached the response arbiter", () => {
    const entry = (kind: string, monotonic_ms: number): JarvisRealtimeTraceEntry => ({
      kind,
      monotonic_ms,
      wall_time: "2026-08-28T00:00:00.000Z",
    });

    const summary = summarizeJarvisRealtimeTrace([
      entry("wake_detected", 0),
      entry("wake_local_speech_stopped", 500),
      entry("input_audio_buffer.speech_stopped", 700),
      entry("backend.provider.user_transcript", 900),
    ]);

    expect(summary.turns).toHaveLength(1);
    expect(summary.turns[0]).toMatchObject({
      kind: "wake",
      outcome: "no_arbiter",
      provider_stop_to_response_created_ms: undefined,
    });
  });

  it("reports audio that never became playable explicitly", () => {
    const entry = (kind: string, monotonic_ms: number): JarvisRealtimeTraceEntry => ({
      kind,
      monotonic_ms,
      wall_time: "2026-08-28T00:00:00.000Z",
    });
    const summary = summarizeJarvisRealtimeTrace([
      entry("input_audio_buffer.speech_stopped", 100),
      entry("backend.provider.user_transcript", 150),
      entry("backend.response.arbiter.started", 180),
      { ...entry("response.created", 200), response_id: "response-no-media" },
      { ...entry("response.output_audio.delta", 250), response_id: "response-no-media" },
    ]);

    expect(summary.turns[0]).toMatchObject({
      response_id: "response-no-media",
      outcome: "no_playable_media",
    });
  });

  it("estimates backend clock offset and bounds cross-source timing uncertainty", () => {
    expect(estimateRealtimeClockAlignment(
      Date.parse("2026-08-28T00:00:00.000Z"),
      Date.parse("2026-08-28T00:05:00.040Z"),
      Date.parse("2026-08-28T00:05:00.060Z"),
      Date.parse("2026-08-28T00:00:00.120Z"),
    )).toEqual({
      offsetMs: 299_990,
      uncertaintyMs: 50,
      networkRttMs: 100,
    });
  });
});
