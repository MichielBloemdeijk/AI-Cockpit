import { describe, expect, it, vi } from "vitest";

import type { JarvisRealtimeCallResponse, JarvisSession } from "./api";
import {
  JarvisRealtimeController,
  type JarvisRealtimeDependencies,
  type JarvisRealtimeSnapshot,
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
  send(message: string) { this.sent.push(message); }
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

function dependencies(overrides: Partial<JarvisRealtimeDependencies> = {}) {
  const peers: FakePeer[] = [];
  const streams: FakeStream[] = [];
  const timers: Array<() => void> = [];
  const createCall = vi.fn().mockResolvedValue(response(1));
  const closeSession = vi.fn().mockResolvedValue(undefined);
  const wait = vi.fn().mockResolvedValue(undefined);
  const detector = { destroy: vi.fn() };
  const handoffOutputs: Array<{
    stream: FakeStream;
    start: ReturnType<typeof vi.fn>;
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
    createBargeInDetector: vi.fn(async (_stream, onSpeechStart) => {
      speechStart = onSpeechStart;
      return detector;
    }),
    createHandoffOutput: vi.fn(async () => {
      const output = {
        stream: new FakeStream(),
        start: vi.fn().mockResolvedValue(undefined),
        destroy: vi.fn().mockResolvedValue(undefined),
      };
      handoffOutputs.push(output);
      return output as unknown as Awaited<ReturnType<JarvisRealtimeDependencies["createHandoffOutput"]>>;
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
    handoffOutputs,
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
    expect(deps.createCall.mock.calls[0][5]).toEqual({ source: "wake_word", wake_score: 0.22 });
    await controller.stop();
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

  it("mutes and clears provider output immediately when local VAD detects barge-in", async () => {
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
    expect(deps.peers[0].channel.sent).toHaveLength(1);
    expect(JSON.parse(deps.peers[0].channel.sent[0])).toMatchObject({
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

  it("uses provider speech-start as the mute fallback when local VAD has not fired", async () => {
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
    expect(deps.peers[0].channel.sent).toEqual([]);
    await controller.stop();
  });

  it("replays a wake utterance before live audio without taking ownership of the wake mic", async () => {
    const deps = dependencies();
    const wakeStream = new FakeStream();
    const controller = new JarvisRealtimeController({ controllerId: "browser-1" }, deps.value);

    await controller.start({
      audioHandoff: {
        localStream: wakeStream as unknown as MediaStream,
        utterance: Promise.resolve(new Float32Array([0.1, 0.2])),
        sampleRate: 16_000,
      },
    });

    expect(deps.streams).toHaveLength(0);
    expect(deps.handoffOutputs).toHaveLength(1);
    expect(deps.handoffOutputs[0].start).toHaveBeenCalledOnce();
    expect(deps.peers[0].addedTracks).toBe(1);
    await controller.stop();
    expect(wakeStream.track.stopped).toBe(false);
    expect(deps.handoffOutputs[0].destroy).toHaveBeenCalledOnce();
  });
});
