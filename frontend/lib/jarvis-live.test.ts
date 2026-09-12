import { describe, expect, it, vi } from "vitest";

import type { JarvisRealtimeCallResponse } from "./api";
import { JarvisLiveController } from "./jarvis-live";
import type { JarvisAudioChunk, JarvisCandidateAudio } from "./jarvis-activation-audio";

function fakeCall(): JarvisRealtimeCallResponse {
  return {
    role: "controller",
    session: { id: "session-1", generation: 1 } as JarvisRealtimeCallResponse["session"],
    sdp_answer: "answer",
    rotate_after_seconds: null,
    audio_policy: null,
    provider_capabilities: { continuous_conversation: true },
    server_received_at: new Date().toISOString(),
    server_responded_at: new Date().toISOString(),
  };
}

function fakeMediaStream(): MediaStream {
  const track = { stop: vi.fn() } as unknown as MediaStreamTrack;
  return {
    getAudioTracks: () => [track],
    getTracks: () => [track],
  } as unknown as MediaStream;
}

function fakePc() {
  const channel = {
    readyState: "open",
    send: vi.fn(),
    close: vi.fn(),
    onmessage: null as ((event: MessageEvent) => void) | null,
    onerror: null as (() => void) | null,
  } as unknown as RTCDataChannel;
  const pc = {
    iceGatheringState: "complete",
    localDescription: null,
    createDataChannel: vi.fn(() => channel),
    createOffer: vi.fn(async () => ({ type: "offer", sdp: "offer" } as RTCSessionDescriptionInit)),
    setLocalDescription: vi.fn(async () => undefined),
    setRemoteDescription: vi.fn(async () => undefined),
    addTrack: vi.fn(),
    close: vi.fn(),
    ontrack: null,
  } as unknown as RTCPeerConnection;
  return { channel, pc };
}

function fakeAudioContext() {
  const starts: number[] = [];
  const buffers: number[][] = [];
  const destination = { stream: fakeMediaStream() };
  const context = {
    currentTime: 1,
    state: "running",
    createMediaStreamDestination: () => destination,
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
  it("negotiates oai-events before the offer and waits for session.started", async () => {
    const { pc, channel } = fakePc();
    const createCall = vi.fn(async () => fakeCall());
    const controller = new JarvisLiveController("c", {}, {
      peerConnection: () => pc,
      getUserMedia: async () => fakeMediaStream(),
      waitForBackendReady: async () => undefined,
      createCall,
    });
    const pending = controller.start();
    await vi.waitFor(() => expect(pc.createOffer).toHaveBeenCalled());
    expect(pc.createDataChannel).toHaveBeenCalledWith("oai-events");
    expect(controller.current().status).toBe("connecting");
    channel.onmessage?.({ data: JSON.stringify({ type: "session.started" }) } as MessageEvent);
    await pending;
    expect(controller.current().status).toBe("connected");
    expect(createCall).toHaveBeenCalledWith("c", "controller", "openai_live", "offer", undefined);
  });

  it("does not send raw audio or Realtime cancellation events", async () => {
    const { pc, channel } = fakePc();
    const controller = new JarvisLiveController("c", {}, {
      peerConnection: () => pc,
      getUserMedia: async () => fakeMediaStream(),
      waitForBackendReady: async () => undefined,
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
      waitForBackendReady: async () => undefined,
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
      waitForBackendReady: async () => undefined,
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
      waitForBackendReady: async () => undefined,
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
      waitForBackendReady: async () => undefined,
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
